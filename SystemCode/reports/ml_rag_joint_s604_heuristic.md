# TenderCheck ML + RAG Joint Evaluation Report (`s604_heuristic`)

## 1. End-to-End Summary Metrics
- **Holdout Tender Clauses Evaluated**: `359`
- **ML Classifier Holdout Accuracy**: **`94.71%`** (Macro-F1: `91.59%`)
- **ML vs. Gold Routing Top-1 Evidence Agreement**: **`97.49%`**
- **ML vs. Gold Routing Top-K Evidence Overlap**: **`96.94%`**
- **Ground-Truth Entity Ledger Slot Accuracy**: **`100.0%`** (`10 / 10` verified bid facts matched)

## 2. Ground-Truth Entity Ledger Slot Verification
| Bid Entity Slot | Ground-Truth Bid Value | Extracted Ledger Value | Match |
| :--- | :--- | :--- | :---: |
| `company_name` | `安徽水利开发有限公司` | `安徽水利开发有限公司` | **PASS** |
| `legal_representative` | `张晓林` | `张晓林` | **PASS** |
| `credit_code` | `91340300MA2RJ2XJXJ` | `91340300MA2RJ2XJXJ` | **PASS** |
| `required_qualification` | `公路工程施工总承包特级` | `公路工程施工总承包特级` | **PASS** |
| `safety_permit_valid` | `True` | `True` | **PASS** |
| `project_manager_name` | `王文年` | `王文年` | **PASS** |
| `bid_price_yuan` | `97984949.58` | `97984949.58` | **PASS** |
| `bid_price_upper` | `玖仟柒佰玖拾捌万肆仟玖佰肆拾玖元伍角捌分` | `玖仟柒佰玖拾捌万肆仟玖佰肆拾玖元伍角捌分` | **PASS** |
| `duration_days` | `360日历天` | `360日历天` | **PASS** |
| `quality_target` | `合格` | `标段工程交工验收的质量评定：合格` | **PASS** |

## 3. Ground-Truth RAG Retrieval Benchmark & Ablation Study (50 Verified Clauses)
| Retrieval Architecture | Hit@1 (Recall@1) | Hit@3 (Recall@3) | Hit@5 (Recall@5) | MRR@5 |
| :--- | ---: | ---: | ---: | ---: |
| **Exp 1: Unrouted BM25 Baseline** (No Category Router) | 74.0% | 92.0% | 100.0% | 0.8380 |
| **Exp 2: Unrouted Hybrid RRF** (BM25 + Dense, No Router) | 64.0% | 88.0% | 92.0% | 0.7557 |
| **Exp 3: ML-Routed Category Retriever** (Chunk Index Only) | 76.0% | 96.0% | 100.0% | 0.8633 |
| **Exp 4: Ours Full Dual-Index** (ML Router + Ledger + Chunks) | **86.0%** | **100.0%** | **100.0%** | **0.9233** |

### Per-Category Ground-Truth Retrieval Metrics (`Ours Full Dual-Index`)
| Category | Gold Queries | Hit@1 | Hit@3 | Hit@5 | MRR@5 |
| :--- | ---: | ---: | ---: | ---: | ---: |
| `Qualification` | 15 | 93.3% | 100.0% | 100.0% | 0.9667 |
| `Document` | 7 | 85.7% | 100.0% | 100.0% | 0.9286 |
| `Technical` | 3 | 100.0% | 100.0% | 100.0% | 1.0000 |
| `Commercial` | 8 | 87.5% | 100.0% | 100.0% | 0.9375 |
| `Schedule` | 4 | 100.0% | 100.0% | 100.0% | 1.0000 |
| `Consistency` | 3 | 100.0% | 100.0% | 100.0% | 1.0000 |
| `Integrity` | 4 | 50.0% | 100.0% | 100.0% | 0.7083 |
| `Scoring` | 6 | 66.7% | 100.0% | 100.0% | 0.8056 |

