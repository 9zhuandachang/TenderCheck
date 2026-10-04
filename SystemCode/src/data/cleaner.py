import json
import sys
from pathlib import Path
from typing import Dict, List, Any

# Ensure UTF-8 output on Windows console
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

NLI_LABEL_MAP = {
    "Entailment": 0,
    "Contradiction": 1,
    "NotMentioned": 2
}

def clean_contract_nli_split(
    json_path: Path, 
    labels_meta: Dict[str, Any]
) -> (List[Dict[str, Any]], List[Dict[str, Any]]):
    """
    Parses a ContractNLI split JSON file and returns:
    1. nli_pairs: (hypothesis, context, choice, label)
    2. clause_records: (span_text, category_id, category_name)
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    nli_pairs = []
    clause_records = []
    
    for doc in data["documents"]:
        doc_id = doc["id"]
        full_text = doc["text"]
        spans = doc["spans"]
        
        # Access the annotation set
        annotations = doc["annotation_sets"][0]["annotations"]
        
        for hyp_id, hyp_info in labels_meta.items():
            hyp_text = hyp_info["hypothesis"]
            hyp_desc = hyp_info["short_description"]
            
            ann = annotations.get(hyp_id)
            if not ann:
                continue
            
            choice = ann["choice"]
            label = NLI_LABEL_MAP.get(choice, 2)
            evidence_span_indices = ann.get("spans", [])
            
            # Extract evidence text
            if evidence_span_indices:
                evidence_pieces = []
                for idx in evidence_span_indices:
                    if idx < len(spans):
                        start, end = spans[idx]
                        span_text = full_text[start:end].strip()
                        if span_text:
                            evidence_pieces.append(span_text)
                            # Record for clause classification
                            clause_records.append({
                                "doc_id": doc_id,
                                "span_id": idx,
                                "text": span_text,
                                "category_id": hyp_id,
                                "category_name": hyp_desc
                            })
                context_text = " \n ".join(evidence_pieces)
            else:
                # For NotMentioned, provide document header / first 300 characters as neutral context
                context_text = full_text[:300].strip().replace("\n", " ")
            
            nli_pairs.append({
                "doc_id": doc_id,
                "hypothesis_id": hyp_id,
                "hypothesis": hyp_text,
                "context": context_text,
                "choice": choice,
                "label": label
            })
            
    return nli_pairs, clause_records

def process_all_splits(raw_dir: Path, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Locate train.json to obtain label definitions
    train_path = raw_dir / "contract_nli" / "contract-nli" / "train.json"
    if not train_path.exists():
        # Maybe extracted directly under raw_dir / "contract_nli"
        train_path = raw_dir / "contract_nli" / "train.json"
        
    if not train_path.exists():
        raise FileNotFoundError(f"Could not find train.json in {raw_dir}")
        
    with open(train_path, "r", encoding="utf-8") as f:
        meta_data = json.load(f)
    labels_meta = meta_data["labels"]
    
    # Save label definitions
    labels_file = output_dir / "labels_meta.json"
    with open(labels_file, "w", encoding="utf-8") as f:
        json.dump(labels_meta, f, indent=2, ensure_ascii=False)
    print(f"[OK] Saved label metadata to {labels_file}")
    
    for split in ["train", "dev", "test"]:
        split_path = train_path.parent / f"{split}.json"
        if not split_path.exists():
            print(f"[!] Warning: {split_path} does not exist, skipping.")
            continue
            
        print(f"[*] Processing {split_path}...")
        nli_pairs, clause_records = clean_contract_nli_split(split_path, labels_meta)
        
        # Save NLI pairs
        nli_out = output_dir / f"contract_nli_{split}_pairs.jsonl"
        with open(nli_out, "w", encoding="utf-8") as f:
            for item in nli_pairs:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
                
        # Save Clause classification data
        clause_out = output_dir / f"contract_clauses_{split}.jsonl"
        with open(clause_out, "w", encoding="utf-8") as f:
            for item in clause_records:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
                
        print(f"[OK] {split.upper()}: {len(nli_pairs)} NLI pairs -> {nli_out.name}")
        print(f"[OK] {split.upper()}: {len(clause_records)} Clause items -> {clause_out.name}")

if __name__ == "__main__":
    base_data_dir = Path(__file__).resolve().parent.parent.parent / "data"
    raw_data_dir = base_data_dir / "raw"
    processed_data_dir = base_data_dir / "processed"
    
    process_all_splits(raw_data_dir, processed_data_dir)
