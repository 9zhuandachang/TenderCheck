"""
Schema and label definitions for TenderCheck Requirement Classification.

Conforms strictly to `TenderCheck_Annotation_Guideline_v1_3.md` (Section 2):
8 standard requirement categories for Chinese engineering tender documents.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# Official 8 Requirement Types ordered by standard taxonomy
REQUIREMENT_TYPES: Tuple[str, ...] = (
    "Qualification",  # 资格与资质要求 (营业执照、资质等级、项目经理资格、业绩、信誉等)
    "Document",       # 投标文件组成与格式 (文件构成、签字盖章、份数、装订、密封、电子加密等)
    "Technical",      # 技术与施工方案要求 (施工组织设计、工期质量目标、安全文明、技术标准等)
    "Commercial",     # 商务与报价要求 (投标报价、最高投标限价、工程量清单、投标保证金、履约担保等)
    "Schedule",       # 时间与工期节点要求 (投标截止时间、开标时间、投标有效期、工期天数等)
    "Consistency",    # 一致性与唯一性要求 (正副本一致、大小写一致、禁止多方案报价、前后一致等)
    "Integrity",      # 廉洁与合规底线要求 (禁止串通投标、弄虚作假、挂靠、违法分包、行贿等)
    "Scoring",        # 评标与打分规则 (初审项、详细评审打分标准、加分项、扣分项、基准价计算等)
)

# Chinese display names for reporting and visualization
LABEL_CN_NAMES: Dict[str, str] = {
    "Qualification": "资格与资质要求",
    "Document": "文件组成与格式",
    "Technical": "技术与施工要求",
    "Commercial": "商务与报价要求",
    "Schedule": "时间与工期节点",
    "Consistency": "一致性与唯一性",
    "Integrity": "廉洁与合规底线",
    "Scoring": "评标与打分规则",
}

# Bidirectional mappings between string label and integer class ID
LABEL2ID: Dict[str, int] = {label: idx for idx, label in enumerate(REQUIREMENT_TYPES)}
ID2LABEL: Dict[int, str] = {idx: label for idx, label in enumerate(REQUIREMENT_TYPES)}
NUM_LABELS: int = len(REQUIREMENT_TYPES)


def format_model_input(
    requirement_text: str,
    section_text: Optional[str] = None,
    use_section_context: bool = True,
) -> str:
    """
    Construct the unified input string fed into the requirement classifier.

    By default, concatenates the section title and the requirement text:
        "[Section] 第三章 评标办法 [SEP] 施工组织设计满分15分。"
    If `use_section_context=False` or `section_text` is empty, falls back to:
        "施工组织设计满分15分。"

    Args:
        requirement_text: The atomic requirement clause text (`Requirement`).
        section_text: The chapter/section header (`Section`), if available.
        use_section_context: Whether to prepend the section header.

    Returns:
        Formatted text string ready for tokenization or vectorization.
    """
    req_clean = (requirement_text or "").strip()
    sec_clean = (section_text or "").strip()

    if use_section_context and sec_clean:
        return f"[Section] {sec_clean} [SEP] {req_clean}"
    return req_clean


@dataclass(frozen=True)
class RequirementSample:
    """
    Immutable representation of a single annotated tender requirement item.

    Attributes:
        sample_id: Unique identifier across dataset (e.g., "2025/P01::T0001").
        item_id: Original ID within the JSON file (e.g., "T0001").
        project_id: Source tender file identifier used for group-based splitting.
        folder: Subfolder name (e.g., "2022", "2025", "2026-03", "2026_01").
        page: 1-indexed page number in the source PDF.
        section: Section/chapter header string.
        requirement: Atomic requirement clause text.
        label: One of the 8 official `REQUIREMENT_TYPES`.
        label_id: Integer class index in `[0, NUM_LABELS - 1]`.
    """

    sample_id: str
    item_id: str
    project_id: str
    folder: str
    page: int
    section: str
    requirement: str
    label: str
    label_id: int

    def to_model_input(self, use_section_context: bool = True) -> str:
        """Format this sample into the classifier's input text representation."""
        return format_model_input(
            requirement_text=self.requirement,
            section_text=self.section,
            use_section_context=use_section_context,
        )

    def to_dict(self) -> Dict[str, object]:
        """Serialize sample to a plain dictionary (for JSONL export)."""
        return {
            "sample_id": self.sample_id,
            "ID": self.item_id,
            "project_id": self.project_id,
            "folder": self.folder,
            "Page": self.page,
            "Section": self.section,
            "Requirement": self.requirement,
            "Requirement Type": self.label,
            "label_id": self.label_id,
        }
