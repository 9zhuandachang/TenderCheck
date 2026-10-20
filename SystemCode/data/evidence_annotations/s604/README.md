# S604 检索证据标注交接

导出日期：2026-10-20。交接基线：**50 条需求，26 条助手已核对，24 条待补齐，人工审定 0 条**。
最新状态（2026-10-20 原件补齐后）：**50 条需求，50 条助手已核对（`assistant_verified`），0 条待补齐，人工审定 0 条（`human_verified`）**。

这是 RAG 的参考证据标注，目标是定位与招标需求有关的投标原文。它不是合规结论标签，也不是本轮 10 条 DeepSeek 实验的模型输出。原有 50 条需求与编号保持不变。下游合规结论参考标注已单独保存在 [compliance_verdict_labels.json](compliance_verdict_labels.json) 中，与检索证据标注完全解耦。

## 文件

| 文件 | 用途 |
| --- | --- |
| [annotations.json](annotations.json) | 唯一检索证据主标注文件，包含全部 50 条（均已完成原件核对并设为 `assistant_verified`，`annotation_complete=true`）。 |
| [compliance_verdict_labels.json](compliance_verdict_labels.json) | 独立保存的 50 条合规结论与评分参考标注（`assistant_verified`），与检索证据标注分离存放。 |
| [pending.json](pending.json) | 待补齐任务快照（当前 `pending_count: 0`，并记录本轮已完成的 24 个 ID）。 |
| [progress.md](progress.md) | 全部 50 条的可读进度与已核对证据组物理页位置。 |
| [sources.json](sources.json) | 24 个 document_id 对应的文件相对路径、SHA-256 和页数，共 20 份不同 PDF。 |

`annotations.json` 初始交接导出 SHA-256：`d79bf299882994355f395665fa33722f1e6d84a94b2e1567f3304ca4c5a07f74`。
`annotations.json` 补齐 24 条后 SHA-256：`459a03c74f287f65bb38b32c4820899a714c030c8f6347a6bc4308ccf6157f25`。

## 给接手 agent 的任务

1. 先阅读主标注文件中的完整样例及 [标注规则](../../../../docs/passage_rag_evaluation.md)。
2. 根据 `pending.json` 的 24 个 ID，在 `annotations.json` 原条目上补充证据组。不要新增另一套需求编号或改写招标需求。
3. 核对原 PDF 后填写引用、来源哈希、物理页码、核对方式及 reviewer/review_note。助手标注只能设为 `assistant_verified`；不得自动升级为 `human_verified`。
4. `annotation_complete=true` 表示本条必要证据组已经定义齐全。缺材料、无法读取或只核对了一部分时保留未完成状态并写清原因。未检索到不等于原文件不存在。
5. 若发现已有 26 条有误或漏等价位置，可以明确记录依据后修正；不要为提高现有检索分数放宽证据要求。
6. 完成后更新进度和待办快照，并汇报各状态数量、修改的 ID、仍未解决的事项。人工审定状态仍须真实人工核对。

本次已完成补齐的 24 个 ID：

`T0003 T0004 T0005 T0007 T0009 T0011 T0012 T0013 T0014 T0015 T0023 T0024 T0033 T0069 T0078 T0079 T0081 T0106 T0152 T0209 T0216 T0222 T0225 T0226`

## 原文件定位

`sources.json` 的 `relative_path` 相对于仓库根目录。`data_extracted/` 原始文件没有随此提交上传，接手环境需已有用户共享的原件；只有 GitHub 代码时无法完成原件核对。

- `sys01` 到 `sys05` 是同一份 1875 页合并 PDF 的虚拟分段 ID。引用的页码是合并 PDF 从 1 开始的物理页，不能按章节重新从 1 编号。
- `sub01` 到 `sub19` 是分章节 PDF，使用各自 PDF 从 1 开始的物理页。
- PDF 印刷页码与物理页码可能不同。换电脑只改本机路径映射，不改文件身份哈希，也不要猜合并页与分章节页之间的偏移。
- 先确认文件 SHA-256 与映射一致；另一个版本的 PDF 必须重新核对，不能套用旧页码。

本映射是便携交接清单，不能直接作为 `--source_map` 的输入；该参数需要 `document_id -> 本机PDF绝对路径` 的简单映射。

## 证据组约定

- 不同 `evidence_groups` 是必须共同具备的证据；一个组的 `alternatives` 是等价位置，找到一个即可。
- 比如跨表报价一致性至少需要当前项目投标函和清单两个组；人员资质通常要关联拟任角色和对应证书。
- 保留不同主体、项目和时间的关系。合作单位证书不能代替当前投标人证书，历史合同不能代替当前报价。
- 承诺存在、材料存在、真实合规是不同问题。概括承诺可以是相关检索证据，但不一定能直接推出数值、事实真实性或最终合规结论。
- 不符合要求或互相矛盾的实际响应仍是相关证据，不能只标“通过”的内容。
- `native_pdf` 表示核对原生 PDF 文本；扫描证书需实际看原页，使用 `visual_pdf` 并记录依据。不得仅凭检索片段或 OCR 自动把 `source_verified` 设为 true。
- `quote` 保留必要的主体、数值、单位和条件。表格可用按原文顺序出现的 `quote_parts`，不能删掉会改变含义的内容。
- 现有助手标注可能漏掉等价位置，属于探索参考，不能当成完整人工金标准。

## 标注完成后的计分

持有原冻结检索结果的环境可运行以下命令，只重算分数，不调用模型：

```powershell
python SystemCode/evaluate_evidence_rag.py score `
  --run_dir SystemCode/reports/rag_experiments/s604_passage_evidence_v2 `
  --annotations SystemCode/data/evidence_annotations/s604/annotations.json `
  --output SystemCode/reports/rag_experiments/s604_passage_evidence_v2/metrics_completed_annotations.json
```

完整检索结果、索引及 OCR 缓存没有放入本交接包。没有本地冻结结果时先完成原件标注，不要伪造检索记录或更改 `corpus_sha256` 来通过校验。该值绑定原检索语料版本，不是 sources.json 的文件哈希。
