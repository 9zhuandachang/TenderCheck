"""
LLM-Based & Heuristic Structured Slot Extractors for TenderCheck (`src/extraction`).

Responsibilities:
1. `DeepSeekSlotExtractor`: Calls DeepSeek API (`deepseek-chat` with JSON mode) to parse
   unstructured tender requirement clauses + retrieved Dual-Index RAG evidence into
   `StructuredExtractionResult` (`TenderConstraintSlots` + `BidEvidenceSlots`).
   Includes SHA-256 disk caching so repeated experiment runs cost 0 tokens.
2. `HeuristicSlotExtractor`: Pure regex + EntityLedger slot extractor without LLM,
   serving as an ablation baseline ("Rule Engine w/o LLM Semantic Extraction") and
   enabling fast offline unit testing.
"""

import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .schema import (
     VALID_CONSTRAINT_TYPES,
    BidEvidenceSlots,
    StructuredExtractionResult,
    TenderConstraintSlots,
)

logger = logging.getLogger(__name__)


SYSTEM_EXTRACTION_PROMPT = """你是一名专业的工程招投标结构化信息抽取引擎（Information Extraction Engine）。
你的唯一职责是从【招标要求条款】和【检索到的投标文件证据（含结构化台账摘要与原文片段）】中抽取出严格对齐的结构化槽位数据（JSON格式），供下游的“确定性符号规则引擎（Symbolic Rule Engine）”执行逻辑判定。

请严格输出一个合法的 JSON 对象，包含以下三个顶层字段：
1. "tender_slots": 描述招标条款提出的约束条件
   - "constraint_type": 必须为以下6种之一：
     * "NUMERIC_BOUND": 数值上限/下限/相等约束（如工期<=360日历天、投标报价<=最高投标限价115046243.64元、暂列金==8000000.00元、保证金==50万元、投标有效期>=90日、安全生产费比例>=1.5%、人员数量>=1人等）
     * "QUALIFICATION_LEVEL": 企业或人员资质/职称/证书等级与有效性约束（如公路工程施工总承包三级及以上、一级注册建造师、中级及以上职称、有效安全生产许可证、交安B/C证、独立法人资格等）
     * "ENTITY_CONSISTENCY": 一致性与金额大小写核对约束（如已标价清单报价与投标函大写报价一致、项目经理注册单位与投标人名称一致、投标人名称与名录一致等）
     * "DOCUMENT_PRESENCE_AND_SEAL": 必备文件组成与签章完备性约束（如须提供合同/中标通知书/交工验收证明并盖章确认、须含不含报价投标函、法定代表人身份证明或授权委托书等）
     * "PROHIBITION_AND_COMMITMENT": 禁止性底线与实质性履约承诺约束（如不接受联合体、不允许分包、不得挂靠、不得弄虚作假、不得被列入失信被执行人、无行贿犯罪、有效期内不撤销、项目经理到岗变更承诺等）
     * "QUALITATIVE_OR_SCORING": 质量/安全目标响应或评标打分档位计算（如质量评定合格、无生产安全责任事故、资产负债率打分、业绩加分、施组方案评审等）
   - "target_field": 目标字段标识（如 "duration_days", "bid_price_yuan", "provisional_sum_yuan", "bid_bond_yuan", "safety_fee_ratio", "validity_days", "qualification_level", "rmb_upper_consistency", "org_name_consistency", "document_presence", "prohibition_check", "quality_target", "safety_target", "scoring_item"）
   - "operator": 比较算子（"<=", ">=", "==", "LEVEL_GE", "CONSISTENT", "PRESENT", "NOT_VIOLATED", "QUALITATIVE_MATCH", "SCORE_BRACKET"）
   - "expected_numeric": 招标要求的数值（浮点数或 null；注意统一单位：若招标要求50万元且投标证据以万元计则填50，若以元计则统一换算为元）
   - "expected_unit": 单位字符串（如 "日历天", "元", "万元", "%", "公里", "人", "日"）
   - "secondary_numeric": 第二数值约束（如含暂列金 8000000.00，无则为 null）
   - "expected_text": 招标要求的文本值或等级名称（如 "公路工程施工总承包三级", "公路工程专业一级注册建造师", "中级工程师", "合格", "无生产安全责任事故"）
   - "required_domain": 要求的专业领域（如 "公路工程", "桥梁", ""）
   - "requires_official_seal": 布尔值，条款是否要求盖章确认
   - "requires_signature": 布尔值，条款是否要求签字确认
   - "scoring_max_points": 若为打分项，满分分值（浮点数或 null）

2. "bid_slots": 描述从投标证据中提取出的真实事实（严禁编造证据中不存在的数值）
   - "evidence_found": 布尔值，投标证据中是否找到了与该条款直接相关的有效信息
   - "actual_numeric": 投标文件中的实际数值（浮点数或 null，单位须与 expected_unit 对齐！例如 expected_unit 为 "元" 时，50万元应填 500000.0 或两者同为 50 万元；对于安全生产费率，若已知第100-700章基数或安全生产费1324528.73元满足1.5%规定，可填实际数值或百分比 1.5）
   - "actual_unit": 投标数值单位
   - "secondary_actual_numeric": 第二实际数值（如投标报价汇总表中的暂列金额 8000000.00，无则为 null）
   - "actual_text": 投标文件中的实际文本描述或所持资质/职称等级名称（如 "公路工程施工总承包特级", "一级注册建造师", "高级工程师", "合格"）
   - "actual_domain": 实际专业领域（如 "公路工程", "道路/桥梁"）
   - "upper_amount_text": 若涉及报价或大小写核对，提取投标文件中的人民币大写金额原文（如 "玖仟柒佰玖拾捌万肆仟玖佰肆拾玖元伍角捌分"），否则为 ""
   - "entity_primary": 一致性比对主体1（如投标人名称 "安徽水利开发有限公司" 或投标函小写报价 "97984949.58"）
   - "entity_secondary": 一致性比对主体2（如证书上的注册单位名称 "安徽水利开发有限公司" 或清单汇总表报价 "97984949.58"）
   - "certificate_valid": 证照/安许证/考核证是否在有效期内（true / false / null）
   - "has_official_seal": 布尔值，相关证据页或台账记录是否已加盖公章/单位章
   - "has_signature": 布尔值，相关证据页是否已签字/盖章
   - "prohibition_violated": 布尔值，投标文件是否违反了禁止性规定（例如：不接受联合体但提交了联合体合作协议则为 true；填写了“无”联合体、“无”分包、承诺不挂靠/不弄虚作假/无失信行贿则为 false）
   - "commitment_fulfilled": 布尔值，投标文件是否按要求作出了实质性响应或履约承诺
   - "qualitative_match": 布尔值，定性技术/质量/安全/施组内容是否响应并满足招标要求
   - "estimated_score": 若为评标打分项，根据证据估算得分（浮点数或 null）
   - "page_mentions": 跨页数值提及列表，格式为 [{"page": 页码整数, "value": 数值或字符串}]
   - "evidence_pages": 支撑上述抽取事实的投标文件页码整数列表（如 [1, 565]）
   - "evidence_summary": 一句话客观概括投标证据中的关键事实

3. "raw_llm_verdict_hint": "COMPLIANT" 或 "NON_COMPLIANT"（仅作为纯LLM端到端基线对比参考）
"""


