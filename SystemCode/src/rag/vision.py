"""
Unified Vision & OCR Protocol Layer for TenderCheck Bid Document Parsing.

Provides a standardized `PageVisionResult` protocol for scanned pages, certificates,
financial tables, and stamped commitment letters:
1. `QwenVLBackend`: Runs `Qwen/Qwen2.5-VL-7B-Instruct` on an RTX 5080 (16GB VRAM)
   via local HuggingFace Transformers or an OpenAI-compatible local server (vLLM/Ollama).
2. `LocalHeuristicOCRBackend`: Zero-GPU fallback combining RapidOCR (if installed)
   with PyMuPDF pixmap HSV red-seal detection and text layout heuristics.
3. `DiskCachedVisionBackend`: Transparent SHA-256 disk cache wrapper so heavy VLM
   inference on the RTX 5080 is executed once and shared across all teammates.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import pymupdf as fitz
except ImportError:  # Older supported PyMuPDF installations.
    import fitz
import numpy as np

logger = logging.getLogger(__name__)

# Standard document categories for bid attachments and chapters
DOC_CATEGORIES: Tuple[str, ...] = (
    "投标函与承诺书",
    "法定代表人身份证明与授权委托书",
    "联合体协议书",
    "投标保证金证明",
    "营业执照与企业资质证书",
    "安全生产许可证",
    "项目管理机构与人员简历证书",
    "社保证明材料",
    "近年类似工程业绩证明",
    "财务状况与审计报告",
    "企业信誉与无违法承诺",
    "报价文件与工程量清单",
    "施工组织设计与技术方案",
    "其他投标材料",
)

VLM_SYSTEM_PROMPT = """你是一个专业的中国建设工程招投标文件视觉审查助手。
请仔细观察输入的投标文件页面图像，并严格输出如下 JSON 格式（不要输出任何多余解释）：
{
  "doc_category": "从[投标函与承诺书, 法定代表人身份证明与授权委托书, 联合体协议书, 投标保证金证明, 营业执照与企业资质证书, 安全生产许可证, 项目管理机构与人员简历证书, 社保证明材料, 近年类似工程业绩证明, 财务状况与审计报告, 企业信誉与无违法承诺, 报价文件与工程量清单, 施工组织设计与技术方案, 其他投标材料]中选一",
  "ocr_text": "完整提取页面中的所有文字；若是表格请还原为Markdown表格格式",
  "has_official_seal": true或false（页面中是否盖有单位红色公章或电子印章）,
  "seal_company_name": "公章上的企业名称文字，若无公章或无法辨认填null",
  "has_signature": true或false（是否有法定代表人或委托代理人签字/私章）,
  "extracted_kv": {}
}
extracted_kv固定为空对象；业务事实留给后续基于证据的提取模块。"""


@dataclass
class PageVisionResult:
    """
    Unified output protocol returned by any OCR or VLM backend for a single PDF page.
    """

    pdf_page: int
    doc_category: str = "其他投标材料"
    ocr_text: str = ""
    has_official_seal: bool = False
    seal_company_name: Optional[str] = None
    has_signature: bool = False
    extracted_kv: Dict[str, Any] = field(default_factory=dict)
    backend_name: str = "heuristic"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PageVisionResult":
        return cls(
            pdf_page=int(data.get("pdf_page", 1)),
            doc_category=str(data.get("doc_category", "其他投标材料")),
            ocr_text=str(data.get("ocr_text", "")),
            has_official_seal=bool(data.get("has_official_seal", False)),
            seal_company_name=data.get("seal_company_name"),
            has_signature=bool(data.get("has_signature", False)),
            extracted_kv=dict(data.get("extracted_kv") or {}),
            backend_name=str(data.get("backend_name", "cached")),
        )


def detect_red_seal_from_pixmap(pix: fitz.Pixmap, min_red_ratio: float = 0.0012) -> bool:
    """
    Detect presence of a red official stamp/seal on a rendered PDF page using RGB dominance.

    Official Chinese corporate seals (公章 / 项目章 / 法人章) exhibit strong red-channel
    dominance (`R > 150`, `R - G > 45`, `R - B > 45`).

    Args:
        pix: PyMuPDF RGB Pixmap of the page.
        min_red_ratio: Minimum fraction of red-stamp pixels required to flag `True`.

    Returns:
        `True` if a red stamp pattern is detected on the page.
    """
    if pix.n < 3:
        return False
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    r = arr[:, :, 0].astype(np.int16)
    g = arr[:, :, 1].astype(np.int16)
    b = arr[:, :, 2].astype(np.int16)

    red_mask = (r > 155) & ((r - g) > 45) & ((r - b) > 45)
    red_ratio = float(np.count_nonzero(red_mask)) / max(arr.shape[0] * arr.shape[1], 1)
    return red_ratio >= min_red_ratio


def infer_doc_category_from_text(text: str, heading_hint: str = "") -> str:
    """Infer standardized `doc_category` from page text and section headings."""
    combined = f"{heading_hint}\n{text}"
    rules: List[Tuple[str, Tuple[str, ...]]] = [
        ("投标函与承诺书", ("投标函", "投标函附录", "承诺函", "我方承诺", "投标承诺书", "投标诚信承诺书")),
        ("法定代表人身份证明与授权委托书", ("法定代表人身份证明", "授权委托书", "委托代理人")),
        ("联合体协议书", ("联合体协议书", "联合体投标")),
        ("投标保证金证明", ("投标保证金", "银行保函", "保证保险", "基本账户开户许可证", "转账凭证")),
        ("安全生产许可证", ("安全生产许可证", "安许证")),
        ("营业执照与企业资质证书", ("营业执照", "统一社会信用代码", "建筑业企业资质证书", "资质等级", "基本情况表")),
        ("社保证明材料", ("社会保险", "社保证明", "参保缴费", "养老保险")),
        ("项目管理机构与人员简历证书", ("项目经理", "技术负责人", "其他管理和技术人员", "建造师", "安全生产考核合格证", "职称证", "简历表", "项目管理机构")),
        ("近年类似工程业绩证明", ("类似项目情况表", "类似工程业绩", "中标通知书", "施工合同", "合同协议书", "竣工验收")),
        ("财务状况与审计报告", ("财务状况", "审计报告", "资产负债表", "利润表", "现金流量表")),
        ("企业信誉与无违法承诺", ("信誉情况表", "无重大违法", "无行贿犯罪", "信用中国", "不良行为记录")),
        ("报价文件与工程量清单", ("已标价工程量清单", "工程量清单", "投标报价汇总表", "投标总价", "开标一览表", "计价表", "单价分析表", "汇总表")),
        ("施工组织设计与技术方案", ("施工组织设计", "施工方案", "施工进度计划", "质量保证措施", "安全文明施工")),
    ]
    for category, keywords in rules:
        if any(kw in combined for kw in keywords):
            return category
    return "其他投标材料"


class BaseVisionBackend(ABC):
    """Abstract base class for page-level OCR and VLM analysis."""

    @abstractmethod
    def analyze_page(
        self,
        doc: fitz.Document,
        pdf_page: int,
        existing_text: str = "",
        heading_hint: str = "",
    ) -> PageVisionResult:
        """Analyze a single 1-indexed PDF page and return a `PageVisionResult`."""


class LocalHeuristicOCRBackend(BaseVisionBackend):
    """
    Local CPU/GPU fallback vision backend:
    - Uses `RapidOCR` (if installed) to extract text from scanned page images.
    - Uses HSV/RGB red dominance to detect official corporate stamps (`has_official_seal`).
    - Preserves transcription and layout metadata; business extraction runs downstream.
    """

    def __init__(self, dpi: int = 150) -> None:
        self.dpi = dpi
        self._rapid_ocr = None
        self._rapid_ocr_checked = False

    def _get_ocr_engine(self):
        if not self._rapid_ocr_checked:
            self._rapid_ocr_checked = True
            try:
                from rapidocr_onnxruntime import RapidOCR  # type: ignore

                self._rapid_ocr = RapidOCR()
                logger.info("Initialized RapidOCR (ONNX Runtime) for scanned page recognition.")
            except ImportError:
                logger.info(
                    "rapidocr-onnxruntime not installed; scanned pages will rely on "
                    "embedded text, sub-chapter source files, or VLM cache."
                )
        return self._rapid_ocr

    def analyze_page(
        self,
        doc: fitz.Document,
        pdf_page: int,
        existing_text: str = "",
        heading_hint: str = "",
    ) -> PageVisionResult:
        page = doc[pdf_page - 1]
        pix = page.get_pixmap(dpi=self.dpi, alpha=False)
        has_seal = detect_red_seal_from_pixmap(pix)

        ocr_text = existing_text.strip()
        ocr_engine = self._get_ocr_engine()
        if ocr_engine is not None and len(re.sub(r"\s+", "", ocr_text)) < 60:
            img_bytes = pix.tobytes("png")
            result, _ = ocr_engine(img_bytes)
            if result:
                lines = [str(item[1]).strip() for item in result if len(item) >= 2]
                ocr_text = "\n".join(lines)

        doc_cat = infer_doc_category_from_text(ocr_text, heading_hint=heading_hint)
        has_sig = any(kw in ocr_text for kw in ("法定代表人", "委托代理人", "签字", "签名", "签章"))
        extracted_kv = {}

        return PageVisionResult(
            pdf_page=pdf_page,
            doc_category=doc_cat,
            ocr_text=ocr_text,
            has_official_seal=has_seal,
            seal_company_name=extracted_kv.get("company_name"),
            has_signature=has_sig,
            extracted_kv=extracted_kv,
            backend_name="local_rapidocr_heuristic" if ocr_engine else "local_heuristic",
        )


class QwenVLBackend(BaseVisionBackend):
    """
    Production Vision-Language Model backend powered by `Qwen/Qwen2.5-VL-7B-Instruct`.

    Designed to run on the teammate's RTX 5080 (16GB VRAM) in two supported modes:
    1. `mode="openai_compatible"`: Connects to a local vLLM / Ollama / LMStudio server
       or cloud DashScope endpoint speaking the OpenAI Chat Completions Vision API.
    2. `mode="transformers"`: Loads `Qwen2.5-VL-7B-Instruct` directly in PyTorch FP16/BF16.
    """

    def __init__(
        self,
        model_name_or_path: str = "Qwen/Qwen2.5-VL-7B-Instruct",
        mode: str = "transformers",
        api_base_url: str = "http://localhost:8000/v1",
        api_key: str = "EMPTY",
        dpi: int = 160,
        quantization: str = "none",
    ) -> None:
        self.model_name_or_path = model_name_or_path
        self.mode = mode
        self.api_base_url = api_base_url
        self.api_key = api_key
        self.dpi = dpi
        if quantization not in {"none", "int8", "nf4"}:
            raise ValueError(f"Unsupported Qwen quantization: {quantization}")
        self.quantization = quantization
        self._hf_model = None
        self._hf_processor = None

    def _ensure_hf_loaded(self) -> None:
        if self._hf_model is not None:
            return
        import torch
        import transformers
        from transformers import AutoProcessor

        logger.info("Loading VLM %s on GPU...", self.model_name_or_path)
        # Limit max image pixels so high-DPI A4 scans don't cause excessive visual token memory
        self._hf_processor = AutoProcessor.from_pretrained(
            self.model_name_or_path,
            min_pixels=256 * 28 * 28,
            max_pixels=1280 * 28 * 28,
        )
        model_cls = getattr(
            transformers,
            "Qwen2_5_VLForConditionalGeneration",
            getattr(transformers, "AutoModelForImageTextToText", None),
        )
        load_kwargs: Dict[str, Any] = {
            "torch_dtype": torch.bfloat16 if torch.cuda.is_available() else torch.float32,
            "device_map": "auto",
        }
        if self.quantization != "none":
            from transformers import BitsAndBytesConfig

            if not torch.cuda.is_available():
                raise RuntimeError("Quantized Qwen loading requires a CUDA GPU in this pipeline.")
            if self.quantization == "int8":
                quantization_config = BitsAndBytesConfig(
                    load_in_8bit=True,
                    llm_int8_skip_modules=["visual", "lm_head"],
                )
            else:
                quantization_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.bfloat16,
                    bnb_4bit_use_double_quant=True,
                    llm_int8_skip_modules=["visual", "lm_head"],
                )
            load_kwargs["quantization_config"] = quantization_config
            logger.info("Loading Qwen language layers in %s; vision encoder remains BF16.", self.quantization)
        self._hf_model = model_cls.from_pretrained(self.model_name_or_path, **load_kwargs)
        self._hf_model.eval()

    def unload(self) -> None:
        """Release VLM weights from GPU VRAM so subsequent BGE-M3 indexing has full 16GB VRAM."""
        if self._hf_model is not None:
            import gc
            import torch

            del self._hf_model
            del self._hf_processor
            self._hf_model = None
            self._hf_processor = None
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            logger.info("Unloaded VLM from GPU VRAM.")

    @staticmethod
    def normalize_recovery_category(category: Any, source_heading: str = "") -> Any:
        """Map explicit document subtypes to the existing coarse taxonomy only."""
        aliases = {
            "中标通知书": "近年类似工程业绩证明",
            "合同协议书": "近年类似工程业绩证明",
            "公路工程交工验收证书": "近年类似工程业绩证明",
            "交工验收证书": "近年类似工程业绩证明",
            "竣工验收报告": "近年类似工程业绩证明",
            "建造师注册证书": "项目管理机构与人员简历证书",
            "一级建造师注册证书": "项目管理机构与人员简历证书",
            "中华人民共和国一级建造师注册证书": "项目管理机构与人员简历证书",
            "专业技术资格证书": "项目管理机构与人员简历证书",
            "职称证书": "项目管理机构与人员简历证书",
            "安全生产考核合格证书": "项目管理机构与人员简历证书",
            "营业执照": "营业执照与企业资质证书",
            "建筑业企业资质证书": "营业执照与企业资质证书",
            "承诺函": "投标函与承诺书",
            "授权委托书": "法定代表人身份证明与授权委托书",
            "法定代表人身份证明": "法定代表人身份证明与授权委托书",
            "合并资产负债表": "财务状况与审计报告",
            "母公司资产负债表": "财务状况与审计报告",
            "合并利润表": "财务状况与审计报告",
            "审计报告": "财务状况与审计报告",
            "施工进度计划网络图": "施工组织设计与技术方案",
        }
        if not isinstance(category, str):
            return category
        source_label = (category in {"商务文件", "商务与资信标", "技术文件", "技术标"}
                        or (category not in DOC_CATEGORIES and len(category) >= 8 and category in source_heading))
        if source_label and source_heading:
            chapter_category = infer_doc_category_from_text(source_heading)
            if chapter_category != "其他投标材料":
                return chapter_category
        return aliases.get(category, category)

    @staticmethod
    def normalize_recovery_metadata(obj: Any, source_heading: str = "") -> Tuple[Any, List[Dict[str, Any]]]:
        """Normalize equivalent schema representations while retaining field values."""
        if not isinstance(obj, dict):
            return obj, []
        normalized = dict(obj)
        changes: List[Dict[str, Any]] = []
        category = QwenVLBackend.normalize_recovery_category(obj.get('doc_category'), source_heading)
        if category != obj.get('doc_category'):
            normalized['doc_category'] = category
            changes.append({'kind':'category','from':obj.get('doc_category'),'to':category})
        slot_names = ('company_name','credit_code','qualification_level','certificate_no','valid_until',
                      'person_name','person_role','id_card','project_name','contract_amount_wan',
                      'completion_date','bid_price_yuan','bid_price_upper','duration_days')
        top_level = {key:obj[key] for key in slot_names if key in obj}
        if 'extracted_kv' not in obj and top_level:
            normalized['extracted_kv'] = top_level
            changes.append({'kind':'top_level_slots_to_extracted_kv','keys':list(top_level)})
        return normalized, changes

    @staticmethod
    def _parse_vlm_json(raw_output: str, pdf_page: int, fallback_seal: bool) -> PageVisionResult:
        """Parse structured JSON emitted by Qwen2.5-VL."""
        cleaned = raw_output.strip()
        m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
                return PageVisionResult(
                    pdf_page=pdf_page,
                    doc_category=str(obj.get("doc_category") or "其他投标材料"),
                    ocr_text=str(obj.get("ocr_text") or ""),
                    has_official_seal=bool(obj.get("has_official_seal", fallback_seal)),
                    seal_company_name=obj.get("seal_company_name"),
                    has_signature=bool(obj.get("has_signature", False)),
                    extracted_kv={},
                    backend_name="qwen2.5_vl",
                )
            except json.JSONDecodeError:
                pass

        return PageVisionResult(
            pdf_page=pdf_page,
            doc_category=infer_doc_category_from_text(cleaned),
            ocr_text=cleaned,
            has_official_seal=fallback_seal,
            has_signature=("签字" in cleaned or "签章" in cleaned),
            extracted_kv={},
            backend_name="qwen2.5_vl_raw",
        )

    def _generate_recovery(self, image, prompt: str, max_tokens: int) -> Dict[str, Any]:
        """Bounded local generation with an explicit finish reason for recovery audits."""
        self._ensure_hf_loaded()
        import time
        import torch
        from transformers import StoppingCriteria, StoppingCriteriaList

        messages = [{"role": "user", "content": [
            {"type": "image", "image": image}, {"type": "text", "text": prompt}]}]
        formatted = self._hf_processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True)
        inputs = self._hf_processor(text=[formatted], images=[image],
                                    return_tensors="pt").to(self._hf_model.device)
        input_length = inputs.input_ids.shape[1]

        class RepeatedTail(StoppingCriteria):
            stopped = False

            def __call__(self, input_ids, scores, **kwargs):
                tail = input_ids[0, input_length:]
                if len(tail) >= 96:
                    # Repeated long spans indicate a loop; crop and retry instead of accepting it.
                    self.stopped = all(torch.equal(tail[-24:], tail[-24*(i+1):-24*i])
                                       for i in range(1, 4))
                return self.stopped

        stopper = RepeatedTail()
        started = time.perf_counter()
        with torch.inference_mode():
            ids = self._hf_model.generate(
                **inputs, max_new_tokens=max_tokens, do_sample=False,
                repetition_penalty=1.05, stopping_criteria=StoppingCriteriaList([stopper]))
        new_ids = ids[0, input_length:]
        text = self._hf_processor.decode(new_ids, skip_special_tokens=True,
                                         clean_up_tokenization_spaces=False).strip()
        reason = "repetition" if stopper.stopped else (
            "length" if len(new_ids) >= max_tokens else "eos")
        return {"text": text, "tokens": len(new_ids), "finish_reason": reason,
                "seconds": round(time.perf_counter() - started, 3)}

    def analyze_page_recovery(self, doc: fitz.Document, pdf_page: int,
                              heading_hint: str = "", rotation_degrees: int = 0,
                              crop_main_scan: bool = False) -> Tuple[Optional[PageVisionResult], Dict[str, Any]]:
        """Recover a failed page using separate transcription and compact field extraction.

        Exhausted output or repetition triggers bounded spatial retries. Failed attempts
        remain auditable and are never relabeled as successful merely by closing JSON.
        """
        import io
        from PIL import Image

        if self.mode != "transformers":
            raise ValueError("Page recovery currently requires the local transformers backend.")
        if rotation_degrees not in (0, 90, 180, 270):
            raise ValueError("Recovery rotation must be 0/90/180/270 degrees counterclockwise.")
        page = doc[pdf_page - 1]
        clip = page.rect
        if crop_main_scan:
            images = page.get_image_info()
            main = max(images, key=lambda x: fitz.Rect(x['bbox']).get_area(), default=None)
            if main and fitz.Rect(main['bbox']).get_area() > .3 * page.rect.get_area():
                clip = fitz.Rect(main['bbox']) & page.rect
        pix = page.get_pixmap(dpi=self.dpi, clip=clip, alpha=False)
        image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        if rotation_degrees:
            image = image.rotate(rotation_degrees, expand=True)
        audit: Dict[str, Any] = {"pdf_page": pdf_page, "ocr_attempts": [], "metadata_attempts": [],
                                 "rotation_degrees_ccw": rotation_degrees, "source_clip_pdf_points": list(clip)}
        ocr_prompt = (
            "请逐行转录这张文档图片中实际可见的文字。仅输出转录正文，不要JSON、解释、总结或补全。"
            "表格按行输出，每行各列用空格分开，不必生成Markdown表头和分隔线。"
            "保留原文的姓名、日期、金额、单位和表头；看不清的文字用[无法辨认]标记，不要猜测。"
            "不要凭空重复文字或空行。图片中全部可见内容转录完毕立即结束。")

        def transcribe(box, depth=0):
            crop = image.crop(box)
            attempt = self._generate_recovery(crop, ocr_prompt, 3072)
            attempt.update(box=list(box), depth=depth)
            audit["ocr_attempts"].append(attempt)
            if attempt["finish_reason"] == "eos" and attempt["text"]:
                return attempt["text"]
            if depth >= 2:
                return None
            x0, y0, x1, y1 = box
            mid = (y0 + y1) // 2
            top = transcribe((x0, y0, x1, mid), depth + 1)
            bottom = transcribe((x0, mid, x1, y1), depth + 1)
            if top is None or bottom is None:
                return None
            return top + "\n" + bottom

        ocr_text = transcribe((0, 0, image.width, image.height))
        if ocr_text is None:
            audit.update(status="failed", reason="ocr_output_incomplete_or_repetitive")
            return None, audit
        if clip != page.rect:
            native_text = page.get_text().strip()
            if native_text:
                ocr_text = native_text + "\n" + ocr_text
                audit["native_text_added"] = native_text
        metadata_prompt = (
            "只根据图片中明确可见的内容输出一个简短的合法JSON对象。不要转录正文，不要Markdown。"
            "不得推断图片未出现的信息。JSON必须具有如下键："
            '"doc_category"（从' + "、".join(DOC_CATEGORIES) + '中选一），'
            '"has_official_seal"（布尔值），"seal_company_name"（字符串或null），'
            '"has_signature"（布尔值），"extracted_kv"（对象）。'
            "extracted_kv固定为空对象，不提取业务字段。"
            "不输出ocr_text，尽量简短。章节提示：" + (heading_hint or "无"))
        for retry in range(2):
            attempt = self._generate_recovery(image, metadata_prompt, 1024 if retry == 0 else 1536)
            audit["metadata_attempts"].append(attempt)
            try:
                text = re.sub(r"^```(?:json)?\s*|\s*```$", "", attempt["text"].strip())
                obj = json.loads(text)
                obj, changes = self.normalize_recovery_metadata(obj, Path(doc.name or '').stem)
                if changes:
                    attempt['schema_normalization'] = changes
                if (attempt["finish_reason"] != "eos" or not isinstance(obj, dict)
                        or obj.get("doc_category") not in DOC_CATEGORIES
                        or not isinstance(obj.get("extracted_kv"), dict)
                        or not isinstance(obj.get("has_official_seal"), bool)
                        or not isinstance(obj.get("has_signature"), bool)
                        or not (obj.get("seal_company_name") is None
                                or isinstance(obj["seal_company_name"], str))):
                    raise ValueError("Invalid metadata schema or incomplete output")
                result = PageVisionResult(
                    pdf_page=pdf_page, doc_category=obj["doc_category"], ocr_text=ocr_text,
                    has_official_seal=obj["has_official_seal"], seal_company_name=obj.get("seal_company_name"),
                    has_signature=obj["has_signature"], extracted_kv={},
                    backend_name="qwen2.5_vl_recovered")
                audit.update(status="recovered", ocr_characters=len(ocr_text))
                return result, audit
            except (json.JSONDecodeError, ValueError) as exc:
                attempt["validation_error"] = str(exc)
                metadata_prompt += "\n请检查输出格式，直接以{开始、以}结束；只填真实可见的少量字段。"
        audit.update(status="failed", reason="invalid_metadata")
        return None, audit

    def analyze_page(
        self,
        doc: fitz.Document,
        pdf_page: int,
        existing_text: str = "",
        heading_hint: str = "",
    ) -> PageVisionResult:
        page = doc[pdf_page - 1]
        pix = page.get_pixmap(dpi=self.dpi, alpha=False)
        fallback_seal = detect_red_seal_from_pixmap(pix)
        png_bytes = pix.tobytes("png")

        if self.mode == "openai_compatible":
            import urllib.request

            b64_img = base64.b64encode(png_bytes).decode("ascii")
            payload = {
                "model": self.model_name_or_path,
                "messages": [
                    {"role": "system", "content": VLM_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": f"当前章节提示：{heading_hint or '无'}。请解析该投标文件页面并输出JSON。",
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{b64_img}"},
                            },
                        ],
                    },
                ],
                "temperature": 0.0,
            }
            req = urllib.request.Request(
                f"{self.api_base_url.rstrip('/')}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                content = resp_data["choices"][0]["message"]["content"]
                return self._parse_vlm_json(content, pdf_page, fallback_seal)

        # Direct HuggingFace Transformers inference on RTX 5080
        self._ensure_hf_loaded()
        import io
        import torch
        from PIL import Image

        pil_img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": pil_img},
                    {
                        "type": "text",
                        "text": f"{VLM_SYSTEM_PROMPT}\n当前章节提示：{heading_hint or '无'}",
                    },
                ],
            }
        ]
        prompt = self._hf_processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = self._hf_processor(
            text=[prompt], images=[pil_img], padding=True, return_tensors="pt"
        ).to(self._hf_model.device)

        with torch.no_grad():
            generated_ids = self._hf_model.generate(
                **inputs, max_new_tokens=1024, do_sample=False
            )
            trimmed_ids = [
                out_ids[len(in_ids) :]
                for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
            ]
            output_text = self._hf_processor.batch_decode(
                trimmed_ids, skip_special_tokens=True, clean_up_tokenization_spaces=False
            )[0]

        return self._parse_vlm_json(output_text, pdf_page, fallback_seal)


class DiskCachedVisionBackend(BaseVisionBackend):
    """
    Caching wrapper around any `BaseVisionBackend`.

    Persists page-level `PageVisionResult` entries to `<cache_dir>/<pdf_hash>.json`
    so VLM outputs computed on the RTX 5080 machine can be committed/shared and
    reused instantaneously on CPU laptops without re-running VLM inference.
    """

    def __init__(self, delegate: BaseVisionBackend, cache_dir: Path) -> None:
        self.delegate = delegate
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._memory_cache: Dict[str, Dict[int, PageVisionResult]] = {}

    @staticmethod
    def _doc_cache_key(doc: fitz.Document) -> str:
        doc_path = getattr(doc, "name", "") or "memory_pdf"
        stat_sig = f"{Path(doc_path).name}_{len(doc)}"
        digest = hashlib.sha1(stat_sig.encode("utf-8")).hexdigest()[:12]
        safe_stem = re.sub(r"[^\w\-.]", "_", Path(doc_path).stem)[:40]
        return f"{safe_stem}_{digest}"

    def _load_doc_cache(self, cache_key: str) -> Dict[int, PageVisionResult]:
        if cache_key in self._memory_cache:
            return self._memory_cache[cache_key]

        cache_file = self.cache_dir / f"{cache_key}.json"
        page_map: Dict[int, PageVisionResult] = {}
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for k, v in raw.items():
                    page_map[int(k)] = PageVisionResult.from_dict(v)
            except Exception as exc:
                logger.warning("Failed to read vision cache %s: %s", cache_file, exc)

        self._memory_cache[cache_key] = page_map
        return page_map

    def _save_doc_cache(self, cache_key: str, page_map: Dict[int, PageVisionResult]) -> None:
        cache_file = self.cache_dir / f"{cache_key}.json"
        serializable = {str(k): v.to_dict() for k, v in sorted(page_map.items())}
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)

    def analyze_page(
        self,
        doc: fitz.Document,
        pdf_page: int,
        existing_text: str = "",
        heading_hint: str = "",
    ) -> PageVisionResult:
        cache_key = self._doc_cache_key(doc)
        page_map = self._load_doc_cache(cache_key)
        if pdf_page in page_map:
            return page_map[pdf_page]

        result = self.delegate.analyze_page(
            doc=doc,
            pdf_page=pdf_page,
            existing_text=existing_text,
            heading_hint=heading_hint,
        )
        page_map[pdf_page] = result
        self._save_doc_cache(cache_key, page_map)
        return result
