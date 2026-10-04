# TenderCheck: Evidence-Based Tender Compliance Review System
> **NUS-ISS Intelligent Reasoning Systems (IRS) Capstone Project**  
> 基于证据链的水利水电与土木工程招投标智能合规审查系统

---

## 📌 项目概述 (Project Overview)

在工程建设招投标领域，招标文件（甲方要求）与投标文件（乙方响应）篇幅长、专业条款繁杂，传统人工审查存在容易遗漏废标红线、审查耗时长等痛点。**TenderCheck** 结合机器学习（条款智能分类与规则路由）与确定性规则推理引擎（Rule Engine），实现招投标合规性自动审查与证据链溯源。

---

## 🏗️ 核心架构与模块划分 (Architecture & Modules)

代码工程位于 `SystemCode/` 目录下，按照分层推理与工程化标准划分：

```text
SystemCode/
├── data/                         # 数据集与元数据
│   ├── processed/                # 标准化处理后的训练集/验证集/测试集 (JSONL)
│   └── raw/                      # 原始基准数据
├── models/                       # 训练持久化模型 (99.45% 准确率条款分类器)
├── reports/                      # 评测指标与实验对比分析报告
└── src/                          # 核心功能源代码
    ├── baseline/                 # 开源基线与分类模型实现 (Logistic Regression & Linear SVM)
    ├── data/                     # 数据抽取、清洗、分类体系与目录标准化脚本
    ├── extraction/               # 【待开发】文档结构化解析与槽位抽取 (工期、金额、资质等)
    ├── rules/                    # 【待开发】确定性硬指标合规判定规则引擎 (工期对比、限价核验等)
    ├── rag/                      # 【待开发】证据链对齐与知识检索模块 (条款与投标段落匹配)
    ├── evaluation/               # 【待开发】端到端系统基准评测模块 (对齐真实专家评审单金标)
    ├── app/                      # 【待开发】用户交互界面与报告生成器 (Streamlit Web UI)
    └── utils/                    # 评测指标计算与通用工具函数
```

---

## 🚀 模块职责说明 (Module Responsibilities)

1. **`src/data/` (数据工程与清洗)**
   * `organize_projects.py`: 76 个全库工程项目目录零删除标准化整理工具。
   * `build_tender_dataset.py`: 招标文件条款解析与项目物理隔离划分 (Project-Level Split)。
   * `taxonomy.py`: 工程招投标 `5+1` 维标准合规分类体系定义。
2. **`src/baseline/` (分类与路由模型 - 已训练)**
   * `chinese_classifier.py`: 基于 Jieba + TF-IDF + Linear SVM 的条款智能分类与规则路由器，测试集准确率 **99.45%**，Macro-F1 **0.9814**。
3. **`src/extraction/` (抽取层 - 规划中)**
   * 从非结构化文本中精准抽取工期数值、控制价金额、企业资质、人员证件等结构化事实。
4. **`src/rules/` (规则引擎层 - 规划中)**
   * 零幻觉、确定性硬逻辑校验（如 `投标工期 <= 招标工期`、`报价 <= 控制价`、`证书有效期 > 开标日`）。
5. **`src/rag/` (证据检索层 - 规划中)**
   * 负责长篇幅施工组织设计、类似业绩的语义检索与证据段落高亮对齐。
6. **`src/evaluation/` (评测体系 - 规划中)**
   * 对接真实专家评审单（Ground Truth）进行端到端精确率与召回率自动化打分。
7. **`src/app/` (应用交互层 - 规划中)**
   * Streamlit 交互式审查工作台，支持标书上传、风险项红色预警与导出合规报告。

---

## 🛠️ 快速上手 (Quick Start)

### 1. 环境准备
```bash
pip install -r requirements.txt
```

### 2. 运行条款分类模型训练与评测
```bash
python SystemCode/src/baseline/chinese_classifier.py
```

### 3. 项目目录标准化工具 (Zero-Deletion)
```bash
python SystemCode/src/data/organize_projects.py --all
```
