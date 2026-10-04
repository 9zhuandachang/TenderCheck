import json
from pathlib import Path
from typing import Dict, List, Any

# Standard 5+1 Real-World Engineering Tender Taxonomy
TENDER_CATEGORIES = {
    0: {
        "id": "qualifications",
        "name_zh": "资格与资质",
        "description": "企业资质等级、安全生产许可证、项目经理/建造师/技术负责人/安全B证、类似工程业绩、近年财务审计报告、信用中国失信被执行人及黑名单记录。",
        "keywords": ["资质", "营业执照", "安全生产许可证", "建造师", "项目经理", "技术负责人", "类似业绩", "财务审计", "失信", "信用", "净资产", "社保", "职称"]
    },
    1: {
        "id": "commercial",
        "name_zh": "商务与报价",
        "description": "招标控制价/最高投标限价、投标总报价、不可竞争费率、工程款进度款支付方式、预付款比例、履约保证金与保函、质量保证金等。",
        "keywords": ["控制价", "最高限价", "投标报价", "投标总价", "工程款", "进度款", "预付款", "质保金", "履约保证金", "履约保函", "投标保证金", "暂列金额", "调价", "结算"]
    },
    2: {
        "id": "schedule",
        "name_zh": "进度与工期",
        "description": "计划总工期日历天数、计划开工/竣工日期、节点工程进度里程碑、工期延误违约责任与罚金。",
        "keywords": ["工期", "日历天", "开工日期", "竣工日期", "节点", "里程碑", "逾期", "工期延误", "进度计划", "拖期", "违约金/天"]
    },
    3: {
        "id": "formality",
        "name_zh": "形式与提交",
        "description": "投标文件递交截止时间与地点、法定代表人身份证明、授权委托书、签字盖章要求、联合体投标要求、中小企业声明函、投标有效期、密封份数等。",
        "keywords": ["截止时间", "递交地点", "法定代表人", "授权委托书", "签字", "盖章", "联合体", "投标有效期", "中小企业声明", "密封", "电子章", "废标条款"]
    },
    4: {
        "id": "technical",
        "name_zh": "技术与方案",
        "description": "工程承包招标范围、施工规范与技术标准、工程质量合格目标、施工组织设计要求、主要施工机械设备配备、安全文明施工与扬尘环保措施。",
        "keywords": ["招标范围", "承包范围", "技术标准", "施工规范", "质量目标", "合格标准", "施工组织设计", "施工方案", "机械设备", "扬尘", "环保", "安全文明施工"]
    },
    5: {
        "id": "general",
        "name_zh": "其他与通用",
        "description": "合同条款响应与偏离表、争议解决方式（诉讼或仲裁）、知识产权、保密义务、优先受偿权及其他通用商务合同条款。",
        "keywords": ["偏离表", "争议解决", "仲裁", "诉讼", "管辖", "优先受偿权", "保密", "不可抗力", "知识产权", "通用条款"]
    }
}

LABEL2ID = {v["id"]: k for k, v in TENDER_CATEGORIES.items()}
ID2LABEL = {k: v["id"] for k, v in TENDER_CATEGORIES.items()}
LABEL_NAMES_ZH = [TENDER_CATEGORIES[i]["name_zh"] for i in range(len(TENDER_CATEGORIES))]

def save_taxonomy(output_file: Path):
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(TENDER_CATEGORIES, f, indent=2, ensure_ascii=False)
    print(f"[OK] Taxonomy saved to {output_file}")

if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent.parent / "data" / "processed" / "chinese_taxonomy.json"
    save_taxonomy(out)
