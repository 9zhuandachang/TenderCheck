# TenderCheck Evolution Roadmap (`ROADMAP.md`)

本文件是 TenderCheck 唯一的问题跟踪与演进路线图（原地更新，不新建副本）。
**核心目标**：① 真正服务家里工程招投标审标实战（零假放行、跨行业可用、勘误可落地）；② 打造干净、可一键复现的高质量 GitHub 开源项目；③ 沉淀扎实的跨项目泛化指标支撑竞赛与展示。

---

## Phase 1：止血保真与开源复现基线（工程规范收口）

- [x] **移除伪验证与假通过兜底**：重写 `reevaluate_clause_with_modified_bid`（保留关联/冲突证据并走真实抽取与逐字验真）；删除 `_offline_chinese_slot_fallback` 并替换为保守弃权 `_strict_conservative_fallback`；禁止 `global_context.py` 无原文依据拷贝 `expected_numeric`；修复无截止日证书被凭批准日期误判有效的问题（由 `tests/test_anti_false_pass_guards.py` 守护）。
- [x] **修复工作台状态机与签发门禁**：修复 `service.confirm_result` 篡改违规结论的 Bug；新增受控人工放行 `approve_manual_pass`（强制填写核验理由并区分 `Reviewer` / `Manager` 权限）；将 `CONFLICTING_EVIDENCE` 映射为红色 `Non-compliant`；加固 `signoff_blockers` 拦截带病签发；按 `project_id` 隔离修订记录。
- [x] **建立防乱与文档治理底座**：落地 `AGENTS.md`、`docs/{DECISIONS,ROADMAP,EXPERIMENTS}.md`、`.gitignore` 隔离规则及仓库卫生自动化单测。
- [x] **对齐 `README.md`、CLI 参数、依赖环境与遗留前端单测**：
  - 已修复 `train_binary_filter.py`、`train_classifier.py`（支持 `--eval_only` 与已导出 JSONL 加载）、`evaluate_extraction.py`（支持 `--hydrate-global-context` 与全量 50 条默认集）、`evaluate_rule_engine.py`（锚定 `BASE_DIR` 并取消默认写入未授权 `.md`）、`replay_compliance_responses.py`（兼容连字符与下划线参数）；
  - 已统一 `README.md` 与 `EXPERIMENTS.md` 的样本数统计口径（`44,668` 条分类语料 / `69` 例独立规则验证集），并在 `requirements.txt` 补齐 `rapidocr-onnxruntime`；
  - 已修复 `ui.py` 已有项目打开被强制拦截在新建页的会话状态 Bug，并同步更新 `test_review_app.py`、`test_home_navigation.py`、`test_s604_frontend_integration.py`，全套单元测试 100% 通过。

---

## Phase 2：去硬编码、跨行业泛化与管线统一（实战可用核心）

- [x] **2.1 剥离 S604 / 安徽省 / 公路单一行业硬编码 + 联合体安全门禁**：
  - **`R06` 多年度与单等级信用评分参数化**（`src/rules/rule_engine.py`、`src/domain_profiles/`）：移除写死的 `{2019, 2020, 2021}` 年份与固定赋分表，支持显式年份、相对“近 N 年”（基于 `review_reference_date`）及水利/市政单等级（`AAA/AA/A`）动态计分；
  - **多行业配置包化（Domain Profiles）**：外置 `src/domain_profiles/profiles.json`，覆盖**水利水电、市政公用、房屋建筑、公路工程**四大领域资质偏序（含跨行业资质专业类别错配拦截）、相关专业词表、全国各省交安/建安/水安 `A/B/C` 证、五大员/八大员岗位证及造价师等级；
  - **收紧 `global_context.py` 宽泛承诺兜底 & 联合体安全路由**：仅对通用响应性条款允许《商务投标函》响应回填，杜绝漏检缺失的专项承诺；新增实质性《联合体协议书》识别（排除空白或“无”占位），对禁联合体项目判 `NON_COMPLIANT`，对允许联合体项目的主体归属条款安全转交人工复核（由 `tests/test_domain_generalization.py` 守护）。
