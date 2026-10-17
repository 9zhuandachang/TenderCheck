# TenderCheck ML + RAG Joint Evaluation Report (`s604_heuristic`)

## 1. End-to-End Summary Metrics
- **Holdout Tender Clauses Evaluated**: `359`
- **ML Classifier Holdout Accuracy**: **`94.71%`** (Macro-F1: `91.59%`)
- **ML vs. Gold Routing Top-1 Evidence Agreement**: **`96.66%`**
- **ML vs. Gold Routing Top-K Evidence Overlap**: **`97.38%`**

## 2. Structured Bid Entity Ledger Completeness
- **Bidder Company Name**: `(Run with --vision_backend qwen_vl for scanned certificates)`
- **Extracted Qualifications**: `公路路基工程专业承包一级, 公路路面工程专业承包一级, 建筑装饰工程专业承包一级, 公路工程施工总承包特级, 建筑工程施工总承包一级, 市政公用工程施工总承包一级, 桥梁工程专业承包一级, 港口与航道工程施工总承包二级`
- **Company Profile Filled Slots**: `3 / 4` (credit_code, qualifications, safety_permit_valid)
- **Commercial/Schedule Filled Slots**: `1 / 5` (duration_days)
- **Personnel Certificates (`personnel_ledger`)**: `7` records
- **Historical Projects (`performance_ledger`)**: `94` records
- **Seal & Signature Registry (`document_seal_registry`)**: `11` document categories
- **Cross-Page Tracked Entity Mentions (`field_mentions`)**: `11` mentions

## 3. Category-Wise ML + RAG Performance Breakdown
| Category | Clauses | ML Acc | Structured Ledger Hit % | Chunk Evidence Hit % | ML vs Gold Top-K Overlap | Mean Top-1 Score |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| `Qualification` | 59 | 100.0% | 100.0% | 100.0% | 100.0% | 0.0163 |
| `Document` | 154 | 95.5% | 100.0% | 100.0% | 98.7% | 0.0160 |
| `Technical` | 20 | 75.0% | 100.0% | 100.0% | 88.0% | 0.0161 |
| `Commercial` | 63 | 96.8% | 100.0% | 100.0% | 97.1% | 0.0162 |
| `Schedule` | 10 | 90.0% | 100.0% | 100.0% | 92.0% | 0.0144 |
| `Consistency` | 18 | 77.8% | 100.0% | 100.0% | 86.7% | 0.0158 |
| `Integrity` | 17 | 100.0% | 100.0% | 100.0% | 100.0% | 0.0155 |
| `Scoring` | 18 | 100.0% | 100.0% | 100.0% | 100.0% | 0.0157 |

## 4. Representative Case Studies (ML -> RAG Chain)
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
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub03_C000021` (p.6, score=`0.0156`) — _3.交工验收证书_

### Category: `Technical`
- **Clause `T0028`** (招标文件正文.pdf / 第二章前附表1.3.3): 标段交工验收质量评定须为合格。
  - **ML Prediction**: `Technical` (Gold: `Technical`) | **Strategy**: `VolumeFilter(技术标) + Dense/BM25 Hybrid RRF + Reranker`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub20_C000156` (p.29, score=`0.0163`) — _[第三节施工总体筹划 > 3.1.1 质量目标 | 第29页] 序号: 1；项目阶段: 交工验收；质量标准: 质量评定为合格_
- **Clause `T0030`** (招标文件正文.pdf / 第二章前附表1.3.4): 安全目标为无生产安全责任事故。
  - **ML Prediction**: `Technical` (Gold: `Technical`) | **Strategy**: `VolumeFilter(技术标) + Dense/BM25 Hybrid RRF + Reranker`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub19_C001169` (p.339, score=`0.015`) — _安全生产管理手段职工安全教育群众活动安全生产监察手段五同时职工上岗安全教育百日安全无事故安全生产制度特殊工程安全措施、安全合格制新项目重点工程安全教育安全生产三检制各项安全管理制度“三不放过”的分级事故调查处理安全生产群管网各级人员安全生产岗位责任制安全日志挂牌单位工程安全负责人制安全奖惩兑现制度总目标：安全生产零事故图1.2-1 安全生产保证体系框架图规范_

### Category: `Commercial`
- **Clause `T0002`** (招标文件正文.pdf / 第一章2.10): 项目招标控制价115046243.64元，含暂列金8000000.00元。
  - **ML Prediction**: `Commercial` (Gold: `Commercial`) | **Strategy**: `CommercialScheduleLedger Lookup + BidLetter/BOQ Table Search`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub12_C000001` (p.1, score=`0.0164`) — _四、投标保证金 S604皖南交旅融合（飞毕路美丽公路）项目 商务文件_
