# TenderCheck review workspace

Streamlit front end for the Proposal workflow: upload → requirements → automated review →
evidence review → remediation and re-inspection → sign-off and export. The page design follows
[`docs/TenderCheck_Frontend_Design.md`](../../../docs/TenderCheck_Frontend_Design.md).

## Run

From `SystemCode/`:

```bash
pip install -r requirements-app.txt
streamlit run src/app/main.py
```

Open http://localhost:8501 and choose **Load demo project** for a fictitious English drainage tender
(tender, Addendum 1, Bid v1, and a revised Bid v2 for the re-inspection step).

Project files and the SQLite database are stored in `SystemCode/data/app/` (git-ignored). Set
`TENDERCHECK_DATA_DIR` to use another location.

## Structure

| Path | Purpose |
| --- | --- |
| `main.py` | Entry point, navigation, sidebar (project, role, data notice) |
| `views/` | One file per page: overview, documents, requirements, workspace, remediation, report |
| `tcui/service.py` | Service layer used by the pages: documents, extraction, review rounds, reviewer actions, remediation, sign-off |
| `tcui/engine.py` | **Placeholder** reasoning engine (keyword classifier, keyword retrieval, regex extraction, rules R01–R04, drawing check, KB matching) |
| `tcui/store.py` | SQLite persistence and audit log |
| `tcui/jobs.py` | Background jobs, so reruns and refreshes never restart a task |
| `tcui/export.py` | Excel compliance matrix and PDF review report |
| `tcui/demo.py` | Generates the demo PDFs |
| `kb/knowledge_base.json` | Static knowledge base used for remediation suggestions |

## Connecting the real pipeline

Each public function in `tcui/engine.py` maps to one Proposal component and returns plain dicts.
Replace them one by one without changing the pages:

| Engine function | Proposal component |
| --- | --- |
| `parse_document` | Document Parser |
| `extract_requirements`, `extract_addendum_changes` | Requirement Extractor (LLM) |
| `classify` | Clause Classifier (ML) |
| `BidIndex.retrieve` | Hybrid Evidence Retriever |
| `extract_constraint`, `BidIndex.best_sentence` | LLM structured extraction |
| `evaluate_rule` | Symbolic Rule Engine |
| `check_drawings` | Drawing register and version check |
| `suggest_kb`, `draft_revision` | Remediation Recommender |

If an external LLM is used, set `TENDERCHECK_LLM_PROVIDER` (for example `DeepSeek API`) so the
sidebar and upload page tell users that document text leaves the machine.

## Test

```bash
python -m pytest tests/test_review_app.py
```