## 4. Structured Bid Entity Ledger Summary
- **Bidder Company Name**: `安徽水利开发有限公司`
- **Legal Representative**: `张晓林`
- **Unified Social Credit Code**: `91340300MA2RJ2XJXJ`
- **Extracted Qualifications**: `公路路基工程专业承包一级, 公路路面工程专业承包一级, 建筑装饰工程专业承包一级, 公路工程施工总承包特级, 建筑工程施工总承包一级, 市政公用工程施工总承包一级, 桥梁工程专业承包一级, 港口与航道工程施工总承包二级`
- **Bid Total Price**: `97984949.58` (`玖仟柒佰玖拾捌万肆仟玖佰肆拾玖元伍角捌分`)
- **Duration & Quality Target**: `360日历天` | `标段工程交工验收的质量评定：合格`
- **Personnel Certificates (`personnel_ledger`)**: `5` records
- **Historical Projects (`performance_ledger`)**: `94` records
- **Seal & Signature Registry (`document_seal_registry`)**: `11` document categories
- **Cross-Page Tracked Entity Mentions (`field_mentions`)**: `9` mentions

## 5. Full Holdout Set (359 Clauses) Category Routing Consistency
| Category | Clauses | ML Acc | ML vs Gold Top-K Overlap | Mean Top-1 Score |
| :--- | ---: | ---: | ---: | ---: |
| `Qualification` | 59 | 100.0% | 100.0% | 0.0163 |
| `Document` | 154 | 95.5% | 97.1% | 0.0162 |
| `Technical` | 20 | 75.0% | 78.0% | 0.0163 |
| `Commercial` | 63 | 96.8% | 97.8% | 0.0162 |
| `Schedule` | 10 | 90.0% | 96.0% | 0.0163 |
| `Consistency` | 18 | 77.8% | 97.8% | 0.0159 |
| `Integrity` | 17 | 100.0% | 100.0% | 0.0155 |
| `Scoring` | 18 | 100.0% | 100.0% | 0.0160 |

## 6. Representative Case Studies (ML -> RAG Chain)
### Category: `Qualification`
- **Clause `T0004`** (招标文件正文.pdf / 第一章3.1): 投标人须具备公路工程施工总承包三级及以上资质。
  - **ML Prediction**: `Qualification` (Gold: `Qualification`) | **Strategy**: `EntityLedger(Company+Personnel+Performance) + Certificate/Table Hybrid Search`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub16_C000002` (p.1, score=`0.0164`) — _称正高级工程师电话0552-3950279成立时间2018年3月7日员工总人数：7192企业资质等级公路路基工程专业承包一级公路路面工程专业承包一级建筑装饰工程专业承包一级公路工程施工总承包特级水利水电施工总承包一级建筑工程施工总承包一级市政公用工程施工总承包一级桥梁工程专业承包一级港口与航道工程施工总承包二级公路交通工程（公路安全设施分项）专业承包二级其中_
- **Clause `T0005`** (招标文件正文.pdf / 第一章3.1): 投标人须具有有效安全生产许可证。
  - **ML Prediction**: `Qualification` (Gold: `Qualification`) | **Strategy**: `EntityLedger(Company+Personnel+Performance) + Certificate/Table Hybrid Search`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub16_C000008` (p.5, score=`0.0164`) — _安全生产许可证 S604皖南交旅融合（飞毕路美丽公路）项目_

### Category: `Document`
- **Clause `T0013`** (招标文件正文.pdf / 第一章3.4): 项目经理业绩须提供中标通知书。
  - **ML Prediction**: `Document` (Gold: `Document`) | **Strategy**: `DocumentSealRegistry Lookup + BM25-Weighted Form/Seal Search`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub06_C000017` (p.2, score=`0.0164`) — _1.中标通知书_
- **Clause `T0014`** (招标文件正文.pdf / 第一章3.4): 项目经理业绩须提供交工或竣工验收证明，须有建设、设计、监理、施工单位等盖章确认。
  - **ML Prediction**: `Document` (Gold: `Document`) | **Strategy**: `DocumentSealRegistry Lookup + BM25-Weighted Form/Seal Search`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub02_C000021` (p.6, score=`0.0158`) — _3.交工验收证书_

