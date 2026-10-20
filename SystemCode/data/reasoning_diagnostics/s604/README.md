# S604 下游流程诊断参考

这里复用 RAG 的 50 个需求编号定位流程问题。**这些文件不是 RAG 标注，也不是正式抽取或规则评测金标准。**

- `compliance_verdict_labels.json`：历史上传的助手结论与评分参考。
- `compliance_calibrated_reference.json`：后续核对原件形成的助手校准参考。

两份文件从 `data/evidence_annotations/s604/` 迁入，内容和哈希不变；不自动升级为人工标注。原文件内部的 `evidence_annotations_file: annotations.json` 是旧目录下的相对定位，现对应 `../../evidence_annotations/s604/annotations.json`，保留该历史字段以保持原始快照。

仅在明确需要探索性比较时向流程诊断或离线回放传入 `--reference_labels`，使用校准参考。比较输出单独存放，不作为 RAG 或规则准确率。

原 RAG 证据标注继续保存在 [检索标注目录](../../evidence_annotations/s604/README.md)。评测分工见 [总览](../../../../docs/evaluation_layout.md)。
