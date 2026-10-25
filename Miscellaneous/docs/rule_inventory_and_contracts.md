# S604 招投标合规与评分规则清单、输入契约及判断逻辑规范（Step 1 & Step 2 验收文档）

更新日期：2026-10-20
适用基准项目：`2022/11.11-S604皖南交旅融合（飞毕路美丽公路）项目`（评审基准日／投标截止日：`2022-11-11`）
关联机器可读映射：[`SystemCode/data/rule_eval/s604_clause_rule_mapping.json`](../SystemCode/data/rule_eval/s604_clause_rule_mapping.json)
关联独立验证集：[`SystemCode/data/rule_eval/s604_rule_validation_suite.json`](../SystemCode/data/rule_eval/s604_rule_validation_suite.json)
关联独立评测报告：[`docs/experiments/s604_rule_engine_validation.md`](experiments/s604_rule_engine_validation.md)

---

## 第一部分：规则清单与招标要求映射（Step 1）

### 1.1 梳理范围与总体分布

本清单基于已完成的 **S604 项目全量招标文件标注**（[`SystemCode/data/holdout_eval/2022__11.11annotations.json`](../SystemCode/data/holdout_eval/2022__11.11annotations.json)，共 **359 条**要求 `T0001`–`T0359`，涵盖招标文件正文第一章至第九章及答疑澄清文件）以及 **50 条 RAG 证据标注子集**（[`SystemCode/data/evidence_annotations/s604/annotations.json`](../SystemCode/data/evidence_annotations/s604/annotations.json)）逐条梳理。

> **核心原则**：八分类结果（`Document` 154、`Commercial` 63、`Qualification` 59、`Technical` 20、`Consistency` 18、`Scoring` 18、`Integrity` 17、`Schedule` 10）仅用于组织招标需求与引导检索，**不代替具体形式化规则**。

全量 359 条招标要求按可形式化边界划分为：
- **可形式化确定性规则（9 大规则族 `R01`–`R09`）**：共 **308 条**（占 85.79%）；
- **不可形式化／转交人工复核或标后合同执行条款（4 类 `HUMAN_REVIEW_*`）**：共 **51 条**（占 14.21%）。

### 1.2 九大确定性规则族清单与差距分析表