### Category: `Technical`
- **Clause `T0028`** (招标文件正文.pdf / 第二章前附表1.3.3): 标段交工验收质量评定须为合格。
  - **ML Prediction**: `Technical` (Gold: `Technical`) | **Strategy**: `VolumeFilter(技术标+投标函承诺) + Dense/BM25 Hybrid RRF + Reranker`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub19_C000156` (p.29, score=`0.0164`) — _[第三节施工总体筹划 > 3.1.1 质量目标 | 第29页] 序号: 1；项目阶段: 交工验收；质量标准: 质量评定为合格_
- **Clause `T0030`** (招标文件正文.pdf / 第二章前附表1.3.4): 安全目标为无生产安全责任事故。
  - **ML Prediction**: `Technical` (Gold: `Technical`) | **Strategy**: `VolumeFilter(技术标+投标函承诺) + Dense/BM25 Hybrid RRF + Reranker`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub19_C001167` (p.339, score=`0.0161`) — _第五章安全生产管理体系及保证措施第一节安全管理目标及组织体系为了加强S604 皖南交旅融合（飞毕路美丽公路）项目过程中的安全生产管理，根据我公司多年的安全生产管理经验，针对本工程的实际情况，制定本工程的安全生产方针,实行安全生产目标管理控制，制订具体措施，工作层层分解落实到每个岗位。安全防护手册印发至全体职工。始终开展“安全日”、“安全周”“安全月”活动，期_

### Category: `Commercial`
- **Clause `T0002`** (招标文件正文.pdf / 第一章2.10): 项目招标控制价115046243.64元，含暂列金8000000.00元。
  - **ML Prediction**: `Commercial` (Gold: `Commercial`) | **Strategy**: `CommercialScheduleLedger Lookup + BidLetter/BOQ Table Search`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sys05_C000014` (p.863, score=`0.0164`) — _[一、工程概况 | 第863页] 序号: 11；章次: 暂列金额（不含计日工总额）；金额: 8000000_
- **Clause `T0024`** (招标文件正文.pdf / 第一章7.3): 投标保证金可采用银行保函、保证保险、担保保函等保函形式，或电汇、转账。
  - **ML Prediction**: `Commercial` (Gold: `Commercial`) | **Strategy**: `CommercialScheduleLedger Lookup + BidLetter/BOQ Table Search`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub12_C000001` (p.1, score=`0.0163`) — _四、投标保证金 S604皖南交旅融合（飞毕路美丽公路）项目 商务文件_

### Category: `Schedule`
- **Clause `T0037`** (招标文件正文.pdf / 第二章前附表3.3.1): 投标有效期自投标文件递交截止日起90日。
  - **ML Prediction**: `Schedule` (Gold: `Schedule`) | **Strategy**: `ScheduleLedger(duration_days/validity_days) + BidLetter/ProgressPlan Search`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sys01_C000004` (p.4, score=`0.0164`) — _投标函郎溪县交通建设发展有限公司 (招标人名称)：1.我方已仔细研究S604皖南交旅融合（飞毕路美丽公路）项目 （项目名称）S604皖南交旅融合（飞毕路美丽公路）项目 标段招标文件的全部内容，在考察工程现场后， 愿以报价文件投标函中的 投标总报价，按合同约定实施和完成承包工程，修补工程中的任何缺陷。2.我方承诺在招标文件规定的投标有效期内不撤销投标文件。3._
- **Clause `T0136`** (招标文件正文.pdf / 第二章3.3.1): 除前附表另有规定外，投标有效期为90日。
  - **ML Prediction**: `Schedule` (Gold: `Schedule`) | **Strategy**: `ScheduleLedger(duration_days/validity_days) + BidLetter/ProgressPlan Search`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sys01_C000004` (p.4, score=`0.0164`) — _投标函郎溪县交通建设发展有限公司 (招标人名称)：1.我方已仔细研究S604皖南交旅融合（飞毕路美丽公路）项目 （项目名称）S604皖南交旅融合（飞毕路美丽公路）项目 标段招标文件的全部内容，在考察工程现场后， 愿以报价文件投标函中的 投标总报价，按合同约定实施和完成承包工程，修补工程中的任何缺陷。2.我方承诺在招标文件规定的投标有效期内不撤销投标文件。3._