- [x] **2.2 补齐复合条款“原子化拆分”与切分器边界修复**：
  - 已修复 `tender_segmenter.py` 中 `last_table_col0/col1` 跨独立表格/跨章节不重置导致的表头串行污染，同时保留合法的跨页同列表格续行继承；
  - 已为 `auto_annotator.py` 主招标文件与混合扫描 PDF 补充按页检测与缓存 OCR 兜底（不再静默输出 0 条）；
  - 已新增确定性复合句拆分器 `src/extraction/atomic_decomposer.py` 并接入 `AutoTenderAnnotator`，将多合一并列要求拆解为独立单约束原子条款（严格保留条件句、`OR` 替代选项、评分档位阶梯及单项目复合业绩，并由 `tests/test_segmenter_and_decomposer.py` 守护）。
- [x] **2.3 打通多模态印章与签字证据链 & 合并离线/在线 RAG 双轨代码**：
  - 已在 `BidChunk`、`BidEvidenceSlots` 与 `evidence.py` 建立严格的 `visual_seal_verified` 视觉溯源通道：彻底移除文本占位符（如“（盖单位章）”）假造签章的隐患，仅允许经视觉引擎实检的 `has_official_seal` / `seal_company_name` / `has_signature` 传入 `R04`（含印章单位与投标人主体一致性校验）并在前端审查卡片独立展示；
  - 已将 `chinese_pipeline.py` 的 PDF 解析与混合检索完全收口至 `src/rag/parser.py`（`BidDocumentParser`）与 `src/rag/indexer.py`（`HybridBidIndex` + `resolve_local_or_hf_model`），消除双轨重复实现（由 `tests/test_visual_seal_and_unified_rag.py` 守护）。
- [x] **2.4 实现中文补遗/答疑条款自动覆盖（Addendum Superseding）**：
  - 已新增确定性补遗覆盖引擎 `src/extraction/addendum_superseder.py` 并接入 `AutoTenderAnnotator.annotate_pdfs`：支持修改（`MODIFY`）、删除/取消（`DELETE`）、新增补充（`SUPPLEMENT`）三类中文补遗指令，通过条款号 + 规范主题 + 原值提示 + 章节边界门禁实现多轮链式覆盖（如第 2 号补遗覆盖第 1 号补遗）；
  - 被替代或取消的原条款保留完整审计溯源（`lifecycle_status` / `superseded_by`），匹配模糊的补遗绝不静默删旧，自动标记交人工复核（由 `tests/test_addendum_superseder.py` 守护）。

---

## Phase 3：实战提效与竞赛亮点打磨（差异化竞争力）

- [x] **3.1 跨条款影响图与联动重审（Impact-Aware Re-evaluation）**：
  - 已新增 `src/rules/impact_graph.py`（`ClauseImpactGraph`），建立 `(document, page_no) -> [关联结果]` 反向索引（兼容 `evidence` 与 `chain.backend_state.evidence_context` 及路径/文档 ID 别名映射）；
  - 已接入 `service.preview_page_impact`、`service.apply_remediation_edit` 与 `views/remediation.py`：在整改页修改任一投标页面后，自动联动重审所有引用该页的关联条款；若原有合规条款发生回归（如改工期误改投标有效期），自动将其标为 `TO_REMEDIATE`、生成/重开整改项并写入 `impact_reevaluate_clause` 审计日志（由 `tests/test_impact_graph_reeval.py` 守护）。
- [ ] **3.2 清理早期英文 Demo 遗留 & 非公路真实标书跨行业实测**：
  - 清理早期英文占位遗留代码（`tcui/engine.py`、`demo.py`）；
  - 在 `data_extracted/` 选取至少 1–2 套非公路/近年真实项目（水利水电或市政 EPC）完成端到端评测，将跨行业泛化结果记入 `docs/EXPERIMENTS.md` 与 `README.md`。
- [ ] **（可选延后）多人协作勘误单导出与工程量清单（BoQ）逐行比对**：
  - 整改页行内 Diff + 独立《标书修改勘误单》导出（单人自用阶段暂不投入）；
  - 独立工程量清单与控制价表格级逐行核对（子目编码、项目特征、计量单位、工程量一致性及合价算术复核）。
