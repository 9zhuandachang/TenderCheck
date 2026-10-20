# 规则引擎独立验证报告（Step 3 自动比较报告）

- **评测范围 (`evaluation_scope`)**: `standalone_rule_engine_verification`（纯符号规则独立验证，不依赖 RAG 检索或 LLM 抽取）
- **基准项目**: `2022/11.11-S604皖南交旅融合（飞毕路美丽公路）项目`
- **用例审定状态 (`review_status`)**: `human_constructed_deterministic`
- **总用例数**: **69** 条（通过 **69** 条，通过率 **100.00%**）
- **合规判定用例 (`COMPLIANT`/`NON_COMPLIANT`/`INSUFFICIENT_EVIDENCE`/`CONFLICTING_EVIDENCE`/`UNSUPPORTED_RULE`)**: 61/61 (**100.00%**)
- **独立计分用例 (`SCORED` 含 0 分边界与累加封顶)**: 8/8 (**100.00%**)

## 1. 关键安全与错误分桶统计（单列项）

| 诊断指标 | 数量 | 用例编号列表 | 说明 |
| --- | ---: | --- | --- |
| **错误放行 (`false_pass`)** | **0** | `[]` | 预期非通过却被判为 `COMPLIANT`（安全红线，必须为 0） |
| **错误拒绝 (`false_reject`)** | **0** | `[]` | 预期通过/证据不足/0分计分却被判为 `NON_COMPLIANT` |
| **合理弃判 (`justified_abstain`)** | **17** | `['R01_UNIT_01', 'R01_MISS_01', 'R01_SUBJ_01', 'R02_MISS_01_T0004_TRAP', 'R02_MISS_02_NO_REF_DATE', 'R03_MISS_01', 'R04_MISS_01_SEAL_UNCONFIRMED', 'R04_SUBJ_01_OTHER_MANAGER_AWARD', 'R05_MISS_01_EXTERNAL_FACT', 'R05_MISS_02_UNVERIFIED_CONFLICT', 'R06_UNIT_01_DEBT', 'R06_MISS_01_CREDIT_YEAR', 'R07_MISS_01_DOMAIN_MISSING', 'R08_MISS_01_MISSING_CHANGE_APPROVAL', 'R08_UNIT_01_PERF_SCALE', 'R09_MISS_01_MISSING_INSURANCE', 'R09_UNIT_01_MISMATCH']` | 缺失时效、单位不符、缺变更批复、缺公式扣减项或仅自述时正确返回 `INSUFFICIENT_EVIDENCE` |
| **过度弃判 (`unjustified_abstain`)** | **0** | `[]` | 证据完整明确时误判为 `INSUFFICIENT_EVIDENCE` |
| **评分错误 (`score_error`)** | **0** | `[]` | 评分条款结论或分值与预期不一致 |

## 2. 按规则族 (`rule_family`) 统计

| 规则族 | 用例总数 | 通过数 | 通过率 |
| --- | ---: | ---: | ---: |
| `R01_NUMERIC_BOUND` | 9 | 9 | 100.00% |
| `R02_QUALIFICATION_HIERARCHY` | 10 | 10 | 100.00% |
| `R03_RMB_UPPER_AND_CONSISTENCY` | 7 | 7 | 100.00% |
| `R04_DOCUMENT_AND_SEAL` | 6 | 6 | 100.00% |
| `R05_PROHIBITION_AND_COMMITMENT` | 6 | 6 | 100.00% |
| `R06_QUALITATIVE_AND_SCORING` | 8 | 8 | 100.00% |
| `R07_STAFFING_COUNT_AND_QUALIFICATION` | 7 | 7 | 100.00% |
| `R08_PERFORMANCE_CHAIN_AND_SCORING` | 10 | 10 | 100.00% |
| `R09_FORMULA_BOUND` | 6 | 6 | 100.00% |

## 3. 按测试类型 (`case_type`) 统计

| 用例类型 | 用例总数 | 通过数 | 通过率 |
| --- | ---: | ---: | ---: |
| `boundary_equal` | 8 | 8 | 100.00% |
| `boundary_just_over` | 8 | 8 | 100.00% |
| `conflict` | 4 | 4 | 100.00% |
| `human_review_routed` | 1 | 1 | 100.00% |
| `missing_info` | 11 | 11 | 100.00% |
| `negative` | 10 | 10 | 100.00% |
| `positive` | 16 | 16 | 100.00% |
| `subject_mismatch` | 7 | 7 | 100.00% |
| `unit_mismatch` | 4 | 4 | 100.00% |

