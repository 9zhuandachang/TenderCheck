"""
parse_chapter1.py - 极简结构化抽取【第一章 招标公告】实质性审查条款

数据格式:
[
  {
    "text": "条款完整文本内容",
    "category": "qualifications | commercial | schedule | formality | technical | general",
    "source": "第一章"
  }
]
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
    """清理多余空白与换行"""
    if not s:
        return ""
    s = re.sub(r'[\r\n\t]+', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def extract_chapter1_core_clauses(pdf_path: Path) -> List[Dict[str, str]]:
    doc = fitz.open(pdf_path)
    
    # 提取第3页至第7页（第一章招标公告范围）
    pages_clean = []
    for p_no in range(3, 8):
        raw_text = doc[p_no - 1].get_text()
        cleaned_page = re.sub(r'^\s*\d+\s*\n', '', raw_text)
        pages_clean.append(cleaned_page)
    
    full_text = "\n".join(pages_clean)

    # 章节定位切片
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
    splits = [re.search(p, full_text).start() for p in sec_pats] + [len(full_text)]
    sec_texts = [full_text[splits[i]:splits[i+1]].strip() for i in range(10)]

    s2 = sec_texts[1]
    s3 = sec_texts[2]
    s5 = sec_texts[4]
    s10 = sec_texts[9]

    clauses: List[Dict[str, str]] = []

    # 1. 建设规模与招标范围 (技术与方案)
    m_scope = re.search(r"2\.9\s*招标范围[:：]\s*(.*?)(?=2\.10|\Z)", s2, re.DOTALL)
    if m_scope:
        clauses.append({
            "text": f"招标范围与建设规模：{clean_text(m_scope.group(1))}",
            "category": "technical",
            "source": "第一章"
        })

    # 2. 合同估算价 (商务与报价)
    m_price = re.search(r"2\.7\s*合同估算价[:：]\s*(.*?)(?=2\.8|\Z)", s2, re.DOTALL)
    if m_price:
        clauses.append({
            "text": f"合同估算价：{clean_text(m_price.group(1))}",
            "category": "commercial",
            "source": "第一章"
        })

    # 3. 计划工期 (进度与工期)
    m_period = re.search(r"2\.8\s*计划工期[:：]\s*(.*?)(?=2\.9|\Z)", s2, re.DOTALL)
    if m_period:
        clauses.append({
            "text": f"计划工期：{clean_text(m_period.group(1))}",
            "category": "schedule",
            "source": "第一章"
        })

    # 4. 投标人资质要求 (资格与资质)
    m_qual = re.search(r"3\.1\s*投标人资质要求[:：]\s*(.*?)(?=3\.2|\Z)", s3, re.DOTALL)
    if m_qual:
        clauses.append({
            "text": f"投标人资质要求：{clean_text(m_qual.group(1))}",
            "category": "qualifications",
            "source": "第一章"
        })

    # 5. 投标人业绩要求 (资格与资质)
    m_perf = re.search(r"3\.2\s*投标人业绩要求[:：]\s*(.*?)(?=3\.3|\Z)", s3, re.DOTALL)
    if m_perf:
        clauses.append({
            "text": f"投标人业绩要求：{clean_text(m_perf.group(1))}",
            "category": "qualifications",
            "source": "第一章"
        })

    # 6. 项目经理资格要求 (资格与资质)
    m_pm = re.search(r"3\.3\s*项目经理资格要求[:：]\s*(.*?)(?=3\.4|\Z)", s3, re.DOTALL)
    if m_pm:
        clauses.append({
            "text": f"项目经理资格要求：{clean_text(m_pm.group(1))}",
            "category": "qualifications",
            "source": "第一章"
        })

    # 7. 投标人信誉要求 (资格与资质)
    m_rep = re.search(r"3\.4\s*投标人须符合下列情形之一[:：]\s*(.*?)(?=3\.5|\Z)", s3, re.DOTALL)
    if m_rep:
        clauses.append({
            "text": f"投标人信誉要求：投标人须符合下列情形之一：{clean_text(m_rep.group(1))}",
            "category": "qualifications",
            "source": "第一章"
        })

    # 8. 联合体投标要求 (形式与提交)
    m_cons = re.search(r"3\.5\s*(本次招标.*?)(?=3\.6|\Z)", s3, re.DOTALL)
    if m_cons:
        clauses.append({
            "text": clean_text(m_cons.group(1)),
            "category": "formality",
            "source": "第一章"
        })

    # 9. 资格审查方式 (形式与提交)
    m_audit = re.search(r"3\.6\s*(本次招标实行资格后审.*?)(?=\Z)", s3, re.DOTALL)
    if m_audit:
        clauses.append({
            "text": clean_text(m_audit.group(1)),
            "category": "formality",
            "source": "第一章"
        })

    # 10. 投标文件递交截止时间 (形式与提交)
    m_sub = re.search(r"5\.\s*投标文件的递交\s*(.*?)(?=\Z)", s5, re.DOTALL)
    if m_sub:
        clauses.append({
            "text": clean_text(m_sub.group(1)),
            "category": "formality",
            "source": "第一章"
        })

    # 11. 投标保证金账户 (商务与报价)
    deposit_clean = clean_text(re.sub(r"^10\.\s*", "", s10))
    clauses.append({
        "text": f"投标保证金账户：{deposit_clean}",
        "category": "commercial",
        "source": "第一章"
    })

    return clauses


def main():
    proj_root = Path(r"data_extracted/2022年投标项目/7.11-兆河生态清洁小流域建设工程（巢湖市段）——水环境治理工程（槐林段）")
    tender_pdf = proj_root / "01_招标文件" / "招标文件正文.pdf"
    
    print("[*] 正在提取【第一章 招标公告】的核心实质性条款 (极简 text/category/source)...")
    clauses = extract_chapter1_core_clauses(tender_pdf)
    print(f"[OK] 成功提炼核心条款: {len(clauses)} 条。")

    out_file1 = Path("SystemCode/data/processed/clauses/7.11_ch1.json")
    out_file1.parent.mkdir(parents=True, exist_ok=True)
    out_file2 = proj_root / "01_招标文件" / "ch1.json"

    with open(out_file1, "w", encoding="utf-8") as f:
        json.dump(clauses, f, ensure_ascii=False, indent=2)
    print(f"[OK] 保存至系统文件: {out_file1}")

    with open(out_file2, "w", encoding="utf-8") as f:
        json.dump(clauses, f, ensure_ascii=False, indent=2)
    print(f"[OK] 保存至项目本地: {out_file2}")


if __name__ == "__main__":
    main()
