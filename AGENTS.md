# TenderCheck Engineering Constitution (`AGENTS.md`)

This document defines mandatory architectural invariants, coding standards, and file-hygiene rules for **TenderCheck**. All human contributors and AI agents operating in this repository must strictly follow these rules.

---

## 1. Core Architectural Invariants (不可逾越的架构红线)

### 1.1 Neuro-Symbolic Separation & Zero False-Pass (严禁玩具兜底与假放行)
- **Role Boundary**: Neural models and LLMs (`DeepSeekSlotExtractor`, `AutoTenderAnnotator`, `HybridBidIndex`) are strictly limited to **clause perception, retrieval, and verbatim-quoted slot extraction**. Only `ComplianceRuleEngine` (`R01`–`R09`) may issue compliance verdicts.
- **Strict Abstention on Missing/Uncertain State**: When an API key is absent, retrieval misses, OCR fails, or any extracted slot fails verbatim provenance checks (`ground_extraction`), the **only** valid behaviors are raising an explicit error or returning `INSUFFICIENT_EVIDENCE` (routed to human review).
- **Prohibited Shortcuts**:
  - Never fabricate mock personnel, mock performance records, back-calculated denominators, or synthetic validity dates.
  - Never copy `tender_slots.expected_numeric` into `bid_slots.actual_numeric` unless the bid passage explicitly states the numeric value and passes `numeric_in_quote`.
  - Never flip a `NON_COMPLIANT` or `INSUFFICIENT_EVIDENCE` verdict to `COMPLIANT` via UI confirmation (`confirm_result`) or ungrounded text edits; manual overrides must go through `approve_manual_pass` with mandatory written rationale and audit logging.

### 1.2 Cross-Domain Generalization & Anti-Hardcoding (严禁单标书/单地区硬编码)
- Core library modules under `SystemCode/src/` must work across Chinese engineering domains (highway, water conservancy, municipal EPC, building construction) and across tender years/provinces.
- **Prohibited Hardcoding in `src/`**:
  - Do not introduce new branches matching a specific project name (e.g., `S604`, `飞毕路`), fixed historical year sets (e.g., `{2019, 2020, 2021}`), single-province certificate prefixes (e.g., `皖交安` only), or verbatim section titles unique to one tender book.
  - Domain vocabularies (qualification hierarchies, major/specialty mappings, BoQ chapter structures) must be parameterized or externalized into structured domain profiles.
- **Two-Project Verification Mindset**: Any new extraction or symbolic rule logic must be validated not only against S604 (highway), but also mentally and empirically checked against non-highway / multi-year tender formats.

### 1.3 Single Execution Trunk (单一真实主干)
- Do not build parallel "demo-only" heuristics alongside the production pipeline. Web UI (`SystemCode/src/app/`), CLI scripts, and offline evaluation suites must invoke the **same** underlying parsing, retrieval, grounding, and symbolic rule modules.
- When refactoring legacy split paths, merge into the canonical module and delete obsolete duplicate code rather than maintaining two versions.

---

## 2. File & Documentation Hygiene Contract (文件与文档落盘铁律)

### 2.1 Strict Markdown Whitelist (定额文档白名单，禁止乱建 `.md`)
Only the following Markdown files are permitted in the repository:
1. `README.md` — External project showcase, architecture overview, and setup/reproduction guide.
2. `AGENTS.md` — This engineering constitution.
3. `docs/DECISIONS.md` — Architecture Decision Records (ADR ledger; append or supersede **in place**).
4. `docs/ROADMAP.md` — Single source of truth for project phases, open defects, and milestones (update **in place**).
5. `docs/EXPERIMENTS.md` — Chronological benchmark & cross-project evaluation ledger (append summary rows **in place**).
6. `Miscellaneous/TenderCheck_Annotation/TenderCheck_Annotation_Guideline_v1_3.md` — Official v1.3 requirement annotation specification.

> **Rule**: Never create ad-hoc `.md` files (such as `SUMMARY.md`, `NOTES.md`, `PLAN_v2.md`, `FIX_REPORT.md`) anywhere in the repository without explicit user authorization. Ephemeral plans and debugging notes belong in chat responses or external agent workspace artifacts.

### 2.2 Zero Script & Artifact Sprawl (禁止冗余中间脚本与实验垃圾入库)
- **Scratch Scripts**: One-off probes, data inspection scripts, and temporary reproductions must be written strictly inside `scratch/` (git-ignored) and deleted once finished. Never place `.py` scripts at the repository root or create `temp_*.py`, `debug_*.py`, or `*_v2.py` files inside `SystemCode/`.
- **Experiment Outputs**: Large per-clause JSON dumps, raw LLM responses, and diagnostic logs must be written under `SystemCode/reports/runs/` (git-ignored). Only distilled metrics and commit-linked takeaways are recorded in `docs/EXPERIMENTS.md`.

---

## 3. Development Workflow & Definition of Done (开发闭环标准)

1. **Boundary Alignment Before Coding**: For non-trivial changes (e.g., addendum superseding, multi-domain profiles, BoQ table diffing, cross-clause re-evaluation), clarify edge cases (scanned PDFs, cross-page tables, missing evidence, runtime latency) before writing implementation code.
2. **Automated Test Verification**:
   - Canonical Conda environment on local machine: `tendercheck` (`C:\Users\34810\miniconda3\envs\tendercheck\python.exe`).
   - Run the full unit and hygiene suite from `SystemCode/` before finalizing changes:
     ```powershell
     C:\Users\34810\miniconda3\envs\tendercheck\python.exe -m unittest discover -s tests -p "test_*.py"
     ```
3. **In-Place Documentation Sync**:
   - Completed a milestone or fixed a tracked issue -> check off `[x]` in `docs/ROADMAP.md`.
   - Made an architectural or domain-modeling trade-off -> append a concise ADR entry in `docs/DECISIONS.md`.
   - Executed an evaluation or generalization benchmark -> append a metrics row in `docs/EXPERIMENTS.md`.
