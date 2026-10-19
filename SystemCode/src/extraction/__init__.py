"""
extraction module - 文档结构化解析与槽位/实体抽取模块
负责从招标文件和投标文件中精准提取工期数值、控制价金额、企业资质、人员证件等结构化事实。
"""

from .llm_extractor import DeepSeekSlotExtractor, HeuristicSlotExtractor
from .schema import (
    VALID_CONSTRAINT_TYPES,
    BidEvidenceSlots,
    StructuredExtractionResult,
    TenderConstraintSlots,
)

__all__ = [
    "VALID_CONSTRAINT_TYPES",
    "TenderConstraintSlots",
    "BidEvidenceSlots",
    "StructuredExtractionResult",
    "DeepSeekSlotExtractor",
    "HeuristicSlotExtractor",
]