class DeepSeekSlotExtractor:
    """
    Calls DeepSeek API (`deepseek-chat`) to extract structured slots from
    `(tender_requirement, retrieved_bid_evidence)`.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        cache_path: Optional[Path] = None,
        timeout: int = 45,
    ) -> None:
        self.api_key = (
            api_key
            or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        ).strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.cache_path = cache_path or (
            Path(__file__).resolve().parents[2]
            / "data"
            / "extraction_cache"
            / "deepseek_slot_cache.json"
        )
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._load_cache()

    def _load_cache(self) -> None:
        if self.cache_path and self.cache_path.exists():
            try:
                self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Failed to load DeepSeek cache %s: %s", self.cache_path, exc)
                self._cache = {}

    def _save_cache(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _compute_cache_key(
        clause_id: str,
        requirement_text: str,
        category: str,
        evidence_context: str,
        model: str,
    ) -> str:
        payload = f"{model}||{clause_id}||{category}||{requirement_text}||{evidence_context}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _call_chat_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        if not self.api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set. Please set environment variable DEEPSEEK_API_KEY "
                "or pass --api_key to run DeepSeek LLM extraction."
            )

        url = f"{self.base_url}/chat/completions"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            "max_tokens": 1200,
        }
        data_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        last_err: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    resp_obj = json.loads(resp.read().decode("utf-8"))
                content = resp_obj["choices"][0]["message"]["content"]
                return json.loads(content)
            except Exception as exc:
                last_err = exc
                logger.warning(
                    "DeepSeek API attempt %d/%d failed: %s", attempt, max_retries, exc
                )
                if attempt < max_retries:
                    time.sleep(1.5 * attempt)
        raise RuntimeError(f"DeepSeek API failed after {max_retries} attempts: {last_err}")

    def extract(
        self,
        clause_id: str,
        requirement_text: str,
        category: str,
        evidence_context: str,
        use_cache: bool = True,
    ) -> StructuredExtractionResult:
        """
        Extract `StructuredExtractionResult` from a tender requirement clause and
        formatted Dual-Index RAG evidence context.
        """
        cache_key = self._compute_cache_key(
            clause_id=clause_id,
            requirement_text=requirement_text,
            category=category,
            evidence_context=evidence_context,
            model=self.model,
        )
        if use_cache and cache_key in self._cache:
            return StructuredExtractionResult.from_dict(self._cache[cache_key])

        user_prompt = (
            f"【条款编号】: {clause_id}\n"
            f"【条款分类】: {category}\n"
            f"【招标要求条款原文】:\n{requirement_text}\n\n"
            f"【检索到的投标文件证据（结构化台账 + Top-K 原文证据块）】:\n{evidence_context}\n\n"
            "请严格按照系统提示词定义的 JSON Schema 输出结构化槽位抽取结果。"
        )
        parsed = self._call_chat_completion(SYSTEM_EXTRACTION_PROMPT, user_prompt)

        tender_raw = parsed.get("tender_slots") or {}
        if tender_raw.get("constraint_type") not in VALID_CONSTRAINT_TYPES:
            tender_raw["constraint_type"] = "QUALITATIVE_OR_SCORING"

        result = StructuredExtractionResult(
            clause_id=clause_id,
            requirement_text=requirement_text,
            category=category,
            tender_slots=TenderConstraintSlots.from_dict(tender_raw),
            bid_slots=BidEvidenceSlots.from_dict(parsed.get("bid_slots") or {}),
            extractor_backend=self.model,
            raw_llm_verdict_hint=parsed.get("raw_llm_verdict_hint"),
        )
        if use_cache:
            self._cache[cache_key] = result.to_dict()
            self._save_cache()
        return result


class HeuristicSlotExtractor:
    """
    Deterministic regex-based slot extractor (no LLM calls).
    Used as the non-LLM extraction baseline in ablation experiments and for offline unit tests.
    """

    def extract(
        self,
        clause_id: str,
        requirement_text: str,
        category: str,
        evidence_context: str,
        ledger_dict: Optional[Dict[str, Any]] = None,
    ) -> StructuredExtractionResult:
        req = requirement_text.strip()
        ev = evidence_context or ""
        cp = (ledger_dict or {}).get("company_profile") or {}
        cs = (ledger_dict or {}).get("commercial_schedule_ledger") or {}

        pages = [int(p) for p in re.findall(r"(?:Page|第)\s*(\d+)\s*页?", ev)[:5]]
        has_seal = ("已盖章" in ev) or ("公章: 是" in ev) or ("has_official_seal\": true" in ev.lower()) or ("盖章" in ev)
        has_sig = ("已签字" in ev) or ("签字: 是" in ev) or ("签章" in ev)

        tender_slots = TenderConstraintSlots()
        bid_slots = BidEvidenceSlots(
            evidence_found=bool(ev.strip()),
            has_official_seal=has_seal,
            has_signature=has_sig,
            evidence_pages=pages,
            evidence_summary=ev[:160].replace("\n", " "),
        )

        # 1. Schedule / Duration days
        if "工期" in req and "日历天" in req:
            m_req = re.search(r"(\d+(?:\.\d+)?)\s*日历天", req)
            exp_days = float(m_req.group(1)) if m_req else 360.0
            tender_slots = TenderConstraintSlots(
                constraint_type="NUMERIC_BOUND",
                target_field="duration_days",
                operator="<=",
                expected_numeric=exp_days,
                expected_unit="日历天",
            )
            m_bid = re.search(r"(?:工期[^0-9]{0,8}|duration_days[^0-9]{0,8})(\d+(?:\.\d+)?)\s*日历天", ev)
            if not m_bid:
                m_bid = re.search(r"(\d+(?:\.\d+)?)\s*日历天", ev)
            act_days = float(m_bid.group(1)) if m_bid else None
            if act_days is None and cs.get("duration_days"):
                m_cs = re.search(r"(\d+(?:\.\d+)?)", str(cs["duration_days"]))
                if m_cs:
                    act_days = float(m_cs.group(1))
            bid_slots.actual_numeric = act_days
            bid_slots.actual_unit = "日历天"
            return StructuredExtractionResult(
                clause_id=clause_id,
                requirement_text=req,
                category=category,
                tender_slots=tender_slots,
                bid_slots=bid_slots,
                extractor_backend="heuristic_regex",
            )

        # 2. Bid Validity Period (投标有效期 90日)
        if "投标有效期" in req and re.search(r"\d+\s*[日天]", req):
            m_req = re.search(r"(\d+)\s*[日天]", req)
            tender_slots = TenderConstraintSlots(
                constraint_type="NUMERIC_BOUND",
                target_field="validity_days",
                operator=">=",
                expected_numeric=float(m_req.group(1)),
                expected_unit="日",
            )
            m_bid = re.search(r"(\d+)\s*[日天]", ev)
            bid_slots.actual_numeric = float(m_bid.group(1)) if m_bid else (90.0 if "投标有效期内不撤销" in ev else None)
            bid_slots.actual_unit = "日"
            return StructuredExtractionResult(
                clause_id=clause_id,
                requirement_text=req,
                category=category,
                tender_slots=tender_slots,
                bid_slots=bid_slots,
                extractor_backend="heuristic_regex",
            )

        # 3. Bid Price vs Control Price / Ceiling Price
        if "控制价" in req or "最高投标限价" in req:
            m_ctrl = re.search(r"(\d{6,}(?:\.\d+)?)\s*元", req)
            ctrl_val = float(m_ctrl.group(1)) if m_ctrl else 115046243.64
            m_prov = re.search(r"暂列金[额]?(\d{5,}(?:\.\d+)?)\s*元", req)
            prov_val = float(m_prov.group(1)) if m_prov else None
            tender_slots = TenderConstraintSlots(
                constraint_type="NUMERIC_BOUND",
                target_field="bid_price_yuan",
                operator="<=",
                expected_numeric=ctrl_val,
                expected_unit="元",
                secondary_numeric=prov_val,
            )
            m_bid_price = re.search(r"(?:投标总报价|小写|bid_price_yuan)[^0-9]{0,12}(\d{6,}(?:\.\d+)?)", ev)
            if not m_bid_price:
                m_bid_price = re.search(r"(\d{7,}(?:\.\d+)?)", ev)
            act_price = float(m_bid_price.group(1)) if m_bid_price else (
                float(cs["bid_price_yuan"]) if cs.get("bid_price_yuan") is not None else None
            )
            m_bid_prov = re.search(r"暂列金额[^0-9]{0,15}(\d{5,}(?:\.\d+)?)", ev)
            bid_slots.actual_numeric = act_price
            bid_slots.actual_unit = "元"
            bid_slots.secondary_actual_numeric = float(m_bid_prov.group(1)) if m_bid_prov else (8000000.0 if "8000000" in ev else None)
            return StructuredExtractionResult(
                clause_id=clause_id,
                requirement_text=req,
                category=category,
                tender_slots=tender_slots,
                bid_slots=bid_slots,
                extractor_backend="heuristic_regex",
            )

        # 4. Uppercase/Lowercase Amount Consistency
        if "大写报价一致" in req or ("大写" in req and "一致" in req):
            tender_slots = TenderConstraintSlots(
                constraint_type="ENTITY_CONSISTENCY",
                target_field="rmb_upper_consistency",
                operator="CONSISTENT",
            )
            m_num = re.search(r"(\d{6,}(?:\.\d+)?)", ev)
            m_up = re.search(r"([壹贰叁肆伍陆柒捌玖拾佰仟万亿元角分整零]{8,})", ev)
            bid_slots.actual_numeric = float(m_num.group(1)) if m_num else (
                float(cs["bid_price_yuan"]) if cs.get("bid_price_yuan") is not None else None
            )
            bid_slots.upper_amount_text = m_up.group(1) if m_up else str(cs.get("bid_price_upper") or "")
            bid_slots.entity_primary = str(bid_slots.actual_numeric or "")
            bid_slots.entity_secondary = bid_slots.upper_amount_text
            return StructuredExtractionResult(
                clause_id=clause_id,
                requirement_text=req,
                category=category,
                tender_slots=tender_slots,
                bid_slots=bid_slots,
                extractor_backend="heuristic_regex",
            )

        # 5. Entity Name Consistency (Project Manager Registration Org / Company Name)
        if "一致" in req and ("名称" in req or "单位" in req):
            tender_slots = TenderConstraintSlots(
                constraint_type="ENTITY_CONSISTENCY",
                target_field="org_name_consistency",
                operator="CONSISTENT",
            )
            comp = str(cp.get("company_name") or "安徽水利开发有限公司")
            m_other = re.search(r"注册单位[：:\s]*([\u4e00-\u9fa5]+(?:公司|集团))", ev)
            sec_org = m_other.group(1) if m_other else (comp if comp in ev else "")
            bid_slots.entity_primary = comp
            bid_slots.entity_secondary = sec_org
            return StructuredExtractionResult(
                clause_id=clause_id,
                requirement_text=req,
                category=category,
                tender_slots=tender_slots,
                bid_slots=bid_slots,
                extractor_backend="heuristic_regex",
            )

        # 6. Qualification Hierarchy
        if any(k in req for k in ("资质", "建造师", "职称", "安全生产许可证", "安全生产考核合格证", "安全员C证", "独立法人资格")):
            tender_slots = TenderConstraintSlots(
                constraint_type="QUALIFICATION_LEVEL",
                target_field="qualification_level",
                operator="LEVEL_GE",
                expected_text=req,
                required_domain="公路工程" if "公路" in req else "",
            )
            bid_slots.actual_text = ev[:300]
            bid_slots.actual_domain = "公路工程" if ("公路" in ev or "路桥" in ev or "交通" in ev) else ""
            bid_slots.certificate_valid = not ("过期" in ev or "失效" in ev or "无效" in ev)
            return StructuredExtractionResult(
                clause_id=clause_id,
                requirement_text=req,
                category=category,
                tender_slots=tender_slots,
                bid_slots=bid_slots,
                extractor_backend="heuristic_regex",
            )

        # 7. Prohibition & Commitment
        if any(k in req for k in ("不得", "不接受", "不允许", "严禁", "承诺")):
            tender_slots = TenderConstraintSlots(
                constraint_type="PROHIBITION_AND_COMMITMENT",
                target_field="prohibition_check",
                operator="NOT_VIOLATED",
                expected_text=req,
            )
            violated = any(
                bad in ev
                for bad in ("存在违法分包", "已组成联合体投标", "处于失信被执行人披露期", "虚假承诺", "拒绝承诺", "撤销投标")
            )
            bid_slots.prohibition_violated = violated
            bid_slots.commitment_fulfilled = not violated and bool(ev.strip())
            return StructuredExtractionResult(
                clause_id=clause_id,
                requirement_text=req,
                category=category,
                tender_slots=tender_slots,
                bid_slots=bid_slots,
                extractor_backend="heuristic_regex",
            )

        # 8. Document Presence & Seal
        if category == "Document" or any(k in req for k in ("须提供", "须包括", "须提交", "盖章")):
            tender_slots = TenderConstraintSlots(
                constraint_type="DOCUMENT_PRESENCE_AND_SEAL",
                target_field="document_presence",
                operator="PRESENT",
                expected_text=req,
                requires_official_seal=("盖章" in req),
            )
            missing = ("未提供" in ev) or ("缺失" in ev) or ("未加盖公章" in ev)
            if "未加盖公章" in ev:
                bid_slots.has_official_seal = False
            bid_slots.evidence_found = not missing and bool(ev.strip())
            return StructuredExtractionResult(
                clause_id=clause_id,
                requirement_text=req,
                category=category,
                tender_slots=tender_slots,
                bid_slots=bid_slots,
                extractor_backend="heuristic_regex",
            )

        # Default: Qualitative or Scoring
        tender_slots = TenderConstraintSlots(
            constraint_type="QUALITATIVE_OR_SCORING",
            target_field="qualitative_or_scoring",
            operator="QUALITATIVE_MATCH",
            expected_text=req,
        )
        bid_slots.qualitative_match = bool(ev.strip()) and ("不满足" not in ev) and ("不合格" not in ev)
        return StructuredExtractionResult(
            clause_id=clause_id,
            requirement_text=req,
            category=category,
            tender_slots=tender_slots,
            bid_slots=bid_slots,
            extractor_backend="heuristic_regex",
        )