| 规则编号 | 规则名称 | 全量条数 (359条) | 50子集条数 | 招标依据代表条款（S604） | 适用范围与实际检查条件 | 原有实现状态 | 原有缺口 (`Gaps`) | 本轮完善后状态 |
| --- | --- | ---: | ---: | --- | --- | --- | --- | --- |
| **`R01_NUMERIC_BOUND`** | 数值边界与阈值核验 | 24 | 8 | `T0001`、`T0002`、`T0023`、`T0027`、`T0037`、`T0070`、`T0101`、`T0135`、`T0204` | 工期上限（$\le 360$日历天）、投标有效期（$=90$日）、报价上限（$\le 115,046,243.64$元）、暂列金固定值（$=8,000,000.00$元）、保证金金额（$=50$万元）、社保连续月数（$\ge 6$个月）、无行贿追溯年限（$\ge 3$年） | 已支持基本单值与双值比较 | 缺少`个月`/`年`/`分`单位；单句缺上下文阈值（如`T0135`/`T0204`）需严禁模型猜测 | 已补全单位体系与双值校验，缺阈值或单位不兼容严格返回 `INSUFFICIENT_EVIDENCE` |
| **`R02_QUALIFICATION_HIERARCHY`** | 资质等级、专业方向、主体角色与**证书时效**核验 | 15 | 6 | `T0003`、`T0004`、`T0005`、`T0007`、`T0008`、`T0009`、`T0015`、`T0061`–`T0063`、`T0105` | 企业总承包资质等级、独立法人营业执照、安全生产许可证、一级建造师、交安B证、总工职称等级与相关专业、证书聘用单位及**评审基准日证书有效性／延期批复** | 仅识别总承包/建造师/职称3类，仅当原文含“有效”二字才查布尔值 | **核心缺口**：① `T0004`不含“有效”字样时仅凭《基本情况表》等级通过，忽略证书2021-12-31过期需核对住建部延续通知；② 未识别安许证、交安B/C证、营业执照；③ 未支持公路工程相关专业集合匹配 | **已全面升级**：支持10类证书等级、公路相关专业白名单、持证人岗位与单位核验、对照`review_reference_date`比较`valid_to`/`extension_valid_to`，拦截自填表假阳性 |
| **`R03_RMB_UPPER_AND_CONSISTENCY`** (`R05_CONSISTENCY`) | 跨表一致性与大写金额精确转换核验 | 24 | 3 | `T0069`、`T0074`、`T0106`、`T0107`、`T0127`、`T0188`、`T0194`、`T0202` | 投标函大写与小写金额分角级精确转换比对、投标函与固化清单汇总表跨表报价相等、投标人名称与证照/交通部名录一致、建造师注册单位与投标人名称一致 | 已支持大写转换及双实体字符串比对 | 需确保跨表比对必须来自两个独立引用源，且全半角括号规范化一致 | 已完善独立双源校验与符号级分角精度核对 |
| **`R04_DOCUMENT_AND_SEAL`** | 文件组成、实质正文与**多方印章/签字主体**核验 | 127 | 6 | `T0012`、`T0013`、`T0014`、`T0112`、`T0113`、`T0118`、`T0218`、`T0294`、`T0352` | 商务/技术/报价文件必备材料存在性、实质正文特征（非目录/空模板）、法定代表人/单位电子章、本人手写签名、造价师执业章、**交竣工验收证明四方盖章（建设/设计/监理/施工）** | 仅支持单一布尔 `has_official_seal` 和 `has_signature` | 无法验证 `T0014`/`T0218` 要求的“建设、设计、监理、施工四方盖章”中是否漏盖某一方；项目经理业绩合同未支持业主变更批复关联 | **已升级**：新增 `required_seal_parties` 与 `observed_seal_parties` 集合包含校验，并在 `evidence.py` 支持项目经理变更批复关联 |
| **`R05_PROHIBITION_AND_COMMITMENT`** (`R03_COMMITMENT`) | 实质性响应承诺、否定性禁令与**外部核查边界** | 81 | 11 | `T0010`、`T0016`、`T0018`、`T0024`、`T0033`、`T0034`、`T0040`、`T0054`、`T0056`、`T0057`、`T0099`、`T0137` | 联合体禁令、分包禁令、不撤标承诺、增值税响应、在岗撤离双路径承诺（无在建 `OR` 有在建但承诺中标后变更）、失信/行贿/挂靠/弄虚作假禁令 | 仅检查 `prohibition_violated` 和 `commitment_fulfilled` 布尔值 | 将投标人《诚信承诺书》自述直接等同于外部无失信/无行贿/无挂靠事实已核实（`T0018`/`T0040`/`T0054`/`T0099`） | **已升级**：新增 `requires_external_verification` 与 `evidence_source_type` 校验，仅凭自述或承诺函时返回 `INSUFFICIENT_EVIDENCE` |
| **`R06_QUALITATIVE_AND_SCORING`** | 客观质量/安全目标、区间分档计分与**多年度信用评分** | 6 | 7 | `T0028`、`T0030`、`T0214`、`T0220`、`T0231` | 交工/竣工质量合格目标、无生产安全责任事故目标、资产负债率四档区间计分（`<75%`得6分，`[75%,80%]`得4分，`(80%,85%]`得2分，`>85%`得0分）、2019–2021三年公路信用等级（省厅优先、部级兜底）计分 | 已支持单指标 `scoring_bands` 与质量/安全目标 | 未支持 `T0220` 跨年度（2019/2020/2021）省厅优先于交通部的信用等级合并计分逻辑；主观技术评分需显式分流 | **已升级**：新增 `CREDIT_MULTI_YEAR` 多年度省部优先级计分及 `requires_human_review` 显式路由；明确 0 分返回 `SCORED(0.0)` 而非不合规 |
| **`R07_STAFFING_COUNT_AND_QUALIFICATION`** | **多人岗位配备数量与逐人资格核验（新增）** | 9 | 3 | `T0067`、`T0072`、`T0078`–`T0084` | 附录5/附录6岗位配备：项目经理1人、总工1人、道路工程师1人、桥梁工程师1人、合同工程师1人、专职安全员1人（省级交通主管部门C证）、测量1人、试验1人、施工员1人 | **无独立规则**（原 `R02` 仅能单人单证比较，无法统计岗位人数或防止跨人拼凑资格） | 缺少按岗位汇总人数、区分“至少 $N$ 人合格 (`AT_LEAST_N_QUALIFIED`)”与“拟派每人均合格 (`ALL_PROPOSED_QUALIFIED`)”、阻断张冠李戴拼接不同人职称/证书 | **全新实现 `R07`**：对 `personnel_records` 按姓名去重并逐人绑定核验角色、等级、专业、证书有效期、单位与社保月数 |
| **`R08_PERFORMANCE_CHAIN_AND_SCORING`** | **历史业绩复合条件、材料链、变更批复与多项目计分（新增）** | 15 | 5 | `T0011`–`T0014`、`T0161`、`T0162`、`T0209`–`T0212`、`T0216`–`T0219`、`T0359` | 时间窗（`2017-01-01`至投标截止日交竣工）`AND` 性质（新建/改建/扩建）`AND` 等级（$\ge$三级公路）`AND` 规模（长度$\ge 15$km `OR` 金额$\ge 5000$万元；`T0210`为$>15$km）`AND` 内容（路基`AND`路面`AND`桥涵）`AND` 人员归属（合同本人 `OR` 业主变更批复+交工验收本人）`AND` 证明材料链与四方印章；以及6分基础分/`4分×N`（上限12分）累加计分 | **无独立规则** | 原引擎无法同时校验单项目的8个复合维度，无法验证“合同原经理向阳 $\rightarrow$ 业主批复变更为王文年”的证据链，也无法对多项目去重累加计分 | **全新实现 `R08`**：完整支持项目级复合条件、人员变更批复闭环、材料链与四方章校验，以及 `BASE_QUAL_SCORE` / `SINGLE_PROJECT_BONUS` / `PER_PROJECT_ADDITIVE` 三种计分模式 |
| **`R09_FORMULA_BOUND`** | **多字段公式与净基数扣减核算（新增）** | 7 | 2 | `T0049`、`T0050`、`T0059`、`T0086`、`T0133`、`T0184`、`T0250` | `T0059`/`T0250` 安全生产费 $\ge (\text{第100-700章合计} - \text{安全生产费} - \text{建筑工程一切险} - \text{第三者责任险}) \times 1.5\%$；`T0049` 履约担保 $= \text{中标价} \times 10\%$ | **无独立规则**（原 `R01` 仅支持单数值直接比较） | 无法从分项金额字典计算扣除安全生产费及两项保险费后的净基数与比例下限 | **全新实现 `R09`**：支持 `NET_BASE_RATIO` 与 `GROSS_RATIO` 精确 `Decimal` 分角核算，缺任一扣减分项严格返回 `INSUFFICIENT_EVIDENCE` |

