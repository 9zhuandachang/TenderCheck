# 投标证据段落检索与评测

## 系统分工

招标条款 → 八分类 → 投标文件段落检索 → LLM 提取要求约束与投标事实 → 规则比较及审查结论。

当前主流程输出证据正文、完整父段落上下文、来源文件、PDF 物理页码、章节和检索分数。分类用于选择检索策略。工期、金额、人员资质等业务事实由后续 LLM 基于证据提取；RAG 不据此直接给出合规结论。真实段落到 LLM 提取及规则判断的接口见 [无台账推理流程](evidence_reasoning.md)。

文档解析仍复用已有 OCR 和缓存。历史 `extracted_kv` 保留读取兼容，但新解析不生成业务字段，检索输出不携带旧字段。台账构建器、旧双索引路由及 `--include_ledger` 已移除。旧索引中的台账文件不再读取，新索引不再生成台账。

## 查询入口

```powershell
python SystemCode/run_rag_pipeline.py --mode query `
  --index_dir SystemCode/data/rag_index/s604_repaired `
  --req_type Schedule --query "计划工期为360日历天。" --top_k 5 `
  --candidate_pool_size 50 `
  --reranker_model SystemCode/models/pretrained/bge-reranker-v2-m3
```

`--req_type` 是调用方提供的八分类结果；此单条查询入口不自动加载分类模型。实验入口会实际调用所指定的分类模型。新建索引会保存 `document_sources.json`；旧索引缺少映射时，传入 `--source_map 路径`。映射格式为 `{"document_id": "原始PDF绝对路径"}`，不能凭文件名猜页码。换机器时需更新路径。

## 四组实验

| 配置 | 检索方式 |
| --- | --- |
| bm25 | 原始条款、BM25、不路由 |
| hybrid | 原始条款、BGE-M3 + BM25、RRF 0.55/0.45 |
| hybrid_rerank | 与 hybrid 相同的前 50 个去重父段落候选，再由 BGE 重排序 |
| predicted_category_rerank | 实际八分类预测，使用现有类别策略，再重排序前 50 个候选 |

类别策略同时包含查询扩展、文档筛选和融合权重，因此最后两组的差异代表整个类别策略的效果，不能单独归因于筛选。检索按父段落去重；返回前 5 个父段落及命中的子段落。未截断父段落，各组上下文长度不完全相同；当前评测不是固定 token 预算的下游 LLM 实验。

```powershell
python SystemCode/evaluate_evidence_rag.py collect `
  --queries SystemCode/data/holdout_eval/s604_rag_benchmark.json `
  --index_dir SystemCode/data/rag_index/s604_repaired `
  --source_map SystemCode/data/rag_index/s604_repaired/document_sources.json `
  --model_dir SystemCode/models/roberta_wwm_ext `
  --reranker_model SystemCode/models/pretrained/bge-reranker-v2-m3 `
  --output_dir SystemCode/reports/rag_experiments/new_passage_run `
  --top_k 5 --candidate_pool_size 50 --offline
