"""
build_tender_dataset.py - Extract and construct Chinese engineering tender clause dataset.

Updates:
- Integrates directly with the newly standardized 4-tier layout (76 projects across 2022-2026).
- Scans all 53+ review approval PDFs and 76+ full tender PDFs in '01_招标文件'.
- Applies Strict Project-Level Split (Train 70%, Dev 15%, Test 15%) to prevent data leakage.
"""

import os
import re
import sys
import glob
import json
import random
from pathlib import Path
from typing import List, Dict, Any

import fitz

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Add SystemCode directory to sys.path
sys_code_dir = Path(__file__).resolve().parent.parent.parent
if str(sys_code_dir) not in sys.path:
    sys.path.insert(0, str(sys_code_dir))

from src.data.taxonomy import TENDER_CATEGORIES, LABEL2ID, ID2LABEL

# Standard curated Chinese construction bidding clauses to complement extracted data and ensure balanced coverage
CURATED_STANDARD_CLAUSES = [
    # 0: qualifications (资格与资质)
    {"text": "投标人须具备建设行政主管部门核发的建筑工程施工总承包三级及以上资质，并具备有效的安全生产许可证。", "category": "qualifications"},
    {"text": "拟派项目经理须具备水利水电工程专业二级及以上注册建造师执业资格，且具备水利行政主管部门核发的有效B类安全生产考核合格证。", "category": "qualifications"},
    {"text": "投标人拟派项目负责人目前不得有在建工程，或虽有在建工程但已办理合规变更手续且符合项目移交要求。", "category": "qualifications"},
    {"text": "投标人近三年（2023年1月1日至今）须至少具有一项合同金额在1000万元以上的市政公用工程施工类似业绩。", "category": "qualifications"},
    {"text": "财务要求：投标人须提供2022、2023、2024年度经会计师事务所审计的完整的财务审计报告，且资产负债率不得高于85%。", "category": "qualifications"},
    {"text": "信誉要求：投标人不得在国家企业信用信息公示系统或“信用中国”网站中被列入失信被执行人或严重违法失信企业名单。", "category": "qualifications"},
    {"text": "投标人及拟委任的项目经理近三年内无行贿犯罪行为记录（以中国裁判文书网查询结果为准）。", "category": "qualifications"},
    {"text": "项目技术负责人须具有工程类高级或中级技术职称，并在本单位连续缴纳社保满6个月以上证明。", "category": "qualifications"},
    {"text": "专职安全员要求：配备不少于2名专职安全生产管理人员，且持有有效的安全生产考核合格证书（C类）。", "category": "qualifications"},
    {"text": "投标人如为联合体投标，联合体牵头人必须具备市政公用工程施工总承包一级及以上资质。", "category": "qualifications"},
    {"text": "投标人资格条件：具备公路工程施工总承包二级资质，近五年内独立完成过不少于20公里的二级以上公路沥青路面施工。", "category": "qualifications"},
    {"text": "拟投入本项目的五大员（施工员、质量员、安全员、材料员、资料员）须具有有效的岗位资格证书，且与投标人签订劳动合同。", "category": "qualifications"},

    # 1: commercial (商务与报价)
    {"text": "本工程招标控制价（最高投标限价）为人民币6867000.00元（含税），投标报价高于最高投标限价的为无效投标。", "category": "commercial"},
    {"text": "投标保证金金额为人民币100000.00元整，须在递交投标文件截止时间前通过基本账户以银行电汇或保函形式递交。", "category": "commercial"},
    {"text": "工程款支付方式：合同签订且进场后拨付10%预付款；每月按实际完成合格工程量的85%支付进度款；竣工验收合格付至90%。", "category": "commercial"},
    {"text": "工程质量保证金为审定结算总价的3%，在工程缺陷责任期满24个月且无质量争议后30日内一次性无息返还。", "category": "commercial"},
    {"text": "履约担保形式：中标人应在收到中标通知书后14天内向发包人提交中标金额10%的银行履约保函或履约担保金。", "category": "commercial"},
    {"text": "工程量清单中的暂列金额为人民币500000.00元，暂估价为120000.00元，投标人不得变动，须原样计入投标总价。", "category": "commercial"},
    {"text": "安全文明施工费、规费、税金等为不可竞争费用，必须严格按照国家和地方工程造价管理规定足额计提，不得优惠下浮。", "category": "commercial"},
    {"text": "本合同采用固定单价合同承包方式，除图纸变更和政策性调价外，综合单价在合同执行期间不予调整。", "category": "commercial"},
    {"text": "主要材料价格风险控制：当主要建筑材料（钢筋、水泥、混凝土）价格涨跌幅度超过基期价格±5%时，超出部分按规定调差。", "category": "commercial"},
    {"text": "竣工结算付款：待第三方政府审计机关或工程造价咨询机构出具正式结算审计报告后，付至最终审定价款的97%。", "category": "commercial"},

    # 2: schedule (进度与工期)
    {"text": "计划工期：本工程总工期为252个日历天，计划开工日期以监理人签发的开工令为准。", "category": "schedule"},
    {"text": "节点工期要求：基础工程必须在开工后60日历天内全部完工，主体结构必须在开工后150日历天内封顶。", "category": "schedule"},
    {"text": "工期延误罚则：因承包人自身原因造成工期拖延的，每延误一天，承包人须向发包人支付违约金人民币2000元/天。", "category": "schedule"},
    {"text": "工期逾期违约金累计最高限额为签约合同总价款的10%，若延误超过30天发包人有权单方解除合同。", "category": "schedule"},
    {"text": "承包人应在开工前7天向发包人和监理人提交详细的施工进度计划和网络图，并按月编制滚动进度计划。", "category": "schedule"},
    {"text": "因不可抗力或发包人原因导致关键线路停工的，经监理人和发包人核定后，工期予以顺延，但不补偿间接停工损失。", "category": "schedule"},
    {"text": "严禁随意压缩合理工期，施工组织设计中必须包含应对农忙期、汛期及极端冬季天气施工的进度保障措施。", "category": "schedule"},
    {"text": "施工进度滞后纠偏：当实际进度落后于计划进度达14天以上时，承包人必须在3天内提交赶工方案和资源增加方案。", "category": "schedule"},
    {"text": "工期考核奖惩机制：提前竣工通过验收且质量优良的，发包人按提前天数给予每日1000元的工期提前奖励。", "category": "schedule"},

    # 3: formality (形式与提交)
    {"text": "投标文件递交截止时间为2026年10月15日09时00分，逾期送达或未成功上传至电子交易系统的投标文件将被拒绝接收。", "category": "formality"},
    {"text": "法定代表人身份证明及法定代表人授权委托书必须加盖投标人公章，并由法定代表人亲笔签名或加盖印鉴。", "category": "formality"},
    {"text": "投标文件必须严格按照招标文件第八章给出的投标文件格式编制，目录清晰，逐页编码，未按规定签章的作废标处理。", "category": "formality"},
    {"text": "本次招标不接受联合体投标，不得转包或者违法分包主体工程。", "category": "formality"},
    {"text": "如为联合体投标，必须随投标文件提交联合体协议书原件，明确各方承担的工作范围、责任及收益分成比例。", "category": "formality"},
    {"text": "投标有效期为自投标文件递交截止之日起算90日历天，在此期间投标人不得撤回投标文件。", "category": "formality"},
    {"text": "投标文件应使用加密的CA数字证书进行电子签名和盖章，且投标文件制作机器码不得与同标段其他投标人雷同。", "category": "formality"},
    {"text": "中小企业扶持政策：符合条件的中小微企业须如实提交《中小企业声明函》，以便在评标时享受相应的价格扣除优惠。", "category": "formality"},
    {"text": "纸质投标文件应按正本一份、副本两份密封在专用包装袋内，并在封口处加盖骑缝公章。", "category": "formality"},
    {"text": "投标函及投标函附录为投标文件的核心文件，若缺少投标函或投标函未盖章，属于重大偏差，评标委员会应当否决其投标。", "category": "formality"},

    # 4: technical (技术与方案)
    {"text": "工程承包范围：南山矿高压架空线路清障、头顶库西侧汇水改造、生产车间办公环境整治及城门峒大坝新建值班室等14项工程。", "category": "technical"},
    {"text": "工程质量标准：必须达到国家现行施工验收规范规定的【合格】标准，一次性验收合格率达100%。", "category": "technical"},
    {"text": "施工组织设计应重点阐述深基坑开挖与支护方案、大体积混凝土浇筑温控措施、降排水措施及现场平面布置图。", "category": "technical"},
    {"text": "主要施工机械设备配备：要求配备不低于3台320型履带式挖掘机、2台装载机及相应的试验检测仪器，性能须良好。", "category": "technical"},
    {"text": "环境保护与文明施工要求：施工现场必须设置连续硬质密闭围挡，落实施工便道硬化、裸土覆盖、自动喷淋等六个百分百抑尘措施。", "category": "technical"},
    {"text": "安全生产管理体系：严格贯彻执行安全生产责任制，现场必须配备专职安全巡检人员，特种作业人员必须持特种作业操作证上岗。", "category": "technical"},
    {"text": "工程材料与设备质量标准：所有进场钢筋、水泥及商品混凝土必须具备出厂合格证及质量检验报告，按批次进行见证取样复检。", "category": "technical"},
    {"text": "技术规范：本工程设计与施工必须严格执行《水利水电建设工程验收规程》(SL223)及《混凝土结构工程施工质量验收规范》。", "category": "technical"},
    {"text": "雨季与防汛应急预案：河道疏浚工程施工单位必须编制切实可行的超标洪水防汛应急预案，备齐防汛抢险沙袋、抽水泵等物资。", "category": "technical"},
    {"text": "临时用电安全技术规范：现场临时配电系统必须采用TN-S接零保护系统，严格实行“三级配电两级保护”和“一机一闸一漏一箱”。", "category": "technical"},

    # 5: general (其他与通用)
    {"text": "合同条款响应：投标人确认完全响应招标文件第四章通用合同条款与专用合同条款，除偏离表载明事项外无任何商务与技术偏离。", "category": "general"},
    {"text": "争议解决方式：因本合同引起的或与本合同有关的任何争议，双方应友好协商解决；协商不成的，向项目所在地人民法院提起诉讼。", "category": "general"},
    {"text": "双方约定争议解决方式为向合肥仲裁委员会申请仲裁，仲裁裁决是终局的，对双方当事人均具有法律约束力。", "category": "general"},
    {"text": "工程款优先受偿权：任何条款不得强制承包人放弃《民法典》第八百零七条赋予的建设工程价款优先受偿权。", "category": "general"},
    {"text": "保密义务：未经发包人书面同意，承包人不得将发包人提供的施工图纸、地质勘察资料及工程技术秘密透露给任何第三方。", "category": "general"},
    {"text": "不可抗力约定：因地震、台风、百年一遇洪水等不可抗力事件导致工程损坏和人员伤亡的，由发承包双方按法定原则分担损失。", "category": "general"},
    {"text": "知识产权约定：发包人提供给承包人的图纸、技术文件等其知识产权归发包人所有，承包人提供的专利技术知识产权归承包人所有。", "category": "general"},
    {"text": "禁止转包与违法分包：承包人不得将其承包的全部工程转包给第三人，也不得将其承包的工程肢解以后以分包的名义分别转让给第三人。", "category": "general"}
]

