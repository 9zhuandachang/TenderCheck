"""
Structured Data Schemas for Stage 3: LLM Information Extraction & Semantic Alignment.

Defines the contract between `src/extraction` (Neural LLM Slot Extractor)
and `src/rules` (Deterministic Symbolic Rule Engine).
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


VALID_CONSTRAINT_TYPES = (
    "NUMERIC_BOUND",                      # 数值边界或精确相等约束 (报价<=控制价、工期<=360天、保证金>=50万、暂列金==800万等)
    "QUALIFICATION_LEVEL",                # 资质/职称/证书等级与有效性约束 (施工总承包三级及以上、一级建造师、中级职称及以上、安许证有效等)
    "ENTITY_CONSISTENCY",                 # 跨页/跨表一致性与大小写金额核对约束 (大小写一致、证书单位与投标人一致、企业名称一致等)
    "DOCUMENT_PRESENCE_AND_SEAL",         # 必备文件组成与签章完备性约束 (含/不含报价投标函、合同、中标通知书、竣工验收单及各方盖章等)
    "PROHIBITION_AND_COMMITMENT",         # 禁止性底线与实质性承诺约束 (不接受联合体、不允许分包、不得挂靠/弄虚作假/失信/行贿、不撤销投标等)
    "QUALITATIVE_OR_SCORING",             # 质量/安全定性目标与评标打分档位计算 (质量合格、无安全事故、资产负债率打分、业绩加分、施组评审等)
    "STAFFING_COUNT_AND_QUALIFICATION",   # 多人岗位配备数量及逐人资格条件约束 (道路/桥梁/合同/测量/试验工程师、安全员C证等人数与逐人资质)
    "PERFORMANCE_CHAIN_AND_SCORING",      # 历史业绩复合条件、人员变更证明链及多项目累加计分约束 (T0011/T0209/T0210/T0212/T0216/T0359)
    "FORMULA_BOUND",                      # 多字段算术公式与基数扣减核算约束 (T0059安全生产费1.5%净基数核算、履约保证金比例等)
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

    # --- Extended Rule Contract Fields (Step 2, backward-compatible defaults) ---
    # 1. Timeliness & Project Benchmark Context
    review_reference_date: str = ""        # Project bid/review benchmark date (e.g. "2022-11-11" for S604)
    min_date: str = ""                     # Lookback window start date (e.g. "2017-01-01" for performance)
    requires_validity_date: bool = False   # Require explicit valid_to / extension_valid_to date comparison
    allow_extension_notice: bool = True    # Allow official qualification extension notices (e.g. valid to 2022-12-31)

    # 2. Subject, Role & Multi-Person Staffing
    required_role: str = ""                # e.g. "project_manager", "chief_engineer", "road_engineer", "safety_officer"
    required_count: Optional[int] = None   # Required headcount for staffing rules (e.g. 1, 2)
    count_mode: str = "AT_LEAST_N_QUALIFIED"  # "AT_LEAST_N_QUALIFIED" | "ALL_PROPOSED_QUALIFIED"
    allowed_domains: List[str] = field(default_factory=list)
    requires_employer_match: bool = False
    required_social_security_months: Optional[int] = None

    # 3. Historical Performance Chain & Multi-Project Scoring
    min_length_km: Optional[float] = None
    length_strict_gt: bool = False         # True for "超过15公里" (T0210), False for "≥15公里"/"不少于15公里"
    min_amount_wanyuan: Optional[float] = None
    required_highway_grade: str = ""       # e.g. "三级公路"
    allowed_construction_types: List[str] = field(default_factory=list)  # e.g. ["新建", "改建", "扩建"]
    required_scopes: List[str] = field(default_factory=list)             # e.g. ["路基", "路面", "桥涵"]
    required_proof_docs: List[str] = field(default_factory=list)         # e.g. ["合同", "中标通知书", "交工验收证明"]
    required_seal_parties: List[str] = field(default_factory=list)       # e.g. ["建设单位", "设计单位", "监理单位", "施工单位"]
    min_project_count: Optional[int] = None
    scoring_mode: str = ""                 # "", "BRACKET", "BASE_QUAL_SCORE", "SINGLE_PROJECT_BONUS", "PER_PROJECT_ADDITIVE", "CREDIT_MULTI_YEAR"
    points_per_project: Optional[float] = None
    baseline_deduct_count: int = 0

    # 4. Formula-Based Net Base & Ratio Calculation
    formula_type: str = ""                 # "NET_BASE_RATIO" | "GROSS_RATIO"
    formula_rate: Optional[float] = None   # e.g. 0.015 (1.5%), 0.10 (10%)
    base_field: str = ""                   # e.g. "chapter_100_700_total"
    deduction_fields: List[str] = field(default_factory=list)

    # 5. External Fact Verification & Human Review Routing
    requires_external_verification: bool = False
    requires_human_review: bool = False

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

    # --- Extended Bid Fact Fields (Step 2, backward-compatible defaults) ---
    certificate_holder: str = ""
    subject_role: str = ""
    certificate_number: str = ""
    certificate_issuer: str = ""
    valid_from: str = ""                   # ISO date "YYYY-MM-DD"
    valid_to: str = ""                     # ISO date "YYYY-MM-DD"
    extension_valid_to: str = ""           # Official extension date "YYYY-MM-DD" (e.g. "2022-12-31")
    is_long_term: Optional[bool] = None
    employer_name: str = ""
    employer_matches_bidder: Optional[bool] = None
    evidence_source_type: str = ""         # "CERTIFICATE_SCAN" | "OFFICIAL_QUERY_SCREENSHOT" | "SELF_DECLARED_TABLE" | "COMMITMENT_LETTER"
    observed_seal_parties: List[str] = field(default_factory=list)
    signature_types: List[str] = field(default_factory=list)
    personnel_records: List[Dict[str, Any]] = field(default_factory=list)
    proposed_person_name: str = ""
    performance_records: List[Dict[str, Any]] = field(default_factory=list)
    component_amounts: Dict[str, float] = field(default_factory=dict)
    credit_ratings: List[Dict[str, Any]] = field(default_factory=list)

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
    boolean = {
        'evidence_found', 'document_present', 'certificate_valid', 'has_official_seal', 'has_signature',
        'prohibition_violated', 'commitment_fulfilled', 'qualitative_match',
        'requires_official_seal', 'requires_signature', 'conflict_detected',
        'requires_validity_date', 'allow_extension_notice', 'requires_employer_match',
        'length_strict_gt', 'requires_external_verification', 'requires_human_review',
        'is_long_term', 'employer_matches_bidder',
    }
    numeric = {
        'expected_numeric', 'secondary_numeric', 'scoring_max_points', 'actual_numeric',
        'secondary_actual_numeric', 'estimated_score', 'required_count',
        'required_social_security_months', 'min_length_km', 'min_amount_wanyuan',
        'min_project_count', 'points_per_project', 'baseline_deduct_count', 'formula_rate',
    }
    arrays = {
        'scoring_bands', 'page_mentions', 'evidence_pages', 'evidence_references',
        'subject_bindings', 'conflict_pairs', 'allowed_domains', 'allowed_construction_types',
        'required_scopes', 'required_proof_docs', 'required_seal_parties', 'deduction_fields',
        'observed_seal_parties', 'signature_types', 'personnel_records', 'performance_records',
        'credit_ratings',
    }
    dicts = {'source_quotes', 'component_amounts'}
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
        elif key in dicts:
            if not isinstance(value, dict):
                raise ValueError(f'{key} must be an object')
            if key == 'component_amounts':
                for k, v in value.items():
                    if type(v) not in (float, int) or not math.isfinite(v):
                        raise ValueError(f'component_amounts[{k}] must be a finite number')
        elif not isinstance(value, str):
            raise ValueError(f'{key} must be a string')
