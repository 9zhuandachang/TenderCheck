# 50 条需求的证据标注进度

助手已核对50条；待补齐0条；人工审定0条。2026-10-20原件纠错后位置如下。
详细改动见 quote_repairs.json 与 visual_reference_repairs.json。

| ID | 需求 | 必要证据组位置（物理页） |
| --- | --- | --- |
| T0001 | 计划工期为360日历天。 | duration: sys01 第4–4页 或 sys02 第302–302页 |
| T0002 | 项目招标控制价115046243.64元，含暂列金8000000.00元。 | bid_total: sys04 第859–859页；provisional_sum: sys05 第863–863页 |
| T0003 | 投标人须具有独立法人资格。 | business_license: sub16 第4–4页 |
| T0004 | 投标人须具备公路工程施工总承包三级及以上资质。 | highway_qualification_certificate: sub16 第6–6页 |
| T0005 | 投标人须具有有效安全生产许可证。 | safety_production_license: sub16 第5–5页 |
| T0007 | 项目经理须具有公路工程专业一级注册建造师资格。 | proposed_manager_role: sub14 第1–1页 或 sub05 第1–1页；manager_constructor_certificate: sub05 第3–3页 |
| T0009 | 项目经理须具有有效安全生产考核合格证书。 | proposed_manager_role: sub14 第1–1页 或 sub05 第1–1页；manager_safety_b_certificate: sub05 第4–4页 |
| T0010 | 项目经理须未在其他项目任职，或虽任职但本项目中标后能够从原项目撤离。 | manager_mobilization: sys01 第4–4页 |
| T0011 | 拟派项目经理须于2017年1月1日至投标截止时间以项目经理身份至少完成一个新建、改建或扩建三级及以上公路施工项目，单合同含长度≥15公里或金额≥5000万元，内容包括路基、路面及桥涵，时间以交工或竣工验收时间为准。 | manager_performance_summary_or_credit: sub06 第1–1页 或 sub02 第1–1页 或 sub03 第1–1页 或 sub06 第9–9页；manager_role_change_approval: sub06 第5–5页 或 sub02 第5–5页 或 sub03 第5–5页；handover_acceptance_scope_and_date: sub06 第6–6页 或 sub02 第6–6页 或 sub03 第6–6页 |
| T0012 | 项目经理业绩须提供合同。 | manager_performance_contract: sub06 第3–4页 或 sub02 第3–4页 或 sub03 第3–4页；manager_role_change_approval: sub06 第5–5页 或 sub02 第5–5页 或 sub03 第5–5页 |
| T0013 | 项目经理业绩须提供中标通知书。 | manager_performance_award_notice: sub06 第2–2页 或 sub02 第2–2页 或 sub03 第2–2页 |
| T0014 | 项目经理业绩须提供交工或竣工验收证明，须有建设、设计、监理、施工单位等盖章确认。 | acceptance_certificate_main: sub06 第6–6页 或 sub02 第6–6页 或 sub03 第6–6页；acceptance_construction_unit_seal: sub06 第7–7页 或 sub02 第7–7页 或 sub03 第7–7页；acceptance_owner_design_supervision_seals: sub06 第8–8页 或 sub02 第8–8页 或 sub03 第8–8页 |
| T0015 | 项目总工须具有公路工程相关专业中级及以上职称，相关专业包括公路工程、桥梁、公路与桥梁、交通土建、隧道（地下结构）、交通工程等。 | proposed_chief_engineer_role: sub14 第1–1页 或 sub05 第8–8页；chief_engineer_title_certificate: sub05 第12–12页 |
| T0016 | 本次招标不接受联合体投标。 | joint_venture_response: sub11 第1–1页 |
| T0018 | 投标人不得被宣城市工程建设行业行政主管部门或集中处罚部门记不良行为且截止日仍在披露期内。 | reputation_statement: sub04 第1–1页 |
| T0023 | 投标保证金为人民币50万元。 | bid_bond_receipt_amount: sub12 第1–1页 |
| T0024 | 投标保证金可采用银行保函、保证保险、担保保函等保函形式，或电汇、转账。 | bid_bond_payment_method: sub12 第1–1页 |
| T0027 | 计划工期360日历天。 | duration: sys01 第4–4页 或 sys02 第302–302页 |
| T0028 | 标段交工验收质量评定须为合格。 | quality_target: sys01 第4–4页 |
| T0030 | 安全目标为无生产安全责任事故。 | safety_target: sys01 第4–4页 |
| T0033 | 本项目不允许分包。 | subcontracting_plan_table: sub15 第1–1页 |
| T0034 | 增值税税率须按国家规定执行。 | tax_response: sys04 第859–859页 |
| T0037 | 投标有效期自投标文件递交截止日起90日。 | validity_response: sys01 第4–4页 |
| T0040 | 不得挂靠投标。 | anti_affiliation_commitment: sys01 第5–5页 |
| T0054 | 招投标活动中不得弄虚作假。 | authenticity_commitment: sys01 第4–4页 |
| T0056 | 拟派经理已任其他项目经理的，须在投标文件承诺本项目中标后签合同前能够变更至本项目全面履约。 | manager_mobilization: sys01 第4–4页 |
| T0057 | 拟派经理已任其他项目经理时，不得虚假承诺未在其他项目任项目经理。 | actual_current_assignment: sys01 第4–4页；no_assignment_declaration: sys01 第5–5页 |
| T0059 | 安全生产费为第100—700章合计报价总额的1.5%，基数不含安全生产费、建筑工程一切险及第三者责任险保险费。 | chapter_total: sys05 第863–863页；insurance_costs: sys05 第864–864页；safety_fee: sys05 第864–864页 |
| T0069 | 项目经理注册证书注册单位须与投标人名称一致。 | bidder_entity_identity: sub16 第1–1页 或 sys01 第2–2页；manager_registration_employer: sub05 第3–3页 |
| T0078 | 须配备道路工程师1人，具公路工程相关专业中级工程师及以上职称。 | proposed_road_engineer_role: sub14 第1–1页 或 sub07 第1–1页 或 sub08 第1–1页；road_engineer_title_certificate: sub08 第4–4页 |
| T0079 | 须配备桥梁工程师1人，具公路工程相关专业中级工程师及以上职称。 | proposed_bridge_engineer_role: sub14 第1–1页 或 sub07 第1–1页 或 sub08 第6–6页；bridge_engineer_title_certificate: sub08 第9–9页 |
| T0081 | 须配备专职安全员1人，具省级及以上交通运输主管部门核发的专职安全员C证。 | proposed_safety_officer_role: sub14 第1–1页 或 sub14 第1–1页 或 sub08 第16–16页 或 sub08 第22–22页；safety_officer_c_certificate: sub08 第18–18页 或 sub08 第24–24页 |
| T0099 | 投标人不得被信用中国列入失信被执行人名单。 | credit_declaration: sub04 第2–2页 |
| T0101 | 投标人自投标截止日追溯3年内不得有行贿犯罪行为。 | bribery_declaration: sub04 第2–2页 |
| T0106 | 投标人名称须与交通运输部施工资质企业名录相应企业名称完全一致。 | bidder_entity_identity: sub16 第1–1页 或 sys01 第2–2页；mot_qualification_directory_record: sub16 第11–11页 |
| T0112 | 商务文件须包括不含报价的投标函。 | non_price_letter: sys01 第4–4页 |
| T0113 | 须提交法定代表人身份证明或授权委托书。 | legal_representative_document: sub10 第1–1页 |
| T0118 | 报价文件须包括含报价的投标函。 | bid_total: sys04 第859–859页 |
| T0127 | 采用固化清单时，已标价清单投标报价须与投标函大写报价一致。 | bid_total: sys04 第859–859页；boq_total: sys05 第863–863页 |
| T0133 | 投标总价须计入安全生产费，按招标规定填写清单第100章安全生产费总额价。 | safety_fee: sys05 第864–864页；boq_total: sys05 第863–863页 |
| T0135 | 投标报价不得超过最高投标限价。 | bid_total: sys04 第859–859页 |
| T0137 | 投标有效期内不得撤销投标文件。 | validity_response: sys01 第4–4页 |
| T0152 | 信誉情况说明须与实际相符，不得弄虚作假。 | reputation_declarations_page1: sub04 第1–1页；reputation_declarations_page2: sub04 第2–2页；authenticity_commitment: sys01 第4–4页 |
| T0204 | 不得更改招标文件暂列金额。 | provisional_sum: sys05 第863–863页 |
| T0209 | 拟任项目经理业绩满足招标最低要求得6分。 | manager_performance_summary_or_credit: sub06 第1–1页 或 sub02 第1–1页 或 sub03 第1–1页 或 sub06 第9–9页；manager_role_change_approval: sub06 第5–5页 或 sub02 第5–5页 或 sub03 第5–5页；handover_acceptance_scope_and_date: sub06 第6–6页 或 sub02 第6–6页 或 sub03 第6–6页 |
| T0214 | 资产负债率75%以下得6分，75%（含）—80%（含）得4分，80%—85%（含）得2分，85%以上不得分；按原文档位保留。 | reported_balance_ratio: sub17 第1–1页 |
| T0216 | 2017年1月1日以来以交工或竣工时间为准，每增加一个单合同含长度≥15公里或金额≥5000万元的新建、改建或扩建三级及以上公路施工业绩，含路基、路面及桥涵，得4分，最高12分。 | project_01_s214_phase4: sub02 第1–1页 或 sub03 第1–1页 或 sub02 第6–6页；project_02_s214_phase5: sub02 第10–10页 或 sub03 第10–10页 或 sub02 第14–14页；project_04_daguang_interchange: sub02 第26–26页 或 sub03 第26–26页 或 sub02 第30–30页；project_05_s331_upgrade: sub02 第33–33页 或 sub03 第33–33页 或 sub02 第36–36页 |
| T0222 | 总体施工组织布置规划按工程特点、总体筹划、目标节点、关键工序、工期保障及场地布置评审：优良0.75≤F≤1分，一般0.5≤F＜0.75分，差0≤F＜0.5分。 | engineering_overview_and_characteristics: sub19 第20–20页 或 sub19 第27–27页；overall_planning_and_schedule_targets: sub19 第29–29页 或 sub19 第30–30页 或 sub19 第261–261页 或 sys02 第302–302页；key_processes_and_schedule_guarantee: sub19 第38–38页 或 sub19 第41–41页 或 sub19 第271–271页；construction_site_layout: sub19 第47–47页 或 sub19 第555–555页 |
| T0225 | 结合工程特点评审质量管理体系及措施：优良1≤F≤1.5分，一般0.5≤F＜1分，差按原文0≤F＜1分。 | quality_management_system_and_targets: sub19 第273–273页；key_process_quality_control_measures: sub19 第291–291页 |
| T0226 | 结合工程特点评审安全生产管理体系及措施：优良1≤F≤1.5分，一般0.5≤F＜1分，差按原文0≤F＜1分。 | safety_management_system_and_organization: sub19 第339–339页 或 sub19 第341–341页；safety_production_guarantee_measures: sub19 第359–359页 |
