"""
Cross-Clause Impact Graph (`(document, page_no) -> [dependent clauses]`) for Phase 3.1.

Enables impact-aware cascade re-evaluation whenever a Bid PDF page is edited during
remediation: modifying a shared anchor page (such as Bid Letter price, duration, or
project manager) automatically identifies and re-evaluates all sibling clauses citing
that page, preventing "fix one clause, silently break another".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


def _canonical_doc_keys(
    *,
    doc_path: str = "",
    doc_id: str = "",
    doc_label: str = "",
    doc_sources: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Set[str]:
    """Build normalized lookup keys for a bid document so path, filename, and doc_id aliases match."""
    keys: Set[str] = set()
    sources = doc_sources or {}

    resolved_path = doc_path
    if not resolved_path and doc_id and doc_id in sources:
        resolved_path = str(sources[doc_id].get("path") or "")

    if resolved_path:
        p = Path(resolved_path)
        try:
            keys.add(f"path:{str(p.resolve()).lower()}")
        except Exception:
            keys.add(f"path:{str(p).lower()}")
        if p.name:
            keys.add(f"name:{p.name.lower()}")

    if doc_id:
        keys.add(f"id:{str(doc_id).strip().lower()}")
        src = sources.get(str(doc_id))
        if src:
            if src.get("path"):
                p = Path(str(src["path"]))
                try:
                    keys.add(f"path:{str(p.resolve()).lower()}")
                except Exception:
                    keys.add(f"path:{str(p).lower()}")
                if p.name:
                    keys.add(f"name:{p.name.lower()}")
            if src.get("filename"):
                keys.add(f"name:{str(src['filename']).strip().lower()}")

    if doc_label:
        lbl = str(doc_label).strip().lower()
        if lbl.endswith(".pdf"):
            keys.add(f"name:{lbl}")

    return keys


class ClauseImpactGraph:
    """
    Reverse index mapping `(document_key, page_no)` to review results that cite that page
    either in `result["evidence"]` or in `result["chain"]["backend_state"]["evidence_context"]`.
    """

    def __init__(
        self,
        results: Iterable[Dict[str, Any]],
        doc_sources: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        self._doc_sources: Dict[str, Dict[str, Any]] = dict(doc_sources or {})
        self._results_by_id: Dict[int, Dict[str, Any]] = {}
        self._page_index: Dict[Tuple[str, int], Set[int]] = {}

        for res in results:
            rid = int(res["id"])
            self._results_by_id[rid] = res
            self._index_result(rid, res)

    def _add_link(self, doc_keys: Set[str], page_no: int, result_id: int) -> None:
        if page_no <= 0 or not doc_keys:
            return
        for key in doc_keys:
            self._page_index.setdefault((key, page_no), set()).add(result_id)

    def _index_result(self, result_id: int, res: Dict[str, Any]) -> None:
        # 1. Index visible evidence entries
        for ev in res.get("evidence") or []:
            if not isinstance(ev, dict):
                continue
            pg = int(ev.get("page") or 0)
            if pg <= 0:
                continue
            keys = _canonical_doc_keys(
                doc_path=str(ev.get("doc_path") or ""),
                doc_id=str(ev.get("document_id") or ev.get("doc_id") or ""),
                doc_label=str(ev.get("doc_label") or ""),
                doc_sources=self._doc_sources,
            )
            self._add_link(keys, pg, result_id)

        # 2. Index backend evidence_context passages (captures multi-page ranges & unhighlighted anchors)
        chain = res.get("chain") or {}
        backend_state = chain.get("backend_state") if isinstance(chain, dict) else None
        if isinstance(backend_state, dict):
            for passage in backend_state.get("evidence_context") or []:
                if not isinstance(passage, dict):
                    continue
                p_start = int(passage.get("page_start") or 0)
                p_end = int(passage.get("page_end") or p_start)
                if p_start <= 0:
                    continue
                keys = _canonical_doc_keys(
                    doc_path=str(passage.get("source") or ""),
                    doc_id=str(passage.get("document_id") or ""),
                    doc_sources=self._doc_sources,
                )
                # Guard against absurdly wide synthetic ranges
                for pg in range(p_start, min(p_end, p_start + 20) + 1):
                    self._add_link(keys, pg, result_id)

    def impacted_results(
        self,
        doc_path: str,
        page_no: int,
        *,
        exclude_result_id: Optional[int] = None,
        doc_id: str = "",
        doc_label: str = "",
    ) -> List[Dict[str, Any]]:
        """Return all results in this round that depend on `(doc_path/doc_id, page_no)`."""
        page_int = int(page_no or 0)
        if page_int <= 0:
            return []
        query_keys = _canonical_doc_keys(
            doc_path=doc_path,
            doc_id=doc_id,
            doc_label=doc_label,
            doc_sources=self._doc_sources,
        )
        matched_ids: Set[int] = set()
        for key in query_keys:
            matched_ids.update(self._page_index.get((key, page_int), set()))

        if exclude_result_id is not None:
            matched_ids.discard(int(exclude_result_id))

        impacted = [self._results_by_id[rid] for rid in matched_ids if rid in self._results_by_id]
        impacted.sort(key=lambda r: (str(r.get("clause_no") or ""), int(r["id"])))
        return impacted

    def impact_summary(
        self,
        doc_path: str,
        page_no: int,
        *,
        exclude_result_id: Optional[int] = None,
        doc_id: str = "",
        doc_label: str = "",
    ) -> Dict[str, Any]:
        """Return a UI-friendly summary of sibling clauses impacted by editing `(doc_path, page_no)`."""
        impacted = self.impacted_results(
            doc_path,
            page_no,
            exclude_result_id=exclude_result_id,
            doc_id=doc_id,
            doc_label=doc_label,
        )
        return {
            "count": len(impacted),
            "clauses": [
                {
                    "result_id": int(r["id"]),
                    "requirement_id": int(r["requirement_id"]),
                    "clause_no": str(r.get("clause_no") or ""),
                    "category": str(r.get("category") or ""),
                    "verdict": str(r.get("verdict") or ""),
                }
                for r in impacted
            ],
        }