## 4. 逐条用例执行轨迹明细

| 用例ID | 条款 | 规则族 | 类型 | 预期结论(分值) | 实际结论(分值) | 状态 | 符号推理轨迹 (`symbolic_trace`) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `R01_POS_01` | `T0002` | `R01_NUMERIC_BOUND` | `positive` | `COMPLIANT` | `COMPLIANT` | **PASS** | `97984949.58 <= 115046243.64; secondary=True` |
| `R01_NEG_01` | `T0002` | `R01_NUMERIC_BOUND` | `negative` | `NON_COMPLIANT` | `NON_COMPLIANT` | **PASS** | `97984949.58 <= 115046243.64; secondary=False` |
| `R01_BND_EQ_01` | `T0001` | `R01_NUMERIC_BOUND` | `boundary_equal` | `COMPLIANT` | `COMPLIANT` | **PASS** | `360.0 <= 360.0; secondary=True` |
| `R01_BND_OVER_01` | `T0001` | `R01_NUMERIC_BOUND` | `boundary_just_over` | `NON_COMPLIANT` | `NON_COMPLIANT` | **PASS** | `361.0 <= 360.0; secondary=True` |
| `R01_BND_OVER_02` | `T0135` | `R01_NUMERIC_BOUND` | `boundary_just_over` | `NON_COMPLIANT` | `NON_COMPLIANT` | **PASS** | `115046243.65 <= 115046243.64; secondary=True` |
| `R01_UNIT_01` | `T0001` | `R01_NUMERIC_BOUND` | `unit_mismatch` | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | **PASS** | `单位缺失或不兼容，不能按数值大小猜测单位。` |
| `R01_MISS_01` | `T0037` | `R01_NUMERIC_BOUND` | `missing_info` | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | **PASS** | `缺少招标阈值或投标实际数值。` |
| `R01_SUBJ_01` | `T0023` | `R01_NUMERIC_BOUND` | `subject_mismatch` | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | **PASS** | `Numeric fact belongs to a third-party entity rather than the bidder` |
| `R01_CONFLICT_01` | `T0001` | `R01_NUMERIC_BOUND` | `conflict` | `CONFLICTING_EVIDENCE` | `CONFLICTING_EVIDENCE` | **PASS** | `证据存在冲突，需要核对来源及主体。` |
| `R02_POS_01_EXTENSION` | `T0004` | `R02_QUALIFICATION_HIERARCHY` | `positive` | `COMPLIANT` | `COMPLIANT` | **PASS** | `rank=enterprise_qual:4/enterprise_qual:1; domain=True; effective_valid_to=2022-12-31>=2022-11-11` |
| `R02_MISS_01_T0004_TRAP` | `T0004` | `R02_QUALIFICATION_HIERARCHY` | `missing_info` | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | **PASS** | `仅提供自填基本情况表，未核实法定证书有效期或延续文件。` |
| `R02_NEG_01_EXPIRED` | `T0004` | `R02_QUALIFICATION_HIERARCHY` | `negative` | `NON_COMPLIANT` | `NON_COMPLIANT` | **PASS** | `rank=enterprise_qual:4/enterprise_qual:1; domain=True; 证书有效期(2021-12-31)早于评审基准日(2022-11-11)且无有效延续覆盖。` |
| `R02_BND_EQ_01_DATE` | `T0005` | `R02_QUALIFICATION_HIERARCHY` | `boundary_equal` | `COMPLIANT` | `COMPLIANT` | **PASS** | `rank=safety_license:1/safety_license:1; domain=True; effective_valid_to=2022-11-11>=2022-11-11` |
| `R02_BND_OVER_01_DATE` | `T0005` | `R02_QUALIFICATION_HIERARCHY` | `boundary_just_over` | `NON_COMPLIANT` | `NON_COMPLIANT` | **PASS** | `rank=safety_license:1/safety_license:1; domain=True; 证书有效期(2022-11-10)早于评审基准日(2022-11-11)且无有效延续覆盖。` |
| `R02_POS_02_MANAGER_B_CERT` | `T0009` | `R02_QUALIFICATION_HIERARCHY` | `positive` | `COMPLIANT` | `COMPLIANT` | **PASS** | `rank=safety_b:1/safety_b:1; domain=True; effective_valid_to=2024-09-03>=2022-11-11` |
| `R02_POS_03_CHIEF_ENG_DOMAIN` | `T0015` | `R02_QUALIFICATION_HIERARCHY` | `positive` | `COMPLIANT` | `COMPLIANT` | **PASS** | `rank=title:3/title:2; domain=True; certificate_valid=None` |
| `R02_SUBJ_01_ROLE_MISMATCH` | `T0007` | `R02_QUALIFICATION_HIERARCHY` | `subject_mismatch` | `NON_COMPLIANT` | `NON_COMPLIANT` | **PASS** | `持证人岗位不匹配: actual_role=chief_engineer != required_role=project_manager` |
| `R02_MISS_02_NO_REF_DATE` | `T0007` | `R02_QUALIFICATION_HIERARCHY` | `missing_info` | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | **PASS** | `缺少项目评审基准日期(review_reference_date)，无法对照核验具体证书有效期日期。` |
| `R02_CONFLICT_01_CERT_DATE` | `T0005` | `R02_QUALIFICATION_HIERARCHY` | `conflict` | `CONFLICTING_EVIDENCE` | `CONFLICTING_EVIDENCE` | **PASS** | `证据存在冲突，需要核对来源及主体。` |
| `R03_POS_01` | `T0127` | `R03_RMB_UPPER_AND_CONSISTENCY` | `positive` | `COMPLIANT` | `COMPLIANT` | **PASS** | `uppercase=True; cross_table=True` |
| `R03_NEG_01` | `T0127` | `R03_RMB_UPPER_AND_CONSISTENCY` | `negative` | `NON_COMPLIANT` | `NON_COMPLIANT` | **PASS** | `uppercase=False; cross_table=True` |
| `R03_BND_EQ_01_PAREN_NORM` | `T0106` | `R03_RMB_UPPER_AND_CONSISTENCY` | `boundary_equal` | `COMPLIANT` | `COMPLIANT` | **PASS** | `安徽水利开发有限公司（总部） == 安徽水利开发有限公司(总部)` |
| `R03_BND_OVER_01_ONE_CENT` | `T0127` | `R03_RMB_UPPER_AND_CONSISTENCY` | `boundary_just_over` | `NON_COMPLIANT` | `NON_COMPLIANT` | **PASS** | `uppercase=True; cross_table=False` |
| `R03_SUBJ_01` | `T0069` | `R03_RMB_UPPER_AND_CONSISTENCY` | `subject_mismatch` | `NON_COMPLIANT` | `NON_COMPLIANT` | **PASS** | `安徽水利开发有限公司 == 安徽水利第一工程局有限公司` |
| `R03_MISS_01` | `T0106` | `R03_RMB_UPPER_AND_CONSISTENCY` | `missing_info` | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | **PASS** | `缺少两个独立来源的比对值，不能复制已有主体补齐。` |
| `R03_CONFLICT_01` | `T0188` | `R03_RMB_UPPER_AND_CONSISTENCY` | `conflict` | `CONFLICTING_EVIDENCE` | `CONFLICTING_EVIDENCE` | **PASS** | `证据存在冲突，需要核对来源及主体。` |
| `R04_POS_01_MULTI_SEAL` | `T0014` | `R04_DOCUMENT_AND_SEAL` | `positive` | `COMPLIANT` | `COMPLIANT` | **PASS** | `seal=True; parties=['建设单位', '设计单位', '监理单位', '施工单位']; signature=None` |
| `R04_NEG_01_MISSING_PARTY_SEAL` | `T0014` | `R04_DOCUMENT_AND_SEAL` | `negative` | `NON_COMPLIANT` | `NON_COMPLIANT` | **PASS** | `材料缺少必需的盖章单位: ['设计单位']` |
| `R04_MISS_01_SEAL_UNCONFIRMED` | `T0014` | `R04_DOCUMENT_AND_SEAL` | `missing_info` | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | **PASS** | `缺少多方盖章单位明细确认（如建设、设计、监理、施工单位印章）。` |
| `R04_NEG_02_DOC_ABSENT` | `T0112` | `R04_DOCUMENT_AND_SEAL` | `negative` | `NON_COMPLIANT` | `NON_COMPLIANT` | **PASS** | `已提取到明确的材料缺失证据。` |
| `R04_SUBJ_01_OTHER_MANAGER_AWARD` | `T0013` | `R04_DOCUMENT_AND_SEAL` | `subject_mismatch` | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | **PASS** | `Performance document lacks a link to the currently proposed project manager` |
| `R04_POS_02_SIGNATURE_PRESENT` | `T0294` | `R04_DOCUMENT_AND_SEAL` | `positive` | `COMPLIANT` | `COMPLIANT` | **PASS** | `seal=None; parties=[]; signature=True` |
| `R05_POS_01` | `T0033` | `R05_PROHIBITION_AND_COMMITMENT` | `positive` | `COMPLIANT` | `COMPLIANT` | **PASS** | `已提取出对应禁止事项与承诺的明确响应。` |
| `R05_POS_02_OR_PATH_WITHDRAWAL` | `T0056` | `R05_PROHIBITION_AND_COMMITMENT` | `boundary_equal` | `COMPLIANT` | `COMPLIANT` | **PASS** | `已提取出对应禁止事项与承诺的明确响应。` |
| `R05_NEG_01` | `T0016` | `R05_PROHIBITION_AND_COMMITMENT` | `negative` | `NON_COMPLIANT` | `NON_COMPLIANT` | **PASS** | `已提取出明确违反或拒绝承诺的证据。` |
| `R05_MISS_01_EXTERNAL_FACT` | `T0099` | `R05_PROHIBITION_AND_COMMITMENT` | `missing_info` | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | **PASS** | `仅提供投标人自述或承诺函，不能独立证实外部行政处罚、挂靠或行贿等客观事实状态。` |
| `R05_CONFLICT_01_T0057` | `T0057` | `R05_PROHIBITION_AND_COMMITMENT` | `conflict` | `CONFLICTING_EVIDENCE` | `CONFLICTING_EVIDENCE` | **PASS** | `证据存在冲突，需要核对来源及主体。` |
| `R05_MISS_02_UNVERIFIED_CONFLICT` | `T0033` | `R05_PROHIBITION_AND_COMMITMENT` | `missing_info` | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | **PASS** | `冲突缺少双方可核对的主体、时期和事实引用。` |
| `R06_POS_01_DEBT_6PTS` | `T0214` | `R06_QUALITATIVE_AND_SCORING` | `positive` | `SCORED (6.0)` | `SCORED (6.0)` | **PASS** | `按已提取的评分档位计分；得零分不自动等于不合规。` |
| `R06_BND_EQ_01_DEBT_75` | `T0214` | `R06_QUALITATIVE_AND_SCORING` | `boundary_equal` | `SCORED (4.0)` | `SCORED (4.0)` | **PASS** | `按已提取的评分档位计分；得零分不自动等于不合规。` |
| `R06_BND_OVER_01_DEBT_ZERO` | `T0214` | `R06_QUALITATIVE_AND_SCORING` | `boundary_just_over` | `SCORED (0.0)` | `SCORED (0.0)` | **PASS** | `按已提取的评分档位计分；得零分不自动等于不合规。` |
| `R06_UNIT_01_DEBT` | `T0214` | `R06_QUALITATIVE_AND_SCORING` | `unit_mismatch` | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | **PASS** | `评分档位、实际值或一致的单位缺失。` |
| `R06_POS_02_CREDIT_MULTI_YEAR` | `T0220` | `R06_QUALITATIVE_AND_SCORING` | `positive` | `SCORED (5.0)` | `SCORED (5.0)` | **PASS** | `2019-2021信用等级={2019: 'AA', 2020: 'AA', 2021: 'AA'}, AA年数=3, 得分=5.0` |
| `R06_MISS_01_CREDIT_YEAR` | `T0220` | `R06_QUALITATIVE_AND_SCORING` | `missing_info` | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | **PASS** | `缺少完整年度信用等级记录，已提取年度=[2019, 2020]` |
| `R06_NEG_01_QUALITY_TARGET` | `T0028` | `R06_QUALITATIVE_AND_SCORING` | `negative` | `NON_COMPLIANT` | `NON_COMPLIANT` | **PASS** | `目标=合格; 引用响应=不合格` |
| `R06_HUMAN_01_SUBJECTIVE` | `T0222` | `R06_QUALITATIVE_AND_SCORING` | `human_review_routed` | `UNSUPPORTED_RULE` | `UNSUPPORTED_RULE` | **PASS** | `该条款属于主观技术评分或版式字体视觉核查，须转交人工评审，不自动给出合规结论或分值。` |
| `R07_POS_01_ROAD_ENG` | `T0078` | `R07_STAFFING_COUNT_AND_QUALIFICATION` | `positive` | `COMPLIANT` | `COMPLIANT` | **PASS** | `mode=AT_LEAST_N_QUALIFIED; qualified=['王雷'] (count=1>=1)` |
| `R07_POS_02_SAFETY_OFFICERS` | `T0081` | `R07_STAFFING_COUNT_AND_QUALIFICATION` | `positive` | `COMPLIANT` | `COMPLIANT` | **PASS** | `mode=AT_LEAST_N_QUALIFIED; qualified=['张磊', '陈亮亮'] (count=2>=1)` |
| `R07_BND_EQ_01_EXACT_TWO_PERSONS` | `T0081` | `R07_STAFFING_COUNT_AND_QUALIFICATION` | `boundary_equal` | `COMPLIANT` | `COMPLIANT` | **PASS** | `mode=AT_LEAST_N_QUALIFIED; qualified=['张磊', '陈亮亮'] (count=2>=2)` |
| `R07_SUBJ_01_SPLICING_TRAP` | `T0078` | `R07_STAFFING_COUNT_AND_QUALIFICATION` | `subject_mismatch` | `NON_COMPLIANT` | `NON_COMPLIANT` | **PASS** | `合格岗位人数不足: qualified=0 < required=1; disqualified=['王雷:资格不满足(title:1 < title:2)']` |
| `R07_NEG_01_ALL_PROPOSED_MODE` | `T0081` | `R07_STAFFING_COUNT_AND_QUALIFICATION` | `negative` | `NON_COMPLIANT` | `NON_COMPLIANT` | **PASS** | `mode=ALL_PROPOSED_QUALIFIED; qualified=1/1; disqualified=['赵六:证书有效期(2021-05-01)早于评审基准日(2022-11-11)且无有效延续覆盖。']` |
| `R07_BND_OVER_01_HEADCOUNT` | `T0081` | `R07_STAFFING_COUNT_AND_QUALIFICATION` | `boundary_just_over` | `NON_COMPLIANT` | `NON_COMPLIANT` | **PASS** | `合格岗位人数不足: qualified=1 < required=2; disqualified=[]` |
| `R07_MISS_01_DOMAIN_MISSING` | `T0079` | `R07_STAFFING_COUNT_AND_QUALIFICATION` | `missing_info` | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | **PASS** | `合格人数尚不足1人，且部分人员信息缺失: ['陈春海:缺少专业信息']` |
| `R08_POS_01_MANAGER_PERF_WITH_CHANGE` | `T0011` | `R08_PERFORMANCE_CHAIN_AND_SCORING` | `positive` | `COMPLIANT` | `COMPLIANT` | **PASS** | `qualified_projects=['省道214郎溪段改建工程四期工程'] (count=1>=1)` |
| `R08_POS_03_T0209_BASE_SCORE` | `T0209` | `R08_PERFORMANCE_CHAIN_AND_SCORING` | `positive` | `SCORED (6.0)` | `SCORED (6.0)` | **PASS** | `mode=BASE_QUAL_SCORE; qualified=['省道214郎溪段改建工程四期工程']; disqualified=[]; score=6.0` |
| `R08_MISS_01_MISSING_CHANGE_APPROVAL` | `T0011` | `R08_PERFORMANCE_CHAIN_AND_SCORING` | `missing_info` | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | **PASS** | `业绩证据链尚未确认完整: ['省道214郎溪段改建工程四期工程:合同原人员(向阳)与当前拟任(王文年)不同，缺少业主变更批复确认']` |
| `R08_SUBJ_01_OTHER_PERSON_PERF` | `T0011` | `R08_PERFORMANCE_CHAIN_AND_SCORING` | `subject_mismatch` | `NON_COMPLIANT` | `NON_COMPLIANT` | **PASS** | `有效业绩数量不足: qualified=0<1; disqualified=['某高速公路一标:业绩归属他人(李四)且无有效变更为王文年的批复']` |
| `R08_SUBJ_02_T0359_ADDENDUM_ROLE` | `T0359` | `R08_PERFORMANCE_CHAIN_AND_SCORING` | `subject_mismatch` | `SCORED (0.0)` | `SCORED (0.0)` | **PASS** | `mode=SINGLE_PROJECT_BONUS; qualified=[]; disqualified=['某一级公路工程:历史担任职务不符(road_engineer!=chief_engineer)']; score=0.0` |
| `R08_NEG_01_MISSING_BRIDGE_SCOPE` | `T0011` | `R08_PERFORMANCE_CHAIN_AND_SCORING` | `negative` | `NON_COMPLIANT` | `NON_COMPLIANT` | **PASS** | `有效业绩数量不足: qualified=0<1; disqualified=["某二级公路改建项目:缺少必需施工内容['桥涵']"]` |
| `R08_BND_EQ_01_OR_SCALE` | `T0011` | `R08_PERFORMANCE_CHAIN_AND_SCORING` | `boundary_equal` | `COMPLIANT` | `COMPLIANT` | **PASS** | `qualified_projects=['某三级公路扩建工程'] (count=1>=1)` |
| `R08_BND_OVER_01_DATE_AND_STRICT_GT` | `T0210` | `R08_PERFORMANCE_CHAIN_AND_SCORING` | `boundary_just_over` | `SCORED (0.0)` | `SCORED (0.0)` | **PASS** | `mode=SINGLE_PROJECT_BONUS; qualified=[]; disqualified=['边界测试公路工程:规模不达标(length=15.0km, amount=4999.99万元)']; score=0.0` |
| `R08_POS_02_ADDITIVE_SCORING_T0216` | `T0216` | `R08_PERFORMANCE_CHAIN_AND_SCORING` | `positive` | `SCORED (12.0)` | `SCORED (12.0)` | **PASS** | `mode=PER_PROJECT_ADDITIVE; qualified=['S214四期工程', 'S214五期工程', '大广高速互通立交工程', 'S331改建工程']; disqualified=[]; score=12.0` |
| `R08_UNIT_01_PERF_SCALE` | `T0011` | `R08_PERFORMANCE_CHAIN_AND_SCORING` | `unit_mismatch` | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | **PASS** | `业绩证据链尚未确认完整: ['某公路项目:规模指标或单位缺失(length=15500米, amount=None万元)']` |
| `R09_POS_01_T0059_EXACT` | `T0059` | `R09_FORMULA_BOUND` | `positive` | `COMPLIANT` | `COMPLIANT` | **PASS** | `formula=NET_BASE_RATIO; net_base=88301915.07; required=1324528.73; actual=1324528.73; op=>=` |
| `R09_BND_EQ_01_PERF_BOND` | `T0049` | `R09_FORMULA_BOUND` | `boundary_equal` | `COMPLIANT` | `COMPLIANT` | **PASS** | `formula=GROSS_RATIO; net_base=97984949.58; required=9798494.96; actual=9798494.96; op===` |
| `R09_BND_OVER_01_T0059_UNDERPAID` | `T0059` | `R09_FORMULA_BOUND` | `boundary_just_over` | `NON_COMPLIANT` | `NON_COMPLIANT` | **PASS** | `formula=NET_BASE_RATIO; net_base=88301915.80; required=1324528.74; actual=1324528.00; op=>=` |
| `R09_NEG_01_ZERO_SAFETY_FEE` | `T0059` | `R09_FORMULA_BOUND` | `negative` | `NON_COMPLIANT` | `NON_COMPLIANT` | **PASS** | `formula=NET_BASE_RATIO; net_base=89626443.80; required=1344396.66; actual=0.00; op=>=` |
| `R09_MISS_01_MISSING_INSURANCE` | `T0059` | `R09_FORMULA_BOUND` | `missing_info` | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | **PASS** | `缺少公式基数或扣减分项金额: ['all_risks_insurance', 'third_party_insurance']，不能仅凭单一金额判定合规。` |
| `R09_UNIT_01_MISMATCH` | `T0059` | `R09_FORMULA_BOUND` | `unit_mismatch` | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | **PASS** | `公式核算单位不一致: expected=元, actual=万元` |