```

输出目录必须新建或为空。原 benchmark 只提供查询 ID、要求正文及章节；关键词、标准类别和业务字段不参与检索计分。输出包含：

- `retrieval.json`：四组完整排名、来源文件 SHA-256、物理页码、索引及输入哈希、模型路径、分类模型文件哈希。
- `review.md`：每条需求对应四组候选段落，供核对原 PDF。
- `annotations.json`：初始全部为待核对，不把候选自动当标准答案。

收集过程不更新索引，结束后检查索引与原 PDF 未改变。重排序失败会终止实验，不把回退排名当正式重排序成绩。模型路径不是完整权重版本锁定，复现还需保留相同模型文件。

## 参考证据如何标注

每条需求定义若干**必要证据组**：跨组必须齐全，同组的等价位置命中一个即可。例如核对报价一致性，需要“本项目投标函报价”和“本项目清单报价”两个组；只找到其中一个不算完整。

每个位置记录 `document_id`、原 PDF 的 `source_sha256`、从 1 开始的物理 `page_start/page_end`、原文 `quote`、`source_verified` 和核对依据 `verification_basis`（`native_pdf` 或 `visual_pdf`）。多个虚拟文档段可能属于同一 PDF，因此定位按文件哈希与物理页码判断。

对于表格，允许显式标注 `quote_parts`，它们必须是已核对原文内按顺序出现的必要片段。命中要求所有片段按顺序存在于同一返回父段落，文件及页码也必须匹配。例如费用名称与金额之间的数量列不影响所需总额证据时，可以指定名称及金额两个片段；不能删掉需求必需的数值、单位或条件。默认仍匹配完整 `quote`。仅统一空白和 Unicode 全半角，不忽略金额、负号或小数点。

不符合招标要求的实际响应、前后冲突的声明，仍是相关证据。招标条款的复述、空白模板说明、其他项目的相似材料，不自动算本项目响应。证据存在不表示其真实性或合规性已经确认。

标注状态：

- `pending`：尚未核实，不计分，也不视为没有材料。
- `assistant_verified`：助手核对过原件的初步参考，单独报告。
- `human_verified`：人工审定的参考，单独报告；不能自动从助手状态升级。

只有 `evidence_status=present` 且 `annotation_complete=true` 的已核对条目进入正样本分母。这里的 complete 表示该需求的必要证据组已定义，不代表已穷尽所有等价页。缺失、未知和部分标注单列，不能按零分或成功处理。原 PDF 全面核查后才能标记 `absent`；当前未计算无证据拒答率。

## 计分

```powershell
python SystemCode/evaluate_evidence_rag.py score `
  --run_dir SystemCode/reports/rag_experiments/new_passage_run `
  --annotations SystemCode/reports/rag_experiments/new_passage_run/annotations.json `
  --output SystemCode/reports/rag_experiments/new_passage_run/metrics.json
```

只重算分数，不调用模型。会验证来源文件未改变；原生文本标注还会重新在 PDF 原页核对原文。扫描件的目视核验由标注者负责，不能仅设置一个布尔值就当作完成核对。

| 指标 | 定义 |
| --- | --- |
| Hit@K | 前 K 个结果至少找到一个必要证据组的需求比例 |
| Evidence Recall@K（macro） | 逐需求计算找回组数/所需组数，再对需求平均 |
| Evidence Recall@K（micro） | 全部找回组数/全部所需组数 |
| 完整证据获取率@K | 前 K 个结果找齐所有必要证据组的需求比例 |
| MRR@K | 第一项必要证据的倒数排名平均；前 K 未命中为零 |

文件哈希、页码范围和原文必须同时匹配。计分针对实际交给后续 LLM 的完整父段落，不仅是子段落。没有已审定样本时指标为 `null`，不能宣称正确率为 0 或 100%。文件可定位性只是引用检查，不等于相关性。

## 当前结果的适用范围

截至 2026-10-20，S604 原 50 条需求已定义 81 个必要证据组，均为助手核对参考，人工审定仍为 0 条。四组冻结结果见 [全量检索结果及历史流程诊断](experiments/s604_full50_evaluation.md)。这 50 条的正式用途是 RAG 证据检索评测；下游复用仅为流程诊断，不据此产生抽取或规则准确率。数据与入口分工见 [评测总览](evaluation_layout.md)。

等价位置未穷尽、OCR/文本清洗差异仍可能产生假阴性。当前未对所有返回段落穷尽标注相关性，因此不报告 Precision 或 nDCG。先冻结本轮结果，补全参考标注、分离开发集和测试集，再做检索调优及独立项目验证。旧关键词/台账实验使用不同指标及候选预算，不能直接用新旧分数判断代码退步。

现已对接“需求＋证据段落＋引用”的 LLM 输入及规则接口，见 [无台账推理流程](evidence_reasoning.md)；正式验收还需要完整人工参考集、跨项目检索验证、字段提取评测和最终规则判定评测。
