"""
parse_chapter1.py - 基于章节切片的精准抽取【第一章 招标公告】

目标项目: 7.11 兆河生态清洁小流域建设工程（槐林段）
输入文件: 01_招标文件/招标文件正文.pdf (第3页至第7页)
输出文件:
  1. SystemCode/data/processed/clauses/7.11_ch1.json (系统集中治理目录)
  2. data_extracted/2022年投标项目/7.11-.../01_招标文件/ch1.json (项目本地目录)
"""

import sys
import json
import re
from pathlib import Path
from typing import List, Dict, Any

import fitz

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def clean_text(s: str) -> str:
    """清理字符串内的多余换行与多余空格"""
    if not s:
        return ""
    # 将多个连续空白（含换行）替换为单个空格
    s = re.sub(r'[\r\n\t]+', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def parse_chapter1(pdf_path: Path) -> Dict[str, Any]:
    doc = fitz.open(pdf_path)
    
    # 第一章位于PDF第3页到第7页 (fitz 索引 2 到 6)
    pages_clean = []
    for p_no in range(3, 8):
        raw_text = doc[p_no - 1].get_text()
        # 移除页顶单独的页码数字
        cleaned_page = re.sub(r'^\s*\d+\s*\n', '', raw_text)
        pages_clean.append((p_no, cleaned_page))
    
    # 拼接整个第一章完整文本
    full_text = "\n".join([txt for _, txt in pages_clean])

    # 10大主章节正则表达式定位
    sec_pats = [
        r'(?m)^\s*1\.\s*招标条件',
        r'(?m)^\s*2\.\s*项目概况与招标范围',
        r'(?m)^\s*3\.\s*投标人资格要求',
        r'(?m)^\s*4\.\s*招标文件的获取',
        r'(?m)^\s*5\.\s*投标文件的递交',
        r'(?m)^\s*6\.\s*开标时间及地点',
        r'(?m)^\s*7\.\s*发布公告的媒介',
        r'(?m)^\s*8\.\s*联系方式',
        r'(?m)^\s*9\.\s*其他事项说明',
        r'(?m)^\s*10\.\s*投标保证金账户'
    ]
    
    # 寻找每个主章节的起始偏移量
    splits = []
    for p in sec_pats:
        m = re.search(p, full_text)
        if not m:
            raise ValueError(f"未能在文档中匹配到主章节标题: {p}")
        splits.append(m.start())
    splits.append(len(full_text))

    # 获取10个章节的独立文本切片
    sec_texts = [full_text[splits[i]:splits[i+1]].strip() for i in range(10)]

    clauses: List[Dict[str, Any]] = []
    c_id = 1

    def add_clause(num: str, sec_title: str, item_title: str, content: str,
                   cat: str, cat_zh: str, is_check: bool, page: int, extra: Dict = None):
        nonlocal c_id
        item = {
            "clause_id": f"CH1-{c_id:03d}",
            "clause_num": num,
            "section_title": sec_title,
            "item_title": item_title,
            "content": clean_text(content),
            "category": cat,
            "category_zh": cat_zh,
            "is_compliance_check": is_check,
            "page": page
        }
        if extra:
            item.update(extra)
        clauses.append(item)
        c_id += 1

    # ================= 1. 招标条件 =================
    s1 = sec_texts[0]
    sec1_defs = [
        ("1.1", "项目名称", r"1\.1\s*项目名称[:：]\s*(.*?)(?=1\.2|\Z)"),
        ("1.2", "项目审批核准机关", r"1\.2\s*项目审批、核准或备案机关名称[:：]\s*(.*?)(?=1\.3|\Z)"),
        ("1.3", "批文名称及编号", r"1\.3\s*批文名称及编号[:：]\s*(.*?)(?=1\.4|\Z)"),
        ("1.4", "招标人", r"1\.4\s*招标人[:：]\s*(.*?)(?=1\.5|\Z)"),
        ("1.5", "项目业主", r"1\.5\s*项目业主[:：]\s*(.*?)(?=1\.6|\Z)"),
        ("1.6", "资金来源", r"1\.6\s*资金来源[:：]\s*(.*?)(?=1\.7|\Z)"),
        ("1.7", "项目出资比例", r"1\.7\s*项目出资比例[:：]\s*(.*?)(?=\Z)"),
    ]
    for num, item_t, pat in sec1_defs:
        m = re.search(pat, s1, re.DOTALL)
        content = m.group(1) if m else ""
        add_clause(num, "1. 招标条件", item_t, content, "general", "其他与通用", False, 3)

    # ================= 2. 项目概况与招标范围 =================
    s2 = sec_texts[1]
    sec2_defs = [
        ("2.1", "招标项目名称", r"2\.1\s*招标项目名称[:：]\s*(.*?)(?=2\.2|\Z)", "general", "其他与通用", False, 3),
        ("2.2", "招标项目编号", r"2\.2\s*招标项目编号[:：]\s*(.*?)(?=2\.3|\Z)", "general", "其他与通用", False, 3),
        ("2.3", "标段划分", r"2\.3\s*标段划分[:：]\s*(.*?)(?=2\.4|\Z)", "general", "其他与通用", False, 3),
        ("2.4", "招标项目标段编号", r"2\.4\s*招标项目标段编号[:：]\s*(.*?)(?=2\.5|\Z)", "general", "其他与通用", False, 3),
        ("2.5", "建设地点", r"2\.5\s*建设地点[:：]\s*(.*?)(?=2\.6|\Z)", "general", "其他与通用", False, 3),
        ("2.6", "建设规模", r"2\.6\s*建设规模[:：]\s*(.*?)(?=2\.7|\Z)", "technical", "技术与方案", False, 3),
        ("2.7", "合同估算价", r"2\.7\s*合同估算价[:：]\s*(.*?)(?=2\.8|\Z)", "commercial", "商务与报价", True, 3),
        ("2.8", "计划工期", r"2\.8\s*计划工期[:：]\s*(.*?)(?=2\.9|\Z)", "schedule", "进度与工期", True, 3),
        ("2.9", "招标范围", r"2\.9\s*招标范围[:：]\s*(.*?)(?=2\.10|\Z)", "technical", "技术与方案", True, 4),
        ("2.10", "项目类别", r"2\.10\s*项目类别[:：]\s*(.*?)(?=2\.11|\Z)", "general", "其他与通用", False, 4),
        ("2.11", "其他说明", r"2\.11\s*其他[:：]\s*(.*?)(?=\Z)", "general", "其他与通用", False, 4),
    ]
    for num, item_t, pat, cat, cat_zh, is_chk, pg in sec2_defs:
        m = re.search(pat, s2, re.DOTALL)
        content = m.group(1) if m else ""
        add_clause(num, "2. 项目概况与招标范围", item_t, content, cat, cat_zh, is_chk, pg)

    # ================= 3. 投标人资格要求 =================
    s3 = sec_texts[2]
    sec3_defs = [
        ("3.1", "投标人资质要求", r"3\.1\s*投标人资质要求[:：]\s*(.*?)(?=3\.2|\Z)", "qualifications", "资格与资质", True, 4),
        ("3.2", "投标人业绩要求", r"3\.2\s*投标人业绩要求[:：]\s*(.*?)(?=3\.3|\Z)", "qualifications", "资格与资质", True, 4),
        ("3.3", "项目经理资格要求", r"3\.3\s*项目经理资格要求[:：]\s*(.*?)(?=3\.4|\Z)", "qualifications", "资格与资质", True, 4),
        ("3.4", "投标人信誉要求", r"3\.4\s*投标人须符合下列情形之一[:：]\s*(.*?)(?=3\.5|\Z)", "qualifications", "资格与资质", True, 4),
        ("3.5", "联合体投标要求", r"3\.5\s*(本次招标.*?)(?=3\.6|\Z)", "formality", "形式与提交", True, 4),
        ("3.6", "资格审查方式", r"3\.6\s*(本次招标实行资格后审.*?)(?=\Z)", "formality", "形式与提交", True, 5),
    ]
    for num, item_t, pat, cat, cat_zh, is_chk, pg in sec3_defs:
        m = re.search(pat, s3, re.DOTALL)
        content = m.group(1) if m else ""
        add_clause(num, "3. 投标人资格要求", item_t, content, cat, cat_zh, is_chk, pg)

    # ================= 4. 招标文件的获取 =================
    s4 = sec_texts[3]
    sec4_defs = [
        ("4.1", "招标文件获取时间", r"4\.1\s*获取时间[:：]\s*(.*?)(?=4\.2|\Z)", "formality", "形式与提交", False, 5),
        ("4.2", "招标文件获取方式", r"4\.2\s*获取方式[:：]\s*(.*?)(?=4\.3|\Z)", "formality", "形式与提交", False, 5),
        ("4.3", "招标文件价格", r"4\.3\s*招标文件价格[:：]\s*(.*?)(?=\Z)", "commercial", "商务与报价", False, 5),
    ]
    for num, item_t, pat, cat, cat_zh, is_chk, pg in sec4_defs:
        m = re.search(pat, s4, re.DOTALL)
        content = m.group(1) if m else ""
        add_clause(num, "4. 招标文件的获取", item_t, content, cat, cat_zh, is_chk, pg)

    # ================= 5. 投标文件的递交 =================
    s5 = sec_texts[4]
    m5 = re.search(r"5\.\s*投标文件的递交\s*(.*?)(?=\Z)", s5, re.DOTALL)
    content5 = m5.group(1) if m5 else s5
    add_clause("5", "5. 投标文件的递交", "投标文件递交截止时间及方式", content5, "formality", "形式与提交", True, 5)

    # ================= 6. 开标时间及地点 =================
    s6 = sec_texts[5]
    m6_1 = re.search(r"6\.1\s*开标时间[:：]\s*(.*?)(?=6\.2|\Z)", s6, re.DOTALL)
    content6_1 = m6_1.group(1) if m6_1 else ""
    add_clause("6.1", "6. 开标时间及地点", "开标时间", content6_1, "formality", "形式与提交", True, 5)

    m6_2 = re.search(r"6\.2\s*开标地点[:：]\s*(.*?)(?=\Z)", s6, re.DOTALL)
    content6_2 = m6_2.group(1) if m6_2 else ""
    add_clause("6.2", "6. 开标时间及地点", "开标地点", content6_2, "general", "其他与通用", False, 5)

    # ================= 7. 发布公告的媒介 =================
    s7 = sec_texts[6]
    m7 = re.search(r"7\.\s*发布公告的媒介\s*(.*?)(?=\Z)", s7, re.DOTALL)
    content7 = m7.group(1) if m7 else s7
    add_clause("7", "7. 发布公告的媒介", "发布媒介", content7, "general", "其他与通用", False, 5)

    # ================= 8. 联系方式 =================
    s8 = sec_texts[7]
    sec8_defs = [
        ("8.1", "招标人联系方式", r"8\.1\s*招标人\s*(.*?)(?=8\.2|\Z)", 5),
        ("8.2", "招标代理机构联系方式", r"8\.2\s*招标代理机构\s*(.*?)(?=8\.3|\Z)", 6),
        ("8.3", "电子交易系统联系方式", r"8\.3\s*电子交易系统\s*(.*?)(?=8\.4|\Z)", 6),
        ("8.4", "电子服务系统联系方式", r"8\.4\s*电子服务系统\s*(.*?)(?=8\.5|\Z)", 6),
        ("8.5", "监督管理机构联系方式", r"8\.5\s*招标监督管理机构\s*(.*?)(?=\Z)", 6),
    ]
    for num, item_t, pat, pg in sec8_defs:
        m = re.search(pat, s8, re.DOTALL)
        content = m.group(1) if m else ""
        add_clause(num, "8. 联系方式", item_t, content, "general", "其他与通用", False, pg)

    # ================= 9. 其他事项说明 =================
    s9 = sec_texts[8]
    sec9_defs = [
        ("9.1", "获取时间与网络风险提示", r"9\.1\s*(.*?)(?=9\.2|\Z)", 6),
        ("9.2", "业务咨询时间说明", r"9\.2\s*(.*?)(?=9\.3|\Z)", 6),
        ("9.3", "疫情防控交易服务指南", r"9\.3\s*(.*?)(?=\Z)", 6),
    ]
    for num, item_t, pat, pg in sec9_defs:
        m = re.search(pat, s9, re.DOTALL)
        content = m.group(1) if m else ""
        # 针对9.3中换行分割的中文特殊文本做净化
        if num == "9.3":
            content = re.sub(r'间\s*交\s*易\s*服\s*务\s*指\s*南', '间交易服务指南', content)
            content = re.sub(r'官\s*网\s*链\s*接', '官网链接', content)
        add_clause(num, "9. 其他事项说明", item_t, content, "general", "其他与通用", False, pg)

    # ================= 10. 投标保证金账户 =================
    s10 = sec_texts[9]
    # 结构化解析保证金银行账户
    bank_accounts = []
    # 账户1: 光大银行
    m_gd = re.search(r"光大银行合肥阜南路支行\s*户名：\s*(.*?)\s*账号：\s*(\d+)\s*开户银行：\s*(光大银行[^\s\n]+)", s10)
    if m_gd:
        bank_accounts.append({
            "bank_name": "光大银行合肥阜南路支行",
            "account_name": clean_text(m_gd.group(1)),
            "account_no": m_gd.group(2),
            "bank_branch": clean_text(m_gd.group(3))
        })
    # 账户2: 徽商银行
    m_hs = re.search(r"徽商银行合肥蜀山支行\s*户名：\s*(.*?)\s*账号：\s*(\d+)\s*开户银行：\s*(徽商银行[^\s\n]+)", s10)
    if m_hs:
        bank_accounts.append({
            "bank_name": "徽商银行合肥蜀山支行",
            "account_name": clean_text(m_hs.group(1)),
            "account_no": m_hs.group(2),
            "bank_branch": clean_text(m_hs.group(3))
        })
    # 账户3: 建设银行
    m_ccb = re.search(r"建行合肥庐阳支行营业部\s*户名：\s*(.*?)\s*账号：\s*([0-9\-]+).*?开户银行：\s*(建行[^\s\n]+)", s10, re.DOTALL)
    if m_ccb:
        bank_accounts.append({
            "bank_name": "建行合肥庐阳支行营业部",
            "account_name": clean_text(m_ccb.group(1)),
            "account_no": clean_text(m_ccb.group(2)),
            "bank_branch": clean_text(m_ccb.group(3))
        })

    add_clause(
        "10",
        "10. 投标保证金账户",
        "投标保证金收款指定账户",
        s10,
        "commercial",
        "商务与报价",
        True,
        7,
        extra={"structured_bank_accounts": bank_accounts}
    )

    doc_data = {
        "project_id": "7.11_zhaohe",
        "project_name": "7.11-兆河生态清洁小流域建设工程（巢湖市段）——水环境治理工程（槐林段）",
        "document_type": "招标文件",
        "chapter_num": "第一章",
        "chapter_name": "招标公告（电子招标投标）",
        "total_clauses": len(clauses),
        "compliance_check_clauses": len([c for c in clauses if c["is_compliance_check"]]),
        "clauses": clauses
    }
    return doc_data


def main():
    proj_root = Path(r"data_extracted/2022年投标项目/7.11-兆河生态清洁小流域建设工程（巢湖市段）——水环境治理工程（槐林段）")
    tender_pdf = proj_root / "01_招标文件" / "招标文件正文.pdf"
    
    print(f"[*] 正在基于切片解析《招标文件正文.pdf》【第一章 招标公告】...")
    doc_data = parse_chapter1(tender_pdf)
    clauses = doc_data["clauses"]
    print(f"[OK] 第一章共解析抽取出: {len(clauses)} 个完整条款条目。")
    print(f"[OK] 其中关键核验条款: {doc_data['compliance_check_clauses']} 项。")

    # 输出路径 1: 系统集中处理目录 (SystemCode/data/processed/clauses/7.11_ch1.json)
    out_dir = Path("SystemCode/data/processed/clauses")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "7.11_ch1.json"
    
    # 输出路径 2: 项目本地标准化路径 (01_招标文件/ch1.json)
    local_file = proj_root / "01_招标文件" / "ch1.json"

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(doc_data, f, ensure_ascii=False, indent=2)
    print(f"[OK] 系统集中标准化输出已保存: {out_file}")

    with open(local_file, "w", encoding="utf-8") as f:
        json.dump(doc_data, f, ensure_ascii=False, indent=2)
    print(f"[OK] 项目本地标准化输出已保存: {local_file}")


if __name__ == "__main__":
    main()