- **Clause `T0024`** (招标文件正文.pdf / 第一章7.3): 投标保证金可采用银行保函、保证保险、担保保函等保函形式，或电汇、转账。
  - **ML Prediction**: `Commercial` (Gold: `Commercial`) | **Strategy**: `CommercialScheduleLedger Lookup + BidLetter/BOQ Table Search`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub12_C000001` (p.1, score=`0.0164`) — _四、投标保证金 S604皖南交旅融合（飞毕路美丽公路）项目 商务文件_

### Category: `Schedule`
- **Clause `T0037`** (招标文件正文.pdf / 第二章前附表3.3.1): 投标有效期自投标文件递交截止日起90日。
  - **ML Prediction**: `Schedule` (Gold: `Schedule`) | **Strategy**: `ScheduleLedger(duration_days/validity_days) + ProgressPlan Hybrid Search`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub19_C000351` (p.90, score=`0.0152`) — _[第三节路基工程施工方案 > 3.2 施工进度计划 | 第90页] 施工段: 第二施工区 (毕桥镇-飞鲤镇）；工序: 全幅路基工程；工期: 80；计划开工日期: 2023.3.31；计划完工日期: 2022.6.18_
- **Clause `T0136`** (招标文件正文.pdf / 第二章3.3.1): 除前附表另有规定外，投标有效期为90日。
  - **ML Prediction**: `Schedule` (Gold: `Schedule`) | **Strategy**: `ScheduleLedger(duration_days/validity_days) + ProgressPlan Hybrid Search`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub19_C000825` (p.264, score=`0.014`) — _[第三节施工进度计划 > 3.1 施工进度计划表 | 第264页] 序号: 7；名称: 景观和便民、安防施工 （第三工程区）；工作 类型: 实工作；开始时间: 2023-01-05；完成时间: 2023-04-04；工期: 90；备注: /_

### Category: `Consistency`
- **Clause `T0022`** (招标文件正文.pdf / 第一章3.15): 一级建造师电子证书本人手写签名须与签名图像笔迹一致。
  - **ML Prediction**: `Consistency` (Gold: `Consistency`) | **Strategy**: `Cross-Document Entity Mention Aggregation (All-Equal Check) + Hybrid Search`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub16_C000002` (p.1, score=`0.0164`) — _称正高级工程师电话0552-3950279成立时间2018年3月7日员工总人数：7192企业资质等级公路路基工程专业承包一级公路路面工程专业承包一级建筑装饰工程专业承包一级公路工程施工总承包特级水利水电施工总承包一级建筑工程施工总承包一级市政公用工程施工总承包一级桥梁工程专业承包一级港口与航道工程施工总承包二级公路交通工程（公路安全设施分项）专业承包二级其中_
- **Clause `T0069`** (招标文件正文.pdf / 第二章附录5 项目经理): 项目经理注册证书注册单位须与投标人名称一致。
  - **ML Prediction**: `Consistency` (Gold: `Consistency`) | **Strategy**: `Cross-Document Entity Mention Aggregation (All-Equal Check) + Hybrid Search`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub06_C000011` (p.1, score=`0.0161`) — _[（六）项目经理近年完成的类似项目情况表 | 第1页] 序号: 项目经理（或注册建 造师）；01: 王文年_

### Category: `Integrity`
- **Clause `T0057`** (招标文件正文.pdf / 第二章前附表10.6（二）): 拟派经理已任其他项目经理时，不得虚假承诺未在其他项目任项目经理。
  - **ML Prediction**: `Integrity` (Gold: `Integrity`) | **Strategy**: `Integrity Commitment Retrieval + Corporate Seal Identity Verification`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub04_C000007` (p.2, score=`0.0157`) — _[（四）投标人信誉情况表 | 第2页] 在 “ 信 用 中 国 ” 网 站 （http://www.creditchina.gov.cn/）中被列 入失信被执行人名单；: 在近三年内（自投标截止之日向前追溯3年） 投标人或其法定代表人、拟委任的项目经理有 行贿犯罪行为的；；我 公 司 在 “ 信 用 中 国 ” 网 站 （http://www.creditc_
- **Clause `T0152`** (招标文件正文.pdf / 第二章3.5.4): 信誉情况说明须与实际相符，不得弄虚作假。
  - **ML Prediction**: `Integrity` (Gold: `Integrity`) | **Strategy**: `Integrity Commitment Retrieval + Corporate Seal Identity Verification`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub04_C000009` (p.2, score=`0.0164`) — _[（四）投标人信誉情况表 | 第2页] 在 “ 信 用 中 国 ” 网 站 （http://www.creditchina.gov.cn/）中被列 入失信被执行人名单；: 法律法规或投标人须知前附表规定的其他情 形。；我 公 司 在 “ 信 用 中 国 ” 网 站 （http://www.creditchina.gov.cn/）中未被 列入失信被执行人名单；_

### Category: `Scoring`
- **Clause `T0209`** (招标文件正文.pdf / 第三章2.2.4（1）项目经理（1）): 拟任项目经理业绩满足招标最低要求得6分。
  - **ML Prediction**: `Scoring` (Gold: `Scoring`) | **Strategy**: `Multi-Hop Scoring Evidence Retrieval (Performance/Awards + Technical Plan)`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub05_C000001` (p.1, score=`0.0153`) — _[（五）拟委任的项目经理简历表 | 第1页] 姓 名: 职 称；王文年: 高级；年龄: 单位 职务；41: 项目经理；学历: 拟在本标段 工程担任职务；本科: 项目经理_
- **Clause `T0210`** (招标文件正文.pdf / 第三章2.2.4（1）项目经理（2）): 2017年1月1日以来以交工日为准，项目经理以项目经理身份完成一个单合同含长度超过15公里或金额≥5000万元的新建、改建或扩建三级及以上公路施工项目，含路基、路面及桥涵，得4分，本项最高4分。
  - **ML Prediction**: `Scoring` (Gold: `Scoring`) | **Strategy**: `Multi-Hop Scoring Evidence Retrieval (Performance/Awards + Technical Plan)`
  - **Top-1 Evidence**: `11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub19_C000167` (p.32, score=`0.0153`) — _质量、安全、费用等建设目标。3.2 施工管理模式3.2.1 项目管理施工我单位根据本工程的特点及实际需要，本项目采用施工管理模式，成立现场项目经理部，直属公司总部管理。项目经理代表企业法人直接向发包人负责，现场成立以项目经理为中心的决策与管理组织，项目所需生产力要素及资源由公司总部根据项目经理部需要及时统一计划调配，以公司资源保证本项目按合同目标顺利实施。3_