### Category: `Consistency`
- **Clause `T0022`** (招标文件正文.pdf / 第一章3.15): 一级建造师电子证书本人手写签名须与签名图像笔迹一致。
  - **ML Prediction**: `Consistency` (Gold: `Consistency`) | **Strategy**: `Cross-Document Entity Mention Aggregation (All-Equal Check) + Hybrid Search`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub14_C000002` (p.1, score=`0.0161`) — _[（二）项目管理机构人员组成表 | 第1页] 职务: 项目经理；姓名: 王文年；职称: 高级；执业或职业资格证明: 一级注册 建造师证；列5: 一级；列6: 皖1342011201207116；列7: 公路工程；列8: 已缴纳_
- **Clause `T0069`** (招标文件正文.pdf / 第二章附录5 项目经理): 项目经理注册证书注册单位须与投标人名称一致。
  - **ML Prediction**: `Consistency` (Gold: `Consistency`) | **Strategy**: `Cross-Document Entity Mention Aggregation (All-Equal Check) + Hybrid Search`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub06_C000011` (p.1, score=`0.0163`) — _[（六）项目经理近年完成的类似项目情况表 | 第1页] 序号: 项目经理（或注册建 造师）；01: 王文年_

### Category: `Integrity`
- **Clause `T0057`** (招标文件正文.pdf / 第二章前附表10.6（二）): 拟派经理已任其他项目经理时，不得虚假承诺未在其他项目任项目经理。
  - **ML Prediction**: `Integrity` (Gold: `Integrity`) | **Strategy**: `Integrity Commitment Retrieval + Corporate Seal Identity Verification`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sys01_C000012` (p.5, score=`0.0164`) — _，本人承诺：本次投标项目所提供的项目经理业绩（招标文件如有要求）已经我本人核实，该业绩工程在实施过程中的项目经理确为我本人，合同（或竣工相关资料证明）上的项目经理签字也均为我本人在该业绩工程实施时段所签，真实无误，不存在虚假和挂靠现象，也不存在为本次投标而造假的行为。本人承诺，一旦本项目中标，我本人将严格按照招标文件规定到岗履约，不得提出任何借口和理由。本人_
- **Clause `T0152`** (招标文件正文.pdf / 第二章3.5.4): 信誉情况说明须与实际相符，不得弄虚作假。
  - **ML Prediction**: `Integrity` (Gold: `Integrity`) | **Strategy**: `Integrity Commitment Retrieval + Corporate Seal Identity Verification`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sys01_C000013` (p.5, score=`0.016`) — _经理签字：王文年 ,联系电话: 18656502005身份证号：34080319811213217X日期：2022年11月10日注：投标人应按招标文件《项目经理到岗履约承诺函》格式填写，将填写完毕的《项目经理到岗履约承诺函》以PDF或WORD格式，作为附件上传至电子投标文件“投标文件制作人的情况说明”模块中。_

### Category: `Scoring`
- **Clause `T0209`** (招标文件正文.pdf / 第三章2.2.4（1）项目经理（1）): 拟任项目经理业绩满足招标最低要求得6分。
  - **ML Prediction**: `Scoring` (Gold: `Scoring`) | **Strategy**: `Multi-Hop Scoring Evidence Retrieval (Performance/Awards + Technical Plan)`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sys01_C000012` (p.5, score=`0.0161`) — _，本人承诺：本次投标项目所提供的项目经理业绩（招标文件如有要求）已经我本人核实，该业绩工程在实施过程中的项目经理确为我本人，合同（或竣工相关资料证明）上的项目经理签字也均为我本人在该业绩工程实施时段所签，真实无误，不存在虚假和挂靠现象，也不存在为本次投标而造假的行为。本人承诺，一旦本项目中标，我本人将严格按照招标文件规定到岗履约，不得提出任何借口和理由。本人_
- **Clause `T0210`** (招标文件正文.pdf / 第三章2.2.4（1）项目经理（2）): 2017年1月1日以来以交工日为准，项目经理以项目经理身份完成一个单合同含长度超过15公里或金额≥5000万元的新建、改建或扩建三级及以上公路施工项目，含路基、路面及桥涵，得4分，本项最高4分。
  - **ML Prediction**: `Scoring` (Gold: `Scoring`) | **Strategy**: `Multi-Hop Scoring Evidence Retrieval (Performance/Awards + Technical Plan)`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sys01_C000007` (p.4, score=`0.0161`) — _。8.拟派项目经理到岗履约承诺（请投标人认真阅读此条内容，并根据实际情况选择以下一种格式进行承诺）：格式二：我单位承诺：我单位拟派本次投标项目的项目经理已在来安县交通运输局（发包人名称）发包的G345来安至釜山段一级公路改建三期工程（项目名称）担任项目经理岗位，该项目合同开工日期为2021年2月18日，竣工日期为2023年8月7日，如本次投标项目中标后,合同_