def clean_clause_text(text: str) -> str:
    """Removes weird characters, redundant newlines and whitespace."""
    if not text:
        return ""
    text = re.sub(r'[\r\n\t]+', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    return text

def extract_from_review_pdf(pdf_path: str, project_name: str) -> List[Dict[str, Any]]:
    """Extracts structured domain clauses from contractor internal review PDFs."""
    clauses = []
    try:
        doc = fitz.open(pdf_path)
        full_text = "\n".join([page.get_text() for page in doc])
    except Exception as e:
        return clauses

    # 1. Scope / Technical
    m_scope = re.search(r'招标范围：\s*(.*?)\s*合同工期条款', full_text, re.DOTALL)
    if m_scope:
        scope_text = clean_clause_text(m_scope.group(1))
        if len(scope_text) > 15:
            clauses.append({
                "project": project_name,
                "text": f"工程承包招标范围：{scope_text}",
                "category": "technical",
                "source": "review_scope"
            })

    # 2. Schedule / Penalty
    m_pen = re.search(r'其他违约罚则：\s*(.*?)\s*合规性审查', full_text, re.DOTALL)
    if m_pen:
        pen_text = clean_clause_text(m_pen.group(1))
        if len(pen_text) > 15:
            clauses.append({
                "project": project_name,
                "text": f"工期延误违约罚则：{pen_text}",
                "category": "schedule",
                "source": "review_penalty"
            })

    # 3. Commercial / Payment
    m_pay = re.search(r'支付条款原文：\s*(.*?)\s*付款方式：', full_text, re.DOTALL)
    if m_pay:
        pay_text = clean_clause_text(m_pay.group(1))
        if len(pay_text) > 15:
            clauses.append({
                "project": project_name,
                "text": f"工程款支付条款：{pay_text}",
                "category": "commercial",
                "source": "review_payment"
            })

    # 4. Commercial / Guarantee
    m_gua = re.search(r'履约担保条款原文：\s*(.*?)\s*是否有预付款保函', full_text, re.DOTALL)
    if m_gua:
        gua_text = clean_clause_text(m_gua.group(1))
        if len(gua_text) > 10:
            clauses.append({
                "project": project_name,
                "text": f"履约担保要求：{gua_text}",
                "category": "commercial",
                "source": "review_guarantee"
            })

    # 5. Formality / Deadline & Consortium
    m_dl = re.search(r'文件递交截止日期\s*：\s*(\d{4}/\d{2}/\d{2}|\d{4}-\d{2}-\d{2})', full_text)
    if m_dl:
        clauses.append({
            "project": project_name,
            "text": f"投标文件递交截止时间要求：本工程文件递交截止日期为 {m_dl.group(1)}，逾期递交视为放弃投标。",
            "category": "formality",
            "source": "review_deadline"
        })

    m_co = re.search(r'是否联合体项目\s*：\s*(是|否)', full_text)
    if m_co:
        is_co = m_co.group(1)
        desc = "本项目接受联合体投标，联合体牵头人承担主要施工工作。" if is_co == "是" else "本项目不接受联合体投标，不得转包或者违法分包主体工程。"
        clauses.append({
            "project": project_name,
            "text": desc,
            "category": "formality",
            "source": "review_consortium"
        })

    # 6. General / Dispute
    m_pri = re.search(r'要求放弃优先受\s*偿权：\s*(是|否)', full_text)
    if m_pri:
        desc = "合同通用条款约定：发包人不得强制要求承包人放弃工程价款优先受偿权，争议协商不成的向项目属地法院起诉。"
        clauses.append({
            "project": project_name,
            "text": desc,
            "category": "general",
            "source": "review_dispute"
        })

    return clauses

def extract_from_full_tender_pdf(pdf_path: str, project_name: str) -> List[Dict[str, Any]]:
    """Extracts qualifications, schedule, pricing, formality, and technical rules from complete tender PDFs."""
    clauses = []
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return clauses

    # Scan pages for specific sections
    for i, page in enumerate(doc):
        text = page.get_text()
        
        # Qualifications extraction
        if "资格要求" in text or "资质要求" in text:
            paragraphs = text.split("\n\n") if "\n\n" in text else text.split("\n")
            current_clause = []
            for line in paragraphs:
                l = clean_clause_text(line)
                if any(k in l for k in ["施工总承包", "二级", "三级", "一级", "建造师", "项目经理", "安全生产许可证", "失信", "业绩"]):
                    current_clause.append(l)
                elif current_clause and len(" ".join(current_clause)) > 25:
                    c_text = " ".join(current_clause)
                    if len(c_text) < 300:
                        clauses.append({
                            "project": project_name,
                            "text": c_text,
                            "category": "qualifications",
                            "source": f"page_{i+1}_qual"
                        })
                    current_clause = []

        # Schedule extraction
        if "工期" in text and ("日历天" in text or "开工" in text):
            for line in text.splitlines():
                l = clean_clause_text(line)
                if ("计划工期" in l or "工期要求" in l or "总工期" in l) and "日历天" in l and len(l) > 12:
                    clauses.append({
                        "project": project_name,
                        "text": l,
                        "category": "schedule",
                        "source": f"page_{i+1}_sched"
                    })

        # Commercial extraction
        if "最高投标限价" in text or "招标控制价" in text or "投标保证金" in text:
            for line in text.splitlines():
                l = clean_clause_text(line)
                if ("最高投标限价" in l or "招标控制价" in l or "投标保证金金额" in l) and any(char.isdigit() for char in l) and len(l) > 12:
                    clauses.append({
                        "project": project_name,
                        "text": l,
                        "category": "commercial",
                        "source": f"page_{i+1}_comm"
                    })

        # Formality extraction (Signatures, seals, deadlines, consortium, packaging)
        if any(k in text for k in ["法定代表人", "委托代理人", "签字或盖章", "电子公章", "密封", "联合体协议"]):
            for line in text.splitlines():
                l = clean_clause_text(line)
                if any(k in l for k in ["法定代表人或其委托代理人签字", "加盖单位章", "电子公章", "密封包装", "不接受联合体", "联合体协议书", "投标有效期为"]) and 15 <= len(l) <= 180:
                    clauses.append({
                        "project": project_name,
                        "text": l,
                        "category": "formality",
                        "source": f"page_{i+1}_form"
                    })

        # Technical extraction (Quality standards, specs, safety, environment)
        if any(k in text for k in ["质量标准", "验收规范", "施工规范", "安全文明施工", "扬尘治理", "施工组织设计"]):
            for line in text.splitlines():
                l = clean_clause_text(line)
                if any(k in l for k in ["质量标准：", "验收合格", "工程质量达到", "安全文明施工措施", "扬尘管控措施", "施工组织设计应包括", "符合现行国家施工质量验收"]) and 15 <= len(l) <= 200:
                    clauses.append({
                        "project": project_name,
                        "text": l,
                        "category": "technical",
                        "source": f"page_{i+1}_tech"
                    })

        # General extraction (Dispute, jurisdiction, deviation, force majeure, confidentiality)
        if any(k in text for k in ["争议解决", "仲裁", "诉讼", "管辖", "偏离表", "不可抗力", "保密"]):
            for line in text.splitlines():
                l = clean_clause_text(line)
                if any(k in l for k in ["向仲裁委员会申请仲裁", "向人民法院提起诉讼", "争议裁判管辖地", "偏离应当符合招标文件", "因不可抗力", "保密义务"]) and 15 <= len(l) <= 180:
                    clauses.append({
                        "project": project_name,
                        "text": l,
                        "category": "general",
                        "source": f"page_{i+1}_gen"
                    })

    return clauses

def build_chinese_tender_dataset(workspace_dir: Path, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    all_clauses = []
    
    # Locate all 76 projects
    from src.data.organize_projects import find_all_projects
    projects = find_all_projects(workspace_dir / "data_extracted")
    print(f"[*] 正在从全库 {len(projects)} 个规范化工程项目中提取条款...")

    review_count = 0
    tender_count = 0

    for p in projects:
        proj_name = p.name
        # 1. 扫描 01_招标文件 和 03_参考与归档 中的评审文件
        review_files = list(p.glob("**/01_招标文件/**/招标文件评审*.pdf")) + list(p.glob("**/03_参考与归档/**/*评审*.pdf"))
        for rf in review_files:
            c_list = extract_from_review_pdf(str(rf), proj_name)
            all_clauses.extend(c_list)
            review_count += 1

        # 2. 扫描 01_招标文件 中的招标文件正文及公告
        tender_files = list(p.glob("**/01_招标文件/**/招标文件*.pdf")) + list(p.glob("**/01_招标文件/**/招标公告.pdf"))
        tender_files = [f for f in tender_files if "评审" not in f.name]
        for tf in tender_files:
            c_list = extract_from_full_tender_pdf(str(tf), proj_name)
            all_clauses.extend(c_list)
            tender_count += 1

    print(f"[*] 扫描解析完成: 处理评审单 {review_count} 份，招标文件 {tender_count} 份。")

    # 3. Add curated standard clauses to cover edge cases and ensure balanced representation
    print(f"[*] 加入精选高标准条款 {len(CURATED_STANDARD_CLAUSES)} 条...")
    for item in CURATED_STANDARD_CLAUSES:
        all_clauses.append({
            "project": "Standard_Model_Specs",
            "text": item["text"],
            "category": item["category"],
            "source": "standard_spec"
        })
        
    # Deduplicate text
    seen_texts = set()
    unique_clauses = []
    for c in all_clauses:
        t = clean_clause_text(c["text"])
        if len(t) >= 12 and t not in seen_texts:
            seen_texts.add(t)
            c["text"] = t
            c["label"] = LABEL2ID[c["category"]]
            c["category_zh"] = TENDER_CATEGORIES[c["label"]]["name_zh"]
            unique_clauses.append(c)

    print(f"[OK] 去重后有效条款总数: {len(unique_clauses)} 条")
    
    # Distribution check
    cat_counts = {}
    for c in unique_clauses:
        cat = c["category_zh"]
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    print("[*] 6 大合规分类分布情况:")
    for cat, cnt in sorted(cat_counts.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {cnt} ({cnt/len(unique_clauses)*100:.1f}%)")

    # 4. Strict Project-Level Split (基于项目的物理隔离划分)
    std_clauses = [c for c in unique_clauses if c["project"] == "Standard_Model_Specs"]
    real_clauses = [c for c in unique_clauses if c["project"] != "Standard_Model_Specs"]
    
    proj_to_clauses = {}
    for c in real_clauses:
        proj_to_clauses.setdefault(c["project"], []).append(c)
    
    sorted_projs = sorted(proj_to_clauses.keys(), key=lambda p: len(proj_to_clauses[p]), reverse=True)
    
    train_clauses, dev_clauses, test_clauses = [], [], []
    train_target = len(real_clauses) * 0.70
    dev_target = len(real_clauses) * 0.15
    
    for p in sorted_projs:
        p_clauses = proj_to_clauses[p]
        if len(train_clauses) < train_target:
            train_clauses.extend(p_clauses)
        elif len(dev_clauses) < dev_target:
            dev_clauses.extend(p_clauses)
        else:
            test_clauses.extend(p_clauses)
            
    # Distribute curated clauses (70% train, 15% dev, 15% test)
    random.seed(42)
    random.shuffle(std_clauses)
    n_std_train = int(len(std_clauses) * 0.70)
    n_std_dev = int(len(std_clauses) * 0.15)
    train_clauses += std_clauses[:n_std_train]
    dev_clauses += std_clauses[n_std_train:n_std_train + n_std_dev]
    test_clauses += std_clauses[n_std_train + n_std_dev:]
    
    print(f"\n[*] 划分结果 (严格项目隔离):")
    print(f"  Train 集: {len(train_clauses)} 条")
    print(f"  Dev   集: {len(dev_clauses)} 条")
    print(f"  Test  集: {len(test_clauses)} 条 (全新盲测项目)")

    # Save splits
    for split_name, dataset in [("train", train_clauses), ("dev", dev_clauses), ("test", test_clauses)]:
        out_file = output_dir / f"chinese_tender_{split_name}.jsonl"
        with open(out_file, "w", encoding="utf-8") as f:
            for item in dataset:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"[OK] 保存成功 -> {out_file.name}")
        
    return unique_clauses

if __name__ == "__main__":
    ws = Path(__file__).resolve().parent.parent.parent.parent
    proc_dir = Path(__file__).resolve().parent.parent.parent / "data" / "processed"
    build_chinese_tender_dataset(ws, proc_dir)
