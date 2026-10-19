# 真实证据输入的提取与规则判断

更新日期：2026-10-20。

## 分工与迁移

招标条款及分类 → RAG 返回实际段落和来源 → LLM 提取 `tender_slots`（约束）与 `bid_slots`（事实） → 规则判断。

旧台账构建器、双索引路由、字段汇总及旧代理评测已移除。单条查询和增量重建均不读取或生成台账。原始 PDF、已有成功 OCR 缓存和向量保留；新的视觉解析只请求正文及版面元数据，不再提取工期、报价等业务字段。旧缓存的字段仅兼容读取，新分块不继承这些业务字段。

八分类对照使用 `scripts/compare_classifiers.py`，检索评测使用 `evaluate_evidence_rag.py`。旧 `evaluate_ml_rag.py` 会明确提示迁移入口，不再产生代理成绩。

## 准备真实输入

输入必须是已完成的 `evaluate_evidence_rag.py collect` 输出。选择某一方法，保持它实际返回的排名、原文及空结果；不会使用标准答案关键词重新挑段落，也不会拼接预先提取的字段。

```powershell
python SystemCode/evaluate_compliance_reasoning.py prepare `
  --retrieval SystemCode/reports/rag_experiments/s604_passage_evidence_v2/retrieval.json `
  --method predicted_category_rerank `
  --output_dir SystemCode/reports/rag_experiments/new_reasoning_inputs
```

生成 `cases.json` 和全待审定的 `labels.json`。50 条原查询对应 50 条输入，不自动标记为合规，不自动制造 50 条负例。输入保留完整父段落；当前没有额外截断。准备和运行都会校验原 PDF 哈希；运行还会验证检索记录哈希和输入内容未被替换。

## 提取和判断

```powershell
# 无网络的有限数值基线，用于检查链路，不代表 LLM 的能力
python SystemCode/evaluate_compliance_reasoning.py run `
  --cases SystemCode/reports/rag_experiments/new_reasoning_inputs/cases.json `
  --backend heuristic `
  --output_dir SystemCode/reports/rag_experiments/new_reasoning_offline

# 配置 DEEPSEEK_API_KEY 后实际调用 LLM
python SystemCode/evaluate_compliance_reasoning.py run `
  --cases SystemCode/reports/rag_experiments/new_reasoning_inputs/cases.json `
  --backend deepseek `
  --output_dir SystemCode/reports/rag_experiments/new_reasoning_deepseek
```

输出目录须为空或新建。DeepSeek 只使用 `DEEPSEEK_API_KEY`，不会把其他服务的密钥作为回退。`--cache_only` 仅允许读取与当前输入完全一致的新缓存，缺失则记录 ERROR，退出码为 2；`--cache 路径` 可指定缓存。缓存键包括契约版本、完整提示词、服务地址、模型和原始输入。旧实验缓存已迁出当前目录，新缓存不加入 Git。

字段提取要求：

- 招标阈值必须附条款原文；投标事实必须附 `field + evidence_id + quote`。
- 引文须存在于对应输入段落，数字和文本事实还会进行字面核验；文件身份及页码由程序映射，模型不能自行补页码。
- 数值保留原文单位，不猜测单位或根据目标阈值修正数值。第二数值约束须与主数值使用同一单位；原文不能支持时保持缺失。
- 不明确的布尔状态使用 `null`。纯文本输入不能验证实际图片上的印章或签字，相关状态保持未知。
- 缺少证据、缺少约束、引用错误、矛盾都不能填成默认通过。

程序只能检查引用确实存在及字面值一致，不能替代语义审定、主体归属审定或原件真实性审核。引用一个真实片段仍可能解释错误，需要后续人工标注评测。

## 规则输出

| 状态 | 含义 |
| --- | --- |
| COMPLIANT | 当前已支持的规则中，所需事实和约束齐全且满足比较 |
| NON_COMPLIANT | 有明确事实支持未满足约束 |
| INSUFFICIENT_EVIDENCE | 缺证据、缺阈值、缺字段或引用校验失败，无法判断 |
| CONFLICTING_EVIDENCE | 发现待核实的冲突 |
| UNSUPPORTED_RULE | 规则类型、资质体系或运算条件当前不支持 |
| SCORED | 按条款中的评分档位计算得分，零分不等于不合规 |
| ERROR | 接口、缓存或格式异常；不能当成正确判断 |

当前规则覆盖明确数值、可识别资质等级、两个实体/金额一致性、材料及签章状态、禁止事项和承诺、明确评分档位及已提取的定性响应。复杂多条件条款仍需拆分与独立验证；不能把当前有限规则集视为完整审标系统。

## 结论标签与指标

检索参考证据标签不能自动成为合规结论标签。人工需在 `labels.json` 中填写 `review_status=human_verified`、`reviewer`、`review_note`、`expected_verdict`，然后通过 `run --labels 路径` 计分。标签绑定 cases 文件哈希，ID 必须完全一致。

没有人工结论标签时 `verdict_accuracy=null`。`decision_coverage` 只表示多少条得到明确符合/不符合判断，不能当准确率。错误和弃判不会被计为正确的负例；评分数值准确率还需要额外评分标签，当前不报告。

本轮已用 S604 的 50 条实际检索记录跑通离线数值基线。该基线仅支持少数显式数值模式，不能据其覆盖率判断 RAG 或 DeepSeek 的准确率。当前环境未配置 DeepSeek 密钥，在线模型效果尚未重新测量。旧实验的 99% 不适用于此流程。
