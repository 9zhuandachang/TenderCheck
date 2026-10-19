"""
Deep Pretrained Language Model Classifier for Chinese Tender Requirements.

Fine-tunes `hfl/chinese-roberta-wwm-ext` (or any HuggingFace AutoModelForSequenceClassification
checkpoint) on the 8-class tender requirement taxonomy. Incorporates class-balanced
cross-entropy loss, linear warmup scheduling, automatic mixed precision (AMP),
and validation Macro-F1 early stopping.
"""

import copy
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

import numpy as np
import torch
import torch.nn as nn
from torch.optim import AdamW
from torch.utils.data import DataLoader, Dataset
from tqdm import tqdm
from transformers import (
    AutoConfig,
    AutoModelForSequenceClassification,
    AutoTokenizer,
    get_linear_schedule_with_warmup,
)

from ..data.schema import ID2LABEL, LABEL2ID, NUM_LABELS, RequirementSample
from ..evaluation.evaluator import evaluate_predictions
from .base import BaseRequirementClassifier

logger = logging.getLogger(__name__)

DEFAULT_PRETRAINED_MODEL = "hfl/chinese-roberta-wwm-ext"


class _TenderTextDataset(Dataset):
    """Internal PyTorch Dataset wrapping pre-tokenized requirement texts and labels."""

    def __init__(
        self,
        texts: Sequence[str],
        labels: Optional[Sequence[int]],
        tokenizer,
        max_length: int = 160,
    ) -> None:
        self.encodings = tokenizer(
            list(texts),
            truncation=True,
            max_length=max_length,
            padding="max_length",
            return_tensors="pt",
        )
        self.labels = (
            torch.tensor(list(labels), dtype=torch.long) if labels is not None else None
        )

    def __len__(self) -> int:
        return self.encodings["input_ids"].size(0)

    def __getitem__(self, idx: int) -> Dict[str, torch.Tensor]:
        item = {
            "input_ids": self.encodings["input_ids"][idx],
            "attention_mask": self.encodings["attention_mask"][idx],
        }
        if "token_type_ids" in self.encodings:
            item["token_type_ids"] = self.encodings["token_type_ids"][idx]
        if self.labels is not None:
            item["labels"] = self.labels[idx]
        return item