---

### 1.3 复合条件拆分与不可形式化人工分流边界

#### （1）典型复合条件逻辑拆分
1. **项目经理／总工历史业绩条件（`T0011`、`T0209`、`T0210`、`T0212`、`T0359`）**：
   $$\text{ValidProject}(p) \iff \text{DateWindow}(p) \land \text{Type}(p) \in \{\text{新建,改建,扩建}\} \land \text{Grade}(p) \ge \text{三级公路} \land \big(\text{Length}(p) \ge 15\text{km} \lor \text{Amount}(p) \ge 5000\text{万元}\big) \land \{\text{路基,路面,桥涵}\} \subseteq \text{Scopes}(p) \land \text{RoleLink}(p, \text{ProposedPerson}) \land \text{ProofChain}(p)$$
   其中人员归属链 $\text{RoleLink}(p, u)$ 拆分为：
   $$\text{RoleLink}(p, u) \iff \text{PerformedRole}(p) = \text{RequiredRole} \land \Big(\big(\text{ContractPerson}(p) = u \land \text{HandoverPerson}(p) \in \{\varnothing, u\}\big) \lor \big(\text{RoleChangeApproved}(p) = \text{True} \land \text{HandoverPerson}(p) = u\big)\Big)$$
   > **注**：针对 `T0212`，答疑澄清文件第1项（`T0359`）已将项目总工加分业绩的担任身份修正为必须以**项目总工**身份完成，规则引擎通过 `required_role = "chief_engineer"` 精确执行。
2. **项目经理在岗与撤离承诺条件（`T0010`、`T0055`、`T0056`、`T0057`、`T0077`、`T0276`、`T0277`）**：
   - 合格路径 A（格式一）：当前未在其他项目担任项目经理（`no_active_project == True`）；
   - 合格路径 B（格式二）：当前已在其他项目担任项目经理，但在投标函中明确承诺本项目中标后合同签订前能够从原项目变更撤离至本项目全面履约（`commit_withdrawal_on_win == True`）；
   - **冲突与诚信红线（`T0057`）**：若同一投标文件一处声明现任其他项目经理（格式二），另一处（如诚信承诺书 `T0284`）又声明拟派项目经理无在建工程，触发双侧冲突 `CONFLICTING_EVIDENCE`，交由评标委员会核实是否构成虚假承诺。
3. **安全生产费净基数公式条件（`T0059`、`T0250`）**：
   $$\text{NetBase} = \text{Total}_{100..700} - \text{SafetyFee} - \text{AllRisksInsurance} - \text{ThirdPartyInsurance}$$
   $$\text{SafetyFee} \ge \text{round}(\text{NetBase} \times 0.015, 2)$$

