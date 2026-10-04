# TenderCheck: Evidence-Based Tender Compliance Review System

> **NUS-ISS Intelligent Reasoning Systems (IRS) Capstone Project**  
> An automated compliance verification and evidence-chain tracing system for engineering tenders and bids.

---

## 📌 Overview

Reviewing engineering bids against tender requirements is traditionally labor-intensive and prone to human oversight on disqualifying criteria. **TenderCheck** combines machine learning (for clause classification and routing) with a deterministic rule engine (for zero-hallucination verification of quantitative constraints like budget ceilings, project duration, and qualification validity).

---

## 🏗️ Repository Structure

```text
TenderCheck/
├── docs/                        # Project proposal and presentation slides
├── SystemCode/                  # Core system implementation
│   ├── data/                    # Processed datasets and metadata
│   ├── models/                  # Trained models (clause classification)
│   ├── reports/                 # Benchmark metrics and evaluation reports
│   └── src/
│       ├── baseline/            # Baseline models and clause classification router
│       ├── data/                # Data cleaning, normalization, and dataset construction
│       ├── extraction/          # [In Progress] Structured fact & slot extraction
│       ├── rules/               # [In Progress] Deterministic compliance rule engine
│       ├── rag/                 # [In Progress] Evidence retrieval & clause alignment
│       ├── evaluation/          # [In Progress] End-to-end evaluation against expert review sheets
│       ├── app/                 # [In Progress] Interactive Web UI and report exporter
│       └── utils/               # Common utilities and evaluation metrics
├── requirements.txt             # Python dependencies
└── README.md                    # Project documentation
```

---

## 🚀 Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Train & Evaluate Clause Classifier
```bash
python SystemCode/src/baseline/chinese_classifier.py
```

### 3. Standardize Project Directories (Zero-Deletion)
```bash
python SystemCode/src/data/organize_projects.py --all
```
