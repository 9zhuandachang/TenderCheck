# Miscellaneous & Supplementary Materials — TenderCheck

This folder contains supplementary technical specifications, annotation guidelines, and detailed stage-by-stage evaluation reports for **TenderCheck**:

- `system_architecture.jpg`: High-resolution system architecture diagram of the 6-step neuro-symbolic pipeline.
- `TenderCheck_Annotation/`:
  - `TenderCheck_Annotation_Guideline_v1_3.md`: Formal 8-category requirement annotation guideline across 71 Chinese engineering tender documents.
  - `output_schema.json` & `annotated_examples.json`: JSON schema and gold-standard annotation examples.
- `docs/`:
  - `rule_inventory_and_contracts.md`: Formal specification of Symbolic Compliance Rules `R01`–`R09` and structured slot contracts (`passage-facts-v11-arch`).
  - `evidence_reasoning.md`: Detailed neuro-symbolic reasoning design and S604 full 359-clause replay analysis.
  - `rag_reproduction.md` & `passage_rag_evaluation.md`: Parent-Child Hybrid RAG indexing, category-guided retrieval, and evaluation methodology.
  - `extraction_rules_plan.md` & `extraction_eval_guide.md`: LLM slot extraction, verbatim quote grounding (`ground_extraction`), and `ProjectGlobalContext` hydration specifications.
  - `TenderCheck_Frontend_Design.md`: Streamlit Review Workspace UI/UX architecture.
  - `experiments/`: Detailed evaluation outputs, CSV benchmarks, and validation reports for classification, RAG, slot extraction, and the rule engine.