#### （2）不可形式化条款的人工分流清单（共 51 条）
以下 4 类条款无法仅靠文本符号规则作确定性合规判定，系统将其明确标记为 `formalizable = False`（在规则引擎中路由为 `UNSUPPORTED_RULE` 或 `INSUFFICIENT_EVIDENCE`），严禁自动放行：
1. **主观技术评审打分（`HUMAN_REVIEW_SUBJECTIVE_SCORING`，9 条：`T0222`–`T0230`）**：
   - 涉及施工组织设计总体布置、施工方案、工期/质量/安全/环保水保/文明施工与文物保护/风险预案/资源计划的“优良/一般/差”定性分档打分。
   - **分流方式**：对应章节是否编制的客观存在性由 `T0330`–`T0339`（归入 `R04_DOCUMENT_AND_SEAL`）核验；章节内容质量等级（优良/一般/差）明确交由人工评委打分，排除在确定性规则准确率分母之外。
2. **版式字体视觉与笔迹鉴定（`HUMAN_REVIEW_VISUAL_TYPOGRAPHY`，8 条：`T0022`、`T0043`–`T0047`、`T0189`、`T0195`）**：
   - 涉及一级建造师手写签名与签名图像笔迹一致性鉴定（`T0022`）、技术暗标三号/四号/五号宋体不加粗排版检测（`T0043`–`T0047`）及扫描件字迹清晰度主观判断（`T0189`、`T0195`）。
3. **外部事实调查与串标造假认定（`HUMAN_REVIEW_EXTERNAL_INVESTIGATION`，10 条：`T0235`、`T0238`–`T0245`、`T0346`）**：
   - 涉及投标报价是否低于企业个别成本（`T0235`、`T0346`）及是否实际存在租借资质、伪造变造证件、财务/业绩/劳务关系造假（`T0238`–`T0245`）。文件内的书面声明由对应承诺条款（如 `T0347`）核验，外部真实性调查由人工结合主管部门平台核查。
4. **标后合同执行与计量结算规则（`HUMAN_REVIEW_CONTRACT_SETTLEMENT_TERM`，24 条：`T0006`、`T0051`–`T0053`、`T0058`、`T0108`、`T0109`、`T0123`、`T0124`、`T0233`、`T0236`、`T0246`–`T0249`、`T0251`–`T0253`、`T0256`–`T0259`、`T0340`、`T0341`）**：
   - 属于合同通用/专用条款中关于“未填单价子目视为已分摊不另支付”“代理费/考勤费含入报价不单列”等标后结算约定，在投标阶段只要投标人未提出负偏离或违规单列项即不触发独立废标。

---

## 第二部分：规则输入契约、判断逻辑表与 Schema 审计（Step 2）

### 2.1 四类输入来源边界（严禁 LLM 猜测缺失上下文）

| 数据来源层级 | 对应对象／字段 | 来源说明 | 缺失时处理原则 |
| --- | --- | --- | --- |
| **1. 单条招标条款约束** | `TenderConstraintSlots` 基础字段（`expected_numeric`, `expected_unit`, `expected_text`, `required_domain` 等） | 直接从当前单条招标要求文本提取，且必须附 `source_quotes` 原文子串校验 | 若单句不含数值或运算符不明确，严禁从投标文件反推，保留 `None` 并由规则引擎返回 `INSUFFICIENT_EVIDENCE` |
| **2. 跨条款招标上下文** | `TenderConstraintSlots` 上下文注入字段（如 `T0135` 的最高限价 `115046243.64`、`T0204` 的暂列金 `8000000.00`、`T0209` 的项目经理最低业绩条件） | 来自招标文件前附表／招标公告的已核实全局配置，**不由单句 LLM 凭空补造** | 单句评测未注入跨条款上下文时，保持 `INSUFFICIENT_EVIDENCE` 并在诊断报告中单列 `requires_additional_tender_context` |
| **3. 项目级配置参数** | `review_reference_date`（S604 为 `"2022-11-11"`） | 来自招投标项目元数据（投标截止日／开标日），绝不使用运行当日系统时间代替历史项目评审时间 | 当规则需要核验具体证书有效期或业绩截止日而 `review_reference_date` 为空时，返回 `INSUFFICIENT_EVIDENCE` |
| **4. 投标文件证据事实** | `BidEvidenceSlots` 及其嵌套列表（`personnel_records`, `performance_records`, `component_amounts`, `credit_ratings`） | 仅从检索命中的投标文件段落提取，每个非空事实必须附 `evidence_references`（含 `document_id`, `source_sha256`, `page_start`, `quote`） | 检索未命中、引文校验失败或关键槽位为 `None` 时，一律返回 `INSUFFICIENT_EVIDENCE`，**绝不默认通过 (`COMPLIANT`)，也不将证据不足直接当成违规 (`NON_COMPLIANT`)** |

