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
- [ ] **2.2 补齐复合条款“原子化拆分”与切分器边界修复**：
  - 修复 `tender_segmenter.py` 中 `last_table_col0/col1` 跨表格不重置导致的表头串行污染；
  - 为 `auto_annotator.py` 主招标文件补充扫描件页 OCR 兜底（避免纯扫描主招标书静默输出 0 条）；
  - 在 Step 2 后增加复合并列条款（如单句同时要求法人资格 + 施工资质 + 安许证）的原子化拆分机制，解决下游单槽位 `TenderConstraintSlots` 漏检并列子要求的问题。
- [ ] **打通多模态印章与签字证据链**：
  - 修复 `evidence.py` 序列化时丢弃并清空 `has_official_seal` / `seal_company_name` / `has_signature` 的断路问题，将视觉层（红章像素占比 / VLM 识别）提取的印章元数据作为带置信度的视觉辅助信号传递至 `R04` 与前端审查卡片。
- [ ] **合并离线（`src/rag/`）与在线（`chinese_pipeline.py`）RAG 双轨代码**：
  - 消除重复的切块与检索封装，统一复用支持跨页表格连续性与本地/HuggingFace 自动权重解析的标准 `src/rag/` 组件。
- [ ] **实现中文补遗/答疑条款自动覆盖（Addendum Superseding）**：
  - 识别补遗中“原招标文件第 X 章/条修改为……”的指向关系，自动将原条款标记为被替代并以补遗新约束入审，避免新旧冲突条款同时生效。

---

## Phase 3：实战提效与竞赛亮点打磨（差异化竞争力）

- [ ] **跨条款影响图与联动重审（Impact-Aware Re-evaluation）**：
  - 建立 `(document_id, page_no) -> [关联 requirement_ids]` 反向索引；在 `Remediation` 修改投标函等核心页面（如改动报价、工期、项目经理）后，自动重算所有引用该页的关联条款（如 `R03` 跨表一致性），防止“修好一条、改坏三条”。
- [ ] **《标书修改勘误单》导出与双栏行内 Diff 体验**：
  - 为 `ui.py` 的 PDF 页面光栅化渲染增加 `@st.cache_data` 缓存消除翻页卡顿；
  - 在整改界面展示清晰的增删高亮对比（Inline Diff），并支持一键导出面向造价/商务标编制人员的 Word/PDF《标书修改勘误单》（含分册、页码、修改前原文、修改后建议文本）。
- [ ] **工程量清单（BoQ）结构化专项比对（可选高价值扩展）**：
  - 针对独立工程量清单与控制价文件，新增表格级结构化核对模块（子目编码、项目特征、计量单位、工程量一致性及合价算术复核）。
- [ ] **规则插件化（Rule Registry）与多项目泛化基准发布**：
  - 将每条规则的 Schema、Prompt 片段、Grounding 校验与符号评估解耦为模块化插件，清理早期英文占位遗留代码（`tcui/engine.py`、`demo.py`）；
  - 在 `data_extracted/` 选取至少 1–2 套非公路/近年真实项目（水利水电或市政 EPC）完成端到端评测，将跨行业泛化结果记入 `docs/EXPERIMENTS.md`。
