# TenderCheck — `SystemCode` Developer & Reproduction Guide

This directory contains the complete source code, trained model checkpoints, evaluation benchmarks, and Streamlit interactive workspace for **TenderCheck**.

---

## 1. Directory Layout

```text
SystemCode/
├── src/
│   ├── parsing/
│   │   ├── tender_segmenter.py        # Step 1: Layout- & table-aware Chinese tender PDF clause segmenter
│   │   └── ocr_seal_detector.py       # Scanned PDF page OCR fallback & red official seal detector
│   ├── classification/
│   │   ├── binary_filter.py           # Step 1: 24-feature BinaryRequirementFilter (+ structural fallback)
│   │   ├── roberta_classifier.py      # Step 2: Fine-tuned 8-class chinese-roberta-wwm-ext classifier
│   │   └── dataset.py                 # Project-grouped dataset loader & leak-free splitter
│   ├── retrieval/
│   │   └── hybrid_retriever.py        # Step 3: Parent-Child Hybrid RAG (BGE-M3 + BM25 + RRF + Reranker)
│   ├── extraction/
│   │   ├── schema.py                  # Typed dataclasses: TenderConstraintSlots & BidEvidenceSlots
│   │   ├── llm_extractor.py           # Step 4: DeepSeekSlotExtractor (contract: passage-facts-v11-arch)
│   │   ├── evidence.py                # Step 5: Anti-hallucination verbatim substring & numeric grounding
│   │   ├── conflicts.py               # Step 5: Cross-passage factual contradiction verifier
│   │   ├── semantics.py               # Step 5: Role-entity binding & qualification ownership checks
│   │   ├── global_context.py          # Step 5: ProjectGlobalContext builder & cross-chapter hydration
│   │   ├── auto_annotator.py          # End-to-end tender PDF auto-annotator (Step 1 + Step 2)
│   │   └── chinese_pipeline.py        # Live Chinese bid PDF parser, fast retriever & global scanner
│   ├── rules/
│   │   ├── engine.py                  # Step 6: ComplianceRuleEngine dispatcher
│   │   └── implementations.py         # Step 6: Deterministic symbolic rules R01–R09
│   ├── evaluation/
│   │   ├── extraction_eval.py         # Slot-level & citation-grounding evaluator for Steps 4–5
│   │   └── rule_eval.py               # 88-case positive/negative synthetic test suite for R01–R09
│   └── app/
│       ├── app.py                     # Streamlit entry point
│       ├── tcui/                      # Frontend services, SQLite store, real_backend bridge & exporter
│       └── views/                     # Overview, Documents, Requirements, Review Workspace, Remediation
├── data/
│   ├── annotation/                    # 71 annotated Chinese tender projects (v1.3 JSON format)
│   ├── rag_eval/                      # S604 50-clause retrieval benchmark
│   ├── extraction_eval/               # S604 50-clause structured slot extraction benchmark
│   ├── extraction_cache/              # Cached DeepSeek slot extractions (passage_v3.json)
│   └── rag_index/s604_repaired/       # Pre-built S604 Parent-Child Hybrid RAG index
├── models/
│   ├── roberta_wwm_ext/               # Fine-tuned 8-class RoBERTa weights & config
│   └── binary_filter/                 # Trained BinaryRequirementFilter pipeline & threshold config
├── reports/                           # Evaluation metrics & full S604 replay outputs
└── tests/                             # Unit & integration tests
```

---

## 2. Model Weights (Google Drive) & Running the Application

### 2.1 Downloading Model Checkpoints
Download the trained model weights from **Google Drive** *(insert link here)* and extract them into `SystemCode/models/`:
- `SystemCode/models/binary_filter/` (`config.json`, `binary_filter_pipeline.joblib`)
- `SystemCode/models/roberta_wwm_ext/` (`classifier_meta.json`, `config.json`, `tokenizer.json`, `tokenizer_config.json`, `model.safetensors`)

### 2.2 Launching the Streamlit Workspace
From the repository root or `SystemCode/`:

```bash
# Install dependencies
pip install -r SystemCode/requirements.txt

# Launch Streamlit UI
streamlit run SystemCode/src/app/app.py
```

- **Offline Demo / S604 Full Replay**: Click **"Load S604 Real Project"** on the home page. All 359 clauses and 22 PDFs load immediately using pre-verified RAG passages and cached extractions.
- **Live Upload of New Tender & Bid PDFs**: Set `DEEPSEEK_API_KEY` in your environment or in `SystemCode/.env`, create a new project in the UI, upload your tender and bid PDFs, and click **"Confirm documents & start review pipeline"**.

---

## 3. Reproducing Module Evaluations (Steps 1–6)

Run all commands below from inside `SystemCode/`:

### Step 1: Binary Requirement Filter (`train_binary_filter.py`)
Trains and evaluates the 24-feature binary requirement filter across 69 non-holdout projects and 2 holdout projects (including S604 end-to-end PDF auto-annotation recall):
```bash
python train_binary_filter.py
```
- **Outputs**: `models/binary_filter/binary_filter_pipeline.joblib`, `reports/binary_filter_evaluation.json`, and `reports/auto_annotation_evaluation.json`.

### Step 2: 8-Class RoBERTa Requirement Classifier (`train_classifier.py`)
Evaluates (or fine-tunes) `hfl/chinese-roberta-wwm-ext` on the 8 compliance categories (`Qualification`, `Document`, `Technical`, `Commercial`, `Schedule`, `Consistency`, `Integrity`, `Scoring`):
```bash
# Evaluate existing checkpoint in models/roberta_wwm_ext
python train_classifier.py --eval_only

# Or run full fine-tuning on GPU
python train_classifier.py --epochs 4 --batch_size 32 --fp16
```
- **Outputs**: `reports/roberta_wwm_ext_val_metrics.json`, `reports/roberta_wwm_ext_test_metrics.json`, and `reports/roberta_wwm_ext_holdout_metrics.json`.

### Step 3: Parent-Child Hybrid RAG (`evaluate_evidence_rag.py`)
Evaluates retrieval hit rates (`Document Hit@5`, `Page Hit@5`, `Content Anchor Hit@5`, `MRR`) on the 50-clause S604 retrieval benchmark:
```bash
python evaluate_evidence_rag.py \
  --index-dir data/rag_index/s604_repaired \
  --benchmark data/rag_eval/s604_50_retrieval_benchmark.json
```

### Steps 4–5: LLM Slot Extraction, Verbatim Grounding & Global Hydration (`evaluate_extraction.py`)
Evaluates structured slot accuracy, subject binding accuracy, citation support rate, unknown abstention rate, and hallucination rate on the 50-clause extraction benchmark (`data/extraction_eval/s604_extraction_benchmark.json`):
```bash
# Raw cached LLM extraction (before GlobalContext hydration)
python evaluate_extraction.py --split all

# Full Step 4–5 pipeline (with Verbatim Grounding + GlobalContext Hydration)
python evaluate_extraction.py --split all --hydrate-global-context
```
- **Outputs**: `reports/extraction_eval/all_report.json`, `dev_report.json`, `test_report.json`.

### Step 6: Symbolic Compliance Rule Engine (`evaluate_rule_engine.py` & `replay_compliance_responses.py`)
1. **88-Case Rule Contract & Unit Suite**:
   ```bash
   python evaluate_rule_engine.py
   ```
2. **S604 Full 359-Clause End-to-End Replay**:
   ```bash
   python replay_compliance_responses.py \
     --cases reports/rag_experiments/s604_full359_reasoning_inputs/cases.json \
     --raw-dir reports/rag_experiments/s604_full359_reasoning_deepseek \
     --output-dir reports/rag_experiments/s604_full359_replay_v3 \
     --review-date 2022-11-11
   ```

---

## 4. Running Automated Tests

```bash
python -m unittest \
  tests/test_compliance_rules.py \
  tests/test_compliance_reference.py \
  tests/test_rule_contracts_and_suite.py \
  tests/test_extraction_benchmark_and_eval.py
```