---

### 2.2 各规则族输入契约与判断逻辑表（`R01`–`R09`）

#### `R01_NUMERIC_BOUND`（数值边界与阈值核验）
- **招标约束输入 (`TenderConstraintSlots`)**：
  - `constraint_type = "NUMERIC_BOUND"`, `operator` $\in \{\text{"<="}, \text{"<"}, \text{">="}, \text{">"}, \text{"=="}\}$
  - `expected_numeric: float`, `expected_unit: str`（必填，量纲：日历天/天/日、个月/月、年、元/万元、公里/米、%、人、分）
  - `secondary_numeric: Optional[float]`（可选，如暂列金 `8000000.00`）
- **投标事实输入 (`BidEvidenceSlots`)**：
  - `actual_numeric: float`, `actual_unit: str`, `secondary_actual_numeric: Optional[float]`
  - 必需引用：`actual_numeric` 与 `actual_unit` 必须绑定同一 `evidence_id` 的原文引用。
- **判断逻辑**：
  1. 若 `expected_numeric` 或 `actual_numeric` 为 `None` $\rightarrow$ `INSUFFICIENT_EVIDENCE`；
  2. 若 `expected_unit` 与 `actual_unit` 量纲不同或未知（如 `日历天` vs `个月`） $\rightarrow$ `INSUFFICIENT_EVIDENCE`（禁止跨量纲估算）；
  3. 换算为同量纲基准值（如 `万元` $\times 10000 \rightarrow$ `元`），使用高精度 `Decimal` 比较 `compare(actual, expected)` 及 `secondary_actual == secondary_expected`；
  4. 满足 $\rightarrow$ `COMPLIANT`，不满足 $\rightarrow$ `NON_COMPLIANT`。

#### `R02_QUALIFICATION_HIERARCHY`（资质等级、主体角色与证书时效核验）
- **招标约束输入 (`TenderConstraintSlots`)**：
  - `constraint_type = "QUALIFICATION_LEVEL"`, `expected_text: str`（必填），`required_domain: str`，`allowed_domains: List[str]`
  - `required_role: str`（如 `"project_manager"`, `"chief_engineer"`），`requires_employer_match: bool`
  - `review_reference_date: str`（如 `"2022-11-11"`），`requires_validity_date: bool`，`allow_extension_notice: bool`
- **投标事实输入 (`BidEvidenceSlots`)**：
  - `actual_text: str`, `actual_domain: str`, `certificate_holder: str`, `subject_role: str`
  - `valid_from: str`, `valid_to: str`, `extension_valid_to: str`, `is_long_term: Optional[bool]`, `certificate_valid: Optional[bool]`
  - `employer_matches_bidder: Optional[bool]`, `evidence_source_type: str`（`"CERTIFICATE_SCAN"` / `"OFFICIAL_QUERY_SCREENSHOT"` / `"SELF_DECLARED_TABLE"`）
- **判断逻辑**：
  1. **自填表拦截**：若 `evidence_source_type == "SELF_DECLARED_TABLE"`（仅引自《投标人基本情况表》而无证书原件/查询截图） $\rightarrow$ `INSUFFICIENT_EVIDENCE`；
  2. **资质体系与等级偏序**：解析 `(family, rank)`。若 `actual_rank < 0`（如分公司不具独立法人资格） $\rightarrow$ `NON_COMPLIANT`；若 `family` 不匹配或 `actual_rank < required_rank` $\rightarrow$ `NON_COMPLIANT`；
  3. **岗位与单位绑定**：若指定 `required_role`，核对 `subject_role`（不符 $\rightarrow$ `NON_COMPLIANT`，缺失 $\rightarrow$ `INSUFFICIENT_EVIDENCE`）；若 `requires_employer_match=True`，核对 `employer_matches_bidder`；
  4. **专业方向匹配**：对职称类（`title`）且要求“公路工程相关专业”时，允许 `HIGHWAY_RELATED_TITLE_DOMAINS`（公路工程、桥梁、公路与桥梁、路桥工程、道路与桥梁、交通土建、隧道、交通工程）；对企业总承包资质与注册建造师严格匹配专业名称；
  5. **证书时效与延期核验**：
     - 若配置了 `review_reference_date`：计算 $\text{effective\_valid\_to} = \max(\text{valid\_to}, \text{extension\_valid\_to})$。若 `valid_from > review_reference_date` 或 $\text{effective\_valid\_to} < \text{review\_reference\_date}$ $\rightarrow$ `NON_COMPLIANT`；若缺少具体有效期且 `requires_validity_date=True` $\rightarrow$ `INSUFFICIENT_EVIDENCE`。
     - 若提取了证书有效期但未配置 `review_reference_date` $\rightarrow$ `INSUFFICIENT_EVIDENCE`（禁止模型用当前年份或猜测日期核验）。