class BertRequirementClassifier(BaseRequirementClassifier):
    """
    Fine-tuned Chinese RoBERTa / BERT sequence classifier for 8 requirement categories.
    """

    def __init__(
        self,
        pretrained_model_name_or_path: str = DEFAULT_PRETRAINED_MODEL,
        use_section_context: bool = True,
        max_length: int = 256,
        batch_size: int = 32,
        learning_rate: float = 2e-5,
        weight_decay: float = 0.01,
        num_epochs: int = 5,
        warmup_ratio: float = 0.1,
        use_class_weights: bool = True,
        patience: int = 2,
        fp16: bool = True,
        device: Optional[str] = None,
        random_state: int = 42,
    ) -> None:
        """
        Args:
            pretrained_model_name_or_path: HuggingFace model ID or local path.
            use_section_context: Prepend `[Section] <section> [SEP]` to requirement text.
            max_length: Maximum token sequence length.
            batch_size: Mini-batch size for training and evaluation.
            learning_rate: Peak learning rate for AdamW optimizer.
            weight_decay: L2 weight decay coefficient.
            num_epochs: Maximum number of fine-tuning epochs.
            warmup_ratio: Fraction of total training steps used for linear LR warmup.
            use_class_weights: Use smoothed inverse-frequency class weights in CrossEntropyLoss.
            patience: Early stopping patience based on validation Macro-F1.
            fp16: Enable mixed-precision training when running on a CUDA GPU.
            device: `"cuda"` or `"cpu"`. Auto-detected if `None`.
            random_state: Random seed for reproducible initialization.
        """
        super().__init__(use_section_context=use_section_context)
        self.pretrained_model_name_or_path = pretrained_model_name_or_path
        self.max_length = max_length
        self.batch_size = batch_size
        self.learning_rate = learning_rate
        self.weight_decay = weight_decay
        self.num_epochs = num_epochs
        self.warmup_ratio = warmup_ratio
        self.use_class_weights = use_class_weights
        self.patience = patience
        self.random_state = random_state

        if device is None:
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)

        self.fp16 = bool(fp16 and self.device.type == "cuda")
        self.tokenizer = None
        self.model = None

    def _set_seed(self) -> None:
        torch.manual_seed(self.random_state)
        np.random.seed(self.random_state)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(self.random_state)

    def _init_model_and_tokenizer(self) -> None:
        """Initialize HuggingFace tokenizer and 8-class classification head."""
        if self.tokenizer is None:
            self.tokenizer = AutoTokenizer.from_pretrained(self.pretrained_model_name_or_path)
        if self.model is None:
            config = AutoConfig.from_pretrained(
                self.pretrained_model_name_or_path,
                num_labels=NUM_LABELS,
                label2id=LABEL2ID,
                id2label=ID2LABEL,
            )
            self.model = AutoModelForSequenceClassification.from_pretrained(
                self.pretrained_model_name_or_path,
                config=config,
            )
            self.model.to(self.device)

    @staticmethod
    def _compute_class_weights(labels: Sequence[int]) -> torch.Tensor:
        """
        Compute square-root smoothed inverse class frequency weights so rare
        categories (e.g., Schedule, Integrity) receive stronger gradient signal
        without destabilizing training.
        """
        counts = np.bincount(labels, minlength=NUM_LABELS).astype(np.float64)
        counts = np.maximum(counts, 1.0)
        inv_freq = np.sqrt(len(labels) / (NUM_LABELS * counts))
        inv_freq = inv_freq / inv_freq.mean()
        return torch.tensor(inv_freq, dtype=torch.float32)

    def fit(
        self,
        train_samples: Sequence[RequirementSample],
        val_samples: Optional[Sequence[RequirementSample]] = None,
    ) -> Dict[str, object]:
        """
        Fine-tune the pretrained Transformer on `train_samples` and track best
        validation Macro-F1 checkpoint.
        """
        if not train_samples:
            raise ValueError("Training sample list cannot be empty.")

        self._set_seed()
        self._init_model_and_tokenizer()
        assert self.model is not None and self.tokenizer is not None

        train_texts = self._prepare_texts(train_samples)
        train_labels = [s.label_id for s in train_samples]
        train_dataset = _TenderTextDataset(
            texts=train_texts,
            labels=train_labels,
            tokenizer=self.tokenizer,
            max_length=self.max_length,
        )
        train_loader = DataLoader(
            train_dataset,
            batch_size=self.batch_size,
            shuffle=True,
        )

        if self.use_class_weights:
            class_weights = self._compute_class_weights(train_labels).to(self.device)
            criterion = nn.CrossEntropyLoss(weight=class_weights)
        else:
            criterion = nn.CrossEntropyLoss()

        optimizer = AdamW(
            self.model.parameters(),
            lr=self.learning_rate,
            weight_decay=self.weight_decay,
        )
        total_steps = len(train_loader) * self.num_epochs
        warmup_steps = int(total_steps * self.warmup_ratio)
        scheduler = get_linear_schedule_with_warmup(
            optimizer,
            num_warmup_steps=warmup_steps,
            num_training_steps=total_steps,
        )
        scaler = torch.amp.GradScaler("cuda", enabled=self.fp16)

        best_val_f1 = -1.0
        best_state_dict = None
        epochs_no_improve = 0
        history: List[Dict[str, object]] = []

        logger.info(
            "Starting RoBERTa fine-tuning on %s | Samples=%d | Epochs=%d | BatchSize=%d | FP16=%s",
            self.device,
            len(train_samples),
            self.num_epochs,
            self.batch_size,
            self.fp16,
        )

        for epoch in range(1, self.num_epochs + 1):
            self.model.train()
            running_loss = 0.0

            progress_bar = tqdm(
                train_loader,
                desc=f"Epoch {epoch}/{self.num_epochs}",
                leave=False,
            )
            for batch in progress_bar:
                optimizer.zero_grad()
                input_ids = batch["input_ids"].to(self.device)
                attention_mask = batch["attention_mask"].to(self.device)
                labels = batch["labels"].to(self.device)
                model_kwargs = {
                    "input_ids": input_ids,
                    "attention_mask": attention_mask,
                }
                if "token_type_ids" in batch:
                    model_kwargs["token_type_ids"] = batch["token_type_ids"].to(self.device)

                with torch.amp.autocast("cuda", enabled=self.fp16):
                    outputs = self.model(**model_kwargs)
                    loss = criterion(outputs.logits, labels)

                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
                scaler.step(optimizer)
                scaler.update()
                scheduler.step()

                running_loss += float(loss.item())
                progress_bar.set_postfix(loss=f"{loss.item():.4f}")

            avg_train_loss = running_loss / max(len(train_loader), 1)
            epoch_log: Dict[str, object] = {
                "epoch": epoch,
                "train_loss": round(avg_train_loss, 4),
            }

            if val_samples:
                val_preds = self.predict_ids(val_samples)
                val_true = [s.label_id for s in val_samples]
                val_metrics = evaluate_predictions(
                    y_true=val_true,
                    y_pred=val_preds,
                    split_name="val",
                    model_name="roberta_wwm_ext",
                )
                val_f1 = float(val_metrics["macro_f1"])
                epoch_log["val_accuracy"] = val_metrics["accuracy"]
                epoch_log["val_macro_f1"] = val_f1
                epoch_log["val_weighted_f1"] = val_metrics["weighted_f1"]

                logger.info(
                    "Epoch %d/%d | Train Loss: %.4f | Val Acc: %.4f | Val Macro-F1: %.4f",
                    epoch,
                    self.num_epochs,
                    avg_train_loss,
                    val_metrics["accuracy"],
                    val_f1,
                )

                if val_f1 > best_val_f1:
                    best_val_f1 = val_f1
                    best_state_dict = copy.deepcopy(self.model.state_dict())
                    epochs_no_improve = 0
                else:
                    epochs_no_improve += 1
                    if epochs_no_improve >= self.patience:
                        logger.info(
                            "Early stopping triggered at epoch %d (best Val Macro-F1=%.4f).",
                            epoch,
                            best_val_f1,
                        )
                        history.append(epoch_log)
                        break

            history.append(epoch_log)

        if best_state_dict is not None:
            self.model.load_state_dict(best_state_dict)

        return {"history": history, "best_val_macro_f1": round(best_val_f1, 4)}

    def predict_proba(
        self,
        inputs: Sequence[Union[RequirementSample, str, Dict[str, str]]],
    ) -> np.ndarray:
        """
        Compute `(N, 8)` softmax probability distribution on `inputs`.
        """
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("RoBERTa model has not been fitted or loaded yet.")

        self.model.eval()
        texts = self._prepare_texts(inputs)
        dataset = _TenderTextDataset(
            texts=texts,
            labels=None,
            tokenizer=self.tokenizer,
            max_length=self.max_length,
        )
        loader = DataLoader(dataset, batch_size=self.batch_size, shuffle=False)

        all_probs: List[np.ndarray] = []
        with torch.no_grad():
            for batch in loader:
                model_kwargs = {
                    "input_ids": batch["input_ids"].to(self.device),
                    "attention_mask": batch["attention_mask"].to(self.device),
                }
                if "token_type_ids" in batch:
                    model_kwargs["token_type_ids"] = batch["token_type_ids"].to(self.device)

                outputs = self.model(**model_kwargs)
                probs = torch.softmax(outputs.logits, dim=-1).cpu().numpy()
                all_probs.append(probs)

        return np.vstack(all_probs) if all_probs else np.empty((0, NUM_LABELS))

    def save(self, model_dir: Union[str, Path]) -> Path:
        """
        Save fine-tuned HuggingFace model, tokenizer, and training metadata to `model_dir`.
        """
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Cannot save an uninitialized BertRequirementClassifier.")

        model_dir = Path(model_dir)
        model_dir.mkdir(parents=True, exist_ok=True)

        self.model.save_pretrained(model_dir)
        self.tokenizer.save_pretrained(model_dir)

        meta = {
            "model_type": "roberta",
            "use_section_context": self.use_section_context,
            "max_length": self.max_length,
            "batch_size": self.batch_size,
            "learning_rate": self.learning_rate,
            "weight_decay": self.weight_decay,
            "num_epochs": self.num_epochs,
            "warmup_ratio": self.warmup_ratio,
            "use_class_weights": self.use_class_weights,
            "patience": self.patience,
            "random_state": self.random_state,
        }
        with open(model_dir / "classifier_meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        logger.info("Saved BertRequirementClassifier to %s", model_dir)
        return model_dir

    @classmethod
    def load(
        cls,
        model_dir: Union[str, Path],
        device: Optional[str] = None,
    ) -> "BertRequirementClassifier":
        """
        Load a fine-tuned `BertRequirementClassifier` checkpoint from `model_dir`.
        """
        model_dir = Path(model_dir)
        meta_path = model_dir / "classifier_meta.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"Missing classifier_meta.json in {model_dir}")

        safetensors_path = model_dir / "model.safetensors"
        if not safetensors_path.exists():
            parts = sorted(model_dir.glob("model.safetensors.part*"))
            if parts:
                logger.info("Reassembling %d split parts into %s...", len(parts), safetensors_path)
                with open(safetensors_path, "wb") as out_f:
                    for part in parts:
                        out_f.write(part.read_bytes())

        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        instance = cls(
            pretrained_model_name_or_path=str(model_dir),
            use_section_context=bool(meta.get("use_section_context", True)),
            max_length=int(meta.get("max_length", 256)),
            batch_size=int(meta.get("batch_size", 32)),
            learning_rate=float(meta.get("learning_rate", 2e-5)),
            weight_decay=float(meta.get("weight_decay", 0.01)),
            num_epochs=int(meta.get("num_epochs", 5)),
            warmup_ratio=float(meta.get("warmup_ratio", 0.1)),
            use_class_weights=bool(meta.get("use_class_weights", True)),
            patience=int(meta.get("patience", 2)),
            device=device,
            random_state=int(meta.get("random_state", 42)),
        )
        instance.tokenizer = AutoTokenizer.from_pretrained(model_dir)
        instance.model = AutoModelForSequenceClassification.from_pretrained(model_dir)
        instance.model.to(instance.device)
        instance.model.eval()
        return instance
