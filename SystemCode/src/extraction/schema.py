"""
Structured Data Schemas for Stage 3: LLM Information Extraction & Semantic Alignment.

Defines the contract between `src/extraction` (Neural LLM Slot Extractor)
and `src/rules` (Deterministic Symbolic Rule Engine).
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


VALID_CONSTRAINT_TYPES = (
    "NUMERIC_BOUND",              # 数值边界或精确相等约束 (报价<=控制价、工期<=360天、保证金>=50万、暂列金==800万等)
    "QUALIFICATION_LEVEL",        # 资质/职称/证书等级与有效性约束 (施工总承包三级及以上、一级建造师、中级职称及以上、安许证有效等)
    "ENTITY_CONSISTENCY",         # 跨页/跨表一致性与大小写金额核对约束 (大小写一致、证书单位与投标人一致、企业名称一致等)
    "DOCUMENT_PRESENCE_AND_SEAL", # 必备文件组成与签章完备性约束 (含/不含报价投标函、合同、中标通知书、竣工验收单及各方盖章等)
    "PROHIBITION_AND_COMMITMENT", # 禁止性底线与实质性承诺约束 (不接受联合体、不允许分包、不得挂靠/弄虚作假/失信/行贿、不撤销投标等)
    "QUALITATIVE_OR_SCORING",     # 质量/安全定性目标与评标打分档位计算 (质量合格、无安全事故、资产负债率打分、业绩加分、施组评审等)
)


@dataclass
class TenderConstraintSlots:
    """Structured constraints parsed from a single tender requirement clause."""

    constraint_type: str = "QUALITATIVE_OR_SCORING"
    target_field: str = ""                 # e.g. "duration_days", "bid_price_yuan", "provisional_sum_yuan", "company_qualification"
    operator: str = "=="                   # "<=", "<", ">=", ">", "==", "LEVEL_GE", "NOT_VIOLATED", "PRESENT", "CONSISTENT", "SCORE_BRACKET"
    expected_numeric: Optional[float] = None
    expected_unit: str = ""                # e.g. "日历天", "元", "万元", "%", "公里", "人"
    secondary_numeric: Optional[float] = None  # e.g. provisional sum 8000000 or secondary threshold
    expected_text: str = ""                # e.g. "公路工程施工总承包三级", "合格", "无生产安全责任事故"
    required_domain: str = ""              # e.g. "公路工程", "桥梁"
    requires_official_seal: bool = False
    requires_signature: bool = False
    scoring_max_points: Optional[float] = None
    scoring_bands: List[Dict[str, Any]] = field(default_factory=list)
    source_quotes: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TenderConstraintSlots":
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in (data or {}).items() if k in valid_keys}
        validate_slot_types(filtered)
        return cls(**filtered)


@dataclass
class BidEvidenceSlots:
    """Facts extracted from cited bid passages; unknown facts remain null."""

    evidence_found: bool = False
    document_present: Optional[bool] = None
    actual_numeric: Optional[float] = None
    actual_unit: str = ""
    secondary_actual_numeric: Optional[float] = None
    actual_text: str = ""
    actual_domain: str = ""
    upper_amount_text: str = ""            # 人民币大写金额 (用于与小写金额进行符号级精确转换比对)
    entity_primary: str = ""               # 主体1 (如投标人名称 / 投标函报价)
    entity_secondary: str = ""             # 主体2 (如建造师注册单位 / 清单汇总表报价)
    certificate_valid: Optional[bool] = None
    has_official_seal: Optional[bool] = None
    has_signature: Optional[bool] = None
    prohibition_violated: Optional[bool] = None
    commitment_fulfilled: Optional[bool] = None
    qualitative_match: Optional[bool] = None
    estimated_score: Optional[float] = None
    page_mentions: List[Dict[str, Any]] = field(default_factory=list)
    evidence_pages: List[int] = field(default_factory=list)
    evidence_summary: str = ""
    conflict_detected: bool = False
    evidence_references: List[Dict[str, Any]] = field(default_factory=list)
    subject_bindings: List[Dict[str, Any]] = field(default_factory=list)
    conflict_pairs: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BidEvidenceSlots":
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in (data or {}).items() if k in valid_keys}
        validate_slot_types(filtered)
        return cls(**filtered)


@dataclass
class StructuredExtractionResult:
    """Complete output of `src/extraction` for a single `(tender_clause, bid_evidence)` pair."""

    clause_id: str
    requirement_text: str
    category: str
    tender_slots: TenderConstraintSlots
    bid_slots: BidEvidenceSlots
    extractor_backend: str = "deepseek-chat"
    raw_llm_verdict_hint: Optional[str] = None  # Used only when comparing against direct-LLM baseline
    extraction_issues: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clause_id": self.clause_id,
            "requirement_text": self.requirement_text,
            "category": self.category,
            "tender_slots": self.tender_slots.to_dict(),
            "bid_slots": self.bid_slots.to_dict(),
            "extractor_backend": self.extractor_backend,
            "raw_llm_verdict_hint": self.raw_llm_verdict_hint,
            "extraction_issues": self.extraction_issues,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StructuredExtractionResult":
        return cls(
            clause_id=str(data.get("clause_id", "")),
            requirement_text=str(data.get("requirement_text", "")),
            category=str(data.get("category", "")),
            tender_slots=TenderConstraintSlots.from_dict(data.get("tender_slots") or {}),
            bid_slots=BidEvidenceSlots.from_dict(data.get("bid_slots") or {}),
            extractor_backend=str(data.get("extractor_backend", "deepseek-chat")),
            raw_llm_verdict_hint=data.get("raw_llm_verdict_hint"),
            extraction_issues=list(data.get("extraction_issues") or []),
        )


def validate_slot_types(values):
    """Reject strings posing as booleans and NaN rather than silently coercing."""
    import math
    boolean = {'evidence_found', 'document_present', 'certificate_valid', 'has_official_seal', 'has_signature',
               'prohibition_violated', 'commitment_fulfilled', 'qualitative_match',
               'requires_official_seal', 'requires_signature', 'conflict_detected'}
    numeric = {'expected_numeric', 'secondary_numeric', 'scoring_max_points', 'actual_numeric',
               'secondary_actual_numeric', 'estimated_score'}
    arrays = {'scoring_bands', 'page_mentions', 'evidence_pages', 'evidence_references', 'subject_bindings', 'conflict_pairs'}
    for key, value in values.items():
        if key in boolean:
            if value is not None and type(value) is not bool:
                raise ValueError(f'{key} must be a boolean or null')
        elif key in numeric:
            if value is not None and (type(value) not in (float, int) or not math.isfinite(value)):
                raise ValueError(f'{key} must be a finite number or null')
        elif key in arrays:
            if not isinstance(value, list):
                raise ValueError(f'{key} must be a list')
        elif key == 'source_quotes':
            if not isinstance(value, dict):
                raise ValueError('source_quotes must be an object')
        elif not isinstance(value, str):
            raise ValueError(f'{key} must be a string')