#### `R03_RMB_UPPER_AND_CONSISTENCY`（跨表一致性与大写金额核验）
- **输入契约**：`entity_primary: str`, `entity_secondary: str`（必须来自两个不同位置/表格的独立引用）；当 `target_field == "rmb_upper_consistency"` 时还需 `actual_numeric: float` 与 `upper_amount_text: str`。
- **判断逻辑**：缺失任一端 $\rightarrow$ `INSUFFICIENT_EVIDENCE`；调用 `number_to_rmb_upper(actual_numeric)` 与 `upper_amount_text` 进行规范化精确匹配，并校验 `Decimal(entity_primary) == Decimal(entity_secondary)`。

#### `R04_DOCUMENT_AND_SEAL`（文件组成、实质正文与多方印章核验）
- **输入契约**：`document_present: Optional[bool]`, `requires_official_seal: bool`, `requires_signature: bool`, `required_seal_parties: List[str]`, `observed_seal_parties: List[str]`, `has_official_seal: Optional[bool]`, `has_signature: Optional[bool]`。
- **判断逻辑**：
  1. `document_present is None` $\rightarrow$ `INSUFFICIENT_EVIDENCE`；`document_present is False` $\rightarrow$ `NON_COMPLIANT`；
  2. 若 `required_seal_parties` 非空（如 `["建设单位", "设计单位", "监理单位", "施工单位"]`）：若 `observed_seal_parties` 为空 $\rightarrow$ `INSUFFICIENT_EVIDENCE`；若存在未覆盖的印章主体 $\rightarrow$ `NON_COMPLIANT`；
  3. 若要求签字或公章而对应状态为 `None` $\rightarrow$ `INSUFFICIENT_EVIDENCE`。

#### `R05_PROHIBITION_AND_COMMITMENT`（实质性承诺、禁令与外部核查边界）
- **输入契约**：`prohibition_violated: Optional[bool]`, `commitment_fulfilled: Optional[bool]`, `requires_external_verification: bool`, `evidence_source_type: str`。
- **判断逻辑**：
  1. `prohibition_violated is True` 或 `commitment_fulfilled is False` $\rightarrow$ `NON_COMPLIANT`；
  2. 若 `requires_external_verification is True`（如失信被执行人、无行贿犯罪、无挂靠、无弄虚作假等需外部事实核查的条款）且 `evidence_source_type` 仅为自填表或承诺函（`"SELF_DECLARED_TABLE"` / `"COMMITMENT_LETTER"` / `""`） $\rightarrow$ `INSUFFICIENT_EVIDENCE`；
  3. 其余情况当 `prohibition_violated is False` 且 `commitment_fulfilled is True` 时 $\rightarrow$ `COMPLIANT`。

#### `R06_QUALITATIVE_AND_SCORING`（质量/安全目标、分档计分与多年度信用评分）
- **输入契约**：
  - 区间计分（`operator == "SCORE_BRACKET"`）：`scoring_bands: List[Dict]`, `expected_unit`, `actual_numeric`, `actual_unit`；
  - 多年度信用计分（`scoring_mode == "CREDIT_MULTI_YEAR"`）：`credit_ratings: List[Dict]`（含 `year`, `provincial_grade`, `national_grade`）；
  - 主观评审路由：`requires_human_review: bool`。
- **判断逻辑**：
  1. `requires_human_review is True` $\rightarrow$ `UNSUPPORTED_RULE`（转人工评审）；
  2. 区间计分：核验单位一致，匹配唯一区间档位并返回 `SCORED` 及对应 `points`（**得 0 分返回 `SCORED(0.0)`，绝不判为 `NON_COMPLIANT`**）；
  3. 多年度信用计分：核齐 2019、2020、2021 三个年度，每年度优先取安徽省交通厅等级，无省厅等级取交通部等级，统计 `AA` 等级年数（3年5分、2年4分、1年3分、0年0分）。

