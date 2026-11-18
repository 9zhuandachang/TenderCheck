# Architecture & Domain Decision Records (`DECISIONS.md`)

本文件记录 TenderCheck 演进过程中的**关键架构决策与业务建模取舍（ADR）**。
- **维护原则**：只记录 **Why（背景痛点）** 与 **Trade-off（方案取舍及放弃原因）**，不重复抄写代码细节；本文件采用**原地演进**，若后续推翻旧决策，将旧条目状态更新为 `Superseded by ADR-XXX` 并追加新条目，严禁另建零散决策文档。

---

### ADR-001：采用“神经感知抽取 + 逐字引用验真 + 确定性符号规则”解耦架构 (2026-10)
- **状态**：已采纳（核心基石）
- **背景**：工程招投标文件动辄数百至上千页，任何一处废标红线漏判或误判代价极高。端到端大模型直接判定合规性存在三大硬伤：跨表算术/大写金额换算幻觉、主体张冠李戴（如把分包或历史业绩人员当成拟任项目经理）、结论缺乏精确到页码与原文字句的审计证据。
- **决策**：
  1. 神经网络与 LLM（`BinaryRequirementFilter` + `chinese-roberta-wwm-ext` + `DeepSeek-V3`）仅负责条款切分分类、混合检索及受限 Schema 槽位抽取（`passage-facts-v11-arch`），禁止直接输出合规裁决或换算单位；
  2. `ground_extraction` 对所有抽取出的数值、单位、主体归属绑定执行 NFKC 归一化后的逐字原文子串与 `Decimal` 精确核验；
  3. `ComplianceRuleEngine`（`R01`–`R09`）用纯确定性 Python 逻辑完成算术比较、资质偏序核验、效期比对与公式计算。
- **放弃方案**：Prompt 工程驱动的端到端 RAG + LLM 直接输出 `Pass/Fail`（原因：无法杜绝数值幻觉，且在无把握证据上倾向于迎合式“假阳性放行”）。
- **影响范围**：`SystemCode/src/extraction/`、`SystemCode/src/rules/`

---

### ADR-002：三值合规逻辑与严格“零假放行（Zero False-Pass）”安全门禁 (2026-11)
- **状态**：已采纳（已通过单测固化）
- **背景**：在真实审标业务中，把违规项误判为“合规”（False Pass）会导致直接废标，危害远大于“提示人工复核”。项目初期在线 UI 曾存在离线启发式兜底（`_offline_chinese_slot_fallback`）捏造默认槽位、整改重审未调真实抽取、以及点击 `Confirm` 将违规项静默改为 `COMPLIANT` 等破绽。
- **决策**：
  1. 引擎严格实行 Kleene 三值逻辑：`COMPLIANT`（证据确凿且满足规则）、`NON_COMPLIANT`（明确超限或双侧实锤自相矛盾 `CONFLICTING_EVIDENCE`）、`INSUFFICIENT_EVIDENCE`（检索未命中、扫描件印章待目视、外部政务网防自证拦截、主观技术评分、或 API/抽取异常）；
  2. 移除所有伪造槽位兜底，无 API Key 且非显式逐字数值比对时一律安全弃权为 `INSUFFICIENT_EVIDENCE`；
  3. UI 层的 `Confirm` 仅确认人工已阅并保留原 `verdict`；人工强行放行必须走独立 `approve_manual_pass` 接口（强制填写 ≥4 字核验依据并记入审计表，确定性违规仅限 `Manager` 角色特批），且存在未闭环缺陷时硬性拦截报告签发（Sign-off）。
- **放弃方案**：在无 Key 或检索缺失时使用关键词匹配自动给绿灯以美化演示效果（原因：彻底违背审标系统的工程诚信与实际业务可用性）。
- **影响范围**：`src/app/tcui/real_backend.py`、`src/app/tcui/service.py`、`tests/test_anti_false_pass_guards.py`

---

### ADR-003：多卷本投标文件采用父子块混合检索（Parent-Child Hybrid RAG）+ 类别路由 (2026-10)
- **状态**：已采纳（待在 Phase 2 完成离线与在线管线完全合流）
- **背景**：工程标书同时包含精确编号/金额（如 `GB50204`、`第100章`、身份证/注册证号）与长段落技术叙述，且表格跨列信息容易在固定窗口切块中丢失表头上下文。
- **决策**：
  1. 解析阶段保留双层粒度：小粒度子块（约 300 字文本片段或单行键值化表格记录）用于高召回检索，命中后映射回完整父块（约 900 字章节或完整 Markdown 表）送入抽取器；
  2. 检索阶段融合零依赖字词/编号 `OkapiBM25` + `BAAI/bge-m3` 稠密检索（RRF 融合）+ `BAAI/bge-reranker-v2-m3` 交叉编码器精排，并依据 Step 2 的 8 类需求标签施加分册过滤与权重偏置（`category_search_policy`）。
- **放弃方案**：单一固定长度纯向量切块检索（原因：在无 Reranker 消融实验中，纯向量或未精排混合检索容易被技术标大段套话稀释商务/资信核心证照页）。
- **影响范围**：`SystemCode/src/rag/`、`SystemCode/src/extraction/chinese_pipeline.py`

---

### ADR-004：建立定额文档白名单、去硬编码规范与跨项目验证门禁 (2026-11-18)
- **状态**：已采纳
- **背景**：项目从课程作业迈向“协助真实工程投标业务 + GitHub 代表作 + 赛事参赛”阶段。需要彻底根治早期遗留的两大痛点：① 核心代码针对单一项目（S604 安徽公路项目）的年份/省份/章节名硬编码；② 多次迭代对话产生大量零散 `.md` 总结与临时脚本污染仓库。
- **决策**：
  1. 设立根级 `AGENTS.md` 规范 AI 与开发者行为；仓库内 `.md` 文件实行严格白名单定额制，所有架构决策、路线图、实验台账统一在 `docs/{DECISIONS,ROADMAP,EXPERIMENTS}.md` 内**原地更新**，由单元测试自动巡检拦截违规文件；
  2. 临时脚本限入 `scratch/`，原始大体积实验输出限入 `SystemCode/reports/runs/`（均由 `.gitignore` 隔离）；
  3. 后续规则与全局锚点重构统一采用**参数化槽位 + 行业配置包（Domain Profiles）**，并以“公路 + 非公路（水利/市政）”双项目验证作为泛化验收标准。
- **放弃方案**：每次功能迭代在仓库内新建独立设计文档或保留多版本脚本 `_v2.py`（原因：导致真相源分裂、维护成本剧增，降低开源与参赛评审观感）。
- **影响范围**：全仓库目录结构、`AGENTS.md`、`docs/`、`tests/test_anti_false_pass_guards.py`
