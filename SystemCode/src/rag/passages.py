"""Passage retrieval for downstream extraction: no entity ledger or business verdicts."""
from .indexer import HybridBidIndex


def source_registry(source_map, parents):
    """Resolve stable PDF identities; multiple virtual document IDs may share a PDF."""
    import hashlib
    from pathlib import Path
    from .parser import fitz
    sources, cache = {}, {}
    for doc_id in sorted({p.document_id for p in parents.values()}):
        path = Path(source_map[doc_id]).resolve()
        if path not in cache:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(8*1024*1024), b""):
                    digest.update(block)
            with fitz.open(path) as pdf:
                cache[path] = dict(path=str(path), sha256=digest.hexdigest(), page_count=len(pdf))
        sources[doc_id] = cache[path]
    return sources


def category_search_policy(requirement, category):
    """Existing category policy: query expansion, document filters and fusion weights.

    This policy bundles these three choices; its ablation is not a filter-only test.
    """
    query, filter_fn, dense, sparse = requirement, None, 0.5, 0.5
    if category == "Qualification":
        categories = {"营业执照与企业资质证书", "安全生产许可证", "项目管理机构与人员简历证书",
                      "社保证明材料", "近年类似工程业绩证明", "财务状况与审计报告"}
        filter_fn = lambda c: c.doc_category in categories or c.volume_type != "技术标"
    elif category == "Document":
        if not any(k in requirement for k in ("施工组织设计", "暗标", "技术文件")):
            filter_fn = lambda c: c.volume_type != "技术标"
        dense, sparse = 0.35, 0.65
    elif category == "Technical":
        include_letter = any(k in requirement for k in ("质量评定", "安全目标", "分包", "实质性要求"))
        filter_fn = lambda c: (c.volume_type == "技术标" or c.doc_category == "施工组织设计与技术方案"
                               or (include_letter and c.doc_category in {"投标函与承诺书", "其他投标材料"}))
    elif category == "Commercial":
        filter_fn = lambda c: c.doc_category in {"投标函与承诺书", "投标保证金证明", "报价文件与工程量清单"} or c.volume_type == "报价标"
    elif category == "Schedule":
        query = f"工期 日历天 投标有效期 进度计划 {requirement}"
        filter_fn = lambda c: c.doc_category == "投标函与承诺书" or "进度" in c.section_path or "工期" in c.text or "有效期" in c.text
    elif category == "Consistency":
        filter_fn = lambda c: c.volume_type != "技术标"
    elif category == "Integrity":
        query = f"投标诚信承诺书 承诺函 弄虚作假 串通投标 信誉情况表 {requirement}"
        filter_fn = lambda c: c.doc_category in {"投标函与承诺书", "企业信誉与无违法承诺", "营业执照与企业资质证书"}
    elif category == "Scoring":
        technical = any(k in requirement for k in ("施工组织", "施工方案", "保证体系", "管理体系", "技术措施", "环保", "文明施工", "风险", "资源计划"))
        filter_fn = (lambda c: c.volume_type == "技术标") if technical else (lambda c: c.volume_type != "技术标")
        dense, sparse = 0.55, 0.45
    return dict(query=query, filter_fn=filter_fn, dense_weight=dense, sparse_weight=sparse)


class PassageRetriever:
    def __init__(self, index: HybridBidIndex):
        self.index = index

    def retrieve(self, requirement, category=None, top_k=5, candidate_pool_size=50):
        """Return ranked child hits and full parent context, without extracting fields."""
        policy = category_search_policy(requirement, category)
        return self.index.search(**policy, top_k=top_k, use_reranker=True,
                                 candidate_pool_size=candidate_pool_size)


def passage_to_dict(hit, sources):
    """Serialize only evidence text and locating metadata for a subsequent LLM."""
    child, parent = hit.child_chunk, hit.parent_chunk
    return dict(rank=hit.rank, score=float(hit.score), document_id=parent.document_id,
                source=sources[parent.document_id]["path"],
                source_sha256=sources[parent.document_id]["sha256"],
                chunk_id=child.chunk_id, parent_id=parent.chunk_id,
                page_start=parent.page_start, page_end=parent.page_end,
                child_page_start=child.page_start, child_page_end=child.page_end,
                doc_category=parent.doc_category, section_path=parent.section_path,
                child_text=child.text, parent_text=parent.text)