#### `R07_STAFFING_COUNT_AND_QUALIFICATION`（多人岗位数量与逐人资格核验）
- **输入契约**：
  - 招标约束：`required_role: str`, `required_count: int`, `count_mode` $\in \{\text{"AT\_LEAST\_N\_QUALIFIED"}, \text{"ALL\_PROPOSED\_QUALIFIED"}\}$, `expected_text: str`, `required_domain: str`, `review_reference_date: str`, `requires_employer_match: bool`, `required_social_security_months: Optional[int]`；
  - 投标事实：`personnel_records: List[Dict]`，每条记录代表**同一自然人**的完整绑定属性：`{name, role, qualification_text, domain, valid_from, valid_to, extension_valid_to, certificate_valid, employer_matches_bidder, social_security_months}`。
- **判断逻辑**：
  1. 过滤 `canonical_role(rec["role"]) == canonical_role(required_role)` 的人员，按 `name` 去重（防止同一人重复计数），若姓名缺失则返回 `INSUFFICIENT_EVIDENCE`；
  2. 对每位候选人独立核验其自身的职称/证书等级、专业方向、证书有效期（对照 `review_reference_date`）、聘用单位及社保月数，**严禁跨人员拼接属性**；
  3. 在 `AT_LEAST_N_QUALIFIED` 模式下：若合格人数 $\ge \text{required\_count}$ $\rightarrow$ `COMPLIANT`；若合格人数 $+$ 信息待确认人数 $\ge \text{required\_count}$ $\rightarrow$ `INSUFFICIENT_EVIDENCE`；否则 $\rightarrow$ `NON_COMPLIANT`；
  4. 在 `ALL_PROPOSED_QUALIFIED` 模式下：只要任一拟派人员不达标或总人数不足 $\rightarrow$ `NON_COMPLIANT`。

#### `R08_PERFORMANCE_CHAIN_AND_SCORING`（历史业绩复合条件、变更批复链与多项目计分）
- **输入契约**：
  - 招标约束：`required_role`, `min_date` (`"2017-01-01"`), `review_reference_date` (`"2022-11-11"`), `allowed_construction_types` (`["新建","改建","扩建"]`), `required_highway_grade` (`"三级公路"`), `min_length_km` (`15.0`), `length_strict_gt` (`bool`), `min_amount_wanyuan` (`5000.0`), `required_scopes` (`["路基","路面","桥涵"]`), `required_proof_docs`, `required_seal_parties`, `min_project_count`, `scoring_mode`, `points_per_project`, `scoring_max_points`；
  - 投标事实：`proposed_person_name: str`, `performance_records: List[Dict]`（含 `project_name`, `person_name`, `handover_person_name`, `role_change_approved`, `performed_role`, `completion_date`, `construction_type`, `highway_grade`, `length_km`, `contract_amount_wanyuan`, `scopes`, `proof_docs`, `seal_parties`）。
- **判断逻辑**：
  1. 按 `project_name` 去重，逐项核验 8 个维度（人员归属与业主变更批复闭环、交竣工时间窗、建设性质、公路等级、里程或金额 `OR` 阈值、路基路面桥涵 `AND` 范围、证明文件齐套性、四方盖章）；
  2. 任一维度明确不符记为 `DISQUALIFIED`，关键字段或变更批复缺失记为 `INCOMPLETE`，全部满足记为 `QUALIFIED`；
  3. 资格审查模式（`T0011`）：合格项目数 $\ge \text{min\_project\_count}$ $\rightarrow$ `COMPLIANT`；含待确认项目可能达标 $\rightarrow$ `INSUFFICIENT_EVIDENCE`；否则 $\rightarrow$ `NON_COMPLIANT`；
  4. 评分模式（`T0209`/`T0210`/`T0216`/`T0359`）：若存在 `INCOMPLETE` 项目且当前得分尚未达到满分上限 `scoring_max_points` $\rightarrow$ `INSUFFICIENT_EVIDENCE`；否则按 `BASE_QUAL_SCORE`（6分）、`SINGLE_PROJECT_BONUS`（4分）或 `PER_PROJECT_ADDITIVE`（每项4分，最高12分封顶）输出 `SCORED`。

#### `R09_FORMULA_BOUND`（多字段公式与净基数扣减核算）
- **输入契约**：
  - 招标约束：`formula_type` (`"NET_BASE_RATIO"` / `"GROSS_RATIO"`), `formula_rate` (`0.015` / `0.10`), `operator` (`">="` / `"=="`), `base_field` (`"chapter_100_700_total"`), `target_field` (`"safety_fee"`), `deduction_fields` (`["safety_fee", "all_risks_insurance", "third_party_insurance"]`), `expected_unit` (`"元"`)；
  - 投标事实：`actual_unit: str`, `component_amounts: Dict[str, float]`。
- **判断逻辑**：
  1. 校验 `expected_unit == actual_unit`，并检查 `base_field`、`target_field` 及所有 `deduction_fields` 在 `component_amounts` 中是否齐全且为有限数值，缺任一项 $\rightarrow$ `INSUFFICIENT_EVIDENCE`；
  2. 使用 `Decimal` 计算净基数 $\text{net\_base} = \text{base} - \sum \text{deductions}$ 及应计目标值 $\text{required} = \text{round}(\text{net\_base} \times \text{rate}, 2)$；
  3. 比较 `actual_target` 与 `required`（允许 $\pm 0.01$ 元四舍五入分位误差），满足 $\rightarrow$ `COMPLIANT`，不足 $\rightarrow$ `NON_COMPLIANT`。

---

### 2.3 抽取字段结构审计与向后兼容扩展表（`src/extraction/schema.py`）

本次对 [`SystemCode/src/extraction/schema.py`](../SystemCode/src/extraction/schema.py) 的修改采用**完全向后兼容**方式：所有原有字段名称、类型与默认值保持不变，新增字段全部提供安全默认值并纳入 `validate_slot_types()` 严格类型检查。

| 所属类 | 变更类别 | 字段名称 | 类型与默认值 | 用途说明 |
| --- | --- | --- | --- | --- |
| `VALID_CONSTRAINT_TYPES` | **保留** | 原有 6 类约束枚举 | `Tuple[str, ...]` | 保持与已有缓存及 `replay_compliance_responses.py` 完全兼容 |
| `VALID_CONSTRAINT_TYPES` | **新增** | `STAFFING_COUNT_AND_QUALIFICATION`, `PERFORMANCE_CHAIN_AND_SCORING`, `FORMULA_BOUND` | `Tuple[str, ...]` | 支持 `R07` 多人岗位、`R08` 业绩证据链、`R09` 公式核算 |
| `TenderConstraintSlots` | **保留** | `constraint_type`, `target_field`, `operator`, `expected_numeric`, `expected_unit`, `secondary_numeric`, `expected_text`, `required_domain`, `requires_official_seal`, `requires_signature`, `scoring_max_points`, `scoring_bands`, `source_quotes` | 原类型不变 | 原有规则输入字段全部保留 |
| `TenderConstraintSlots` | **新增（时效上下文）** | `review_reference_date`, `min_date`, `requires_validity_date`, `allow_extension_notice` | `str=""`, `bool=False/True` | 评审基准日（如 `"2022-11-11"`）、业绩起始日（`"2017-01-01"`）与延期批复开关 |
| `TenderConstraintSlots` | **新增（岗位与人数）** | `required_role`, `required_count`, `count_mode`, `allowed_domains`, `requires_employer_match`, `required_social_security_months` | `str`, `Optional[int]`, `List[str]`, `bool` | 拟任角色、最低人数、计数模式（至少 $N$ 人 vs 每人满足）、相关专业集合、社保月数 |
| `TenderConstraintSlots` | **新增（业绩与计分）** | `min_length_km`, `length_strict_gt`, `min_amount_wanyuan`, `required_highway_grade`, `allowed_construction_types`, `required_scopes`, `required_proof_docs`, `required_seal_parties`, `min_project_count`, `scoring_mode`, `points_per_project`, `baseline_deduct_count` | 数值、布尔与列表 | 历史业绩复合条件、材料链、四方印章主体及多项目累加计分参数 |
| `TenderConstraintSlots` | **新增（公式与路由）** | `formula_type`, `formula_rate`, `base_field`, `deduction_fields`, `requires_external_verification`, `requires_human_review` | `str`, `Optional[float]`, `List[str]`, `bool` | 净基数扣减公式参数、外部事实核查标记、主观评审转人工标记 |
| `BidEvidenceSlots` | **保留** | `evidence_found` 至 `conflict_pairs` 原有 23 个字段 | 原类型不变 | 原有投标事实与引用绑定字段全部保留 |
| `BidEvidenceSlots` | **新增（证书时效与主体）** | `certificate_holder`, `subject_role`, `certificate_number`, `certificate_issuer`, `valid_from`, `valid_to`, `extension_valid_to`, `is_long_term`, `employer_name`, `employer_matches_bidder`, `evidence_source_type` | `str=""`, `Optional[bool]=None` | 证书持有人、有效期起止日、官方延续截止日、长期有效标记、证据来源类型（区分证书原件与自填表） |
| `BidEvidenceSlots` | **新增（复合列表与字典）** | `observed_seal_parties`, `signature_types`, `personnel_records`, `proposed_person_name`, `performance_records`, `component_amounts`, `credit_ratings` | `List[Dict]`, `Dict[str, float]` | 多方印章明细、多人岗位人员列表、历史业绩证据链列表、公式分项金额字典、多年度信用等级列表 |
