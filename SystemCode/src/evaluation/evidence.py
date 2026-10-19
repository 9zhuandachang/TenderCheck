"""Reference-evidence metrics. Keywords and business field values never award credit."""
import re
import unicodedata


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def matches_location(hit, reference):
    """A returned parent must contain the annotated quote at the right source/pages."""
    return (hit["source_sha256"] == reference["source_sha256"]
            and hit["page_start"] <= reference["page_start"] <= reference["page_end"] <= hit["page_end"]
            and bool(compact(reference["quote"]))
            and contains_reference_text(hit["parent_text"], reference))


def contains_reference_text(text, reference):
    """Match reviewed spans in order, allowing gaps between table cells.

    Never discard digits, signs or punctuation to force a match. For a table,
    annotators may explicitly specify required quote_parts from its source quote.
    """
    text, offset = compact(text), 0
    for part in reference.get("quote_parts", [reference["quote"]]):
        part = compact(part)
        pos = text.find(part, offset)
        if not part or pos < 0:
            return False
        offset = pos + len(part)
    return True


def validate_annotations(annotations, runs):
    if annotations.get("schema_version") != 2:
        raise ValueError("Expected reference-evidence schema_version=2; keyword benchmarks cannot be scored")
    if annotations.get("corpus_sha256") != runs["corpus_sha256"]:
        raise ValueError("Annotations belong to a different corpus")
    query_map = {q["clause_id"]: q for q in runs["queries"]}
    rows = annotations["queries"]
    ids = [q["clause_id"] for q in rows]
    if len(set(ids)) != len(ids) or set(ids) != set(query_map):
        raise ValueError("Annotation and retrieval query IDs must agree exactly")
    for q in rows:
        if q["requirement"] != query_map[q["clause_id"]]["requirement"]:
            raise ValueError("Requirement changed after retrieval")
        if q["review_status"] not in {"pending", "assistant_verified", "human_verified"}:
            raise ValueError("Unknown review status")
        if q["evidence_status"] not in {"unknown", "present", "absent"}:
            raise ValueError("Unknown evidence status")
        if q["review_status"] == "pending":
            continue
        if not q.get("reviewer") or not q.get("review_note"):
            raise ValueError("Reviewed annotations require reviewer and review_note")
        groups = q["evidence_groups"]
        if q["evidence_status"] == "present" and not groups:
            raise ValueError("Present evidence needs reference groups")
        if q["evidence_status"] == "absent" and groups:
            raise ValueError("Absent evidence cannot contain positive references")
        if len({g["group_id"] for g in groups}) != len(groups):
            raise ValueError("Duplicate evidence group")
        for group in groups:
            if not group["alternatives"]:
                raise ValueError("Evidence group cannot be empty")
            for ref in group["alternatives"]:
                source = runs["sources"].get(ref["document_id"])
                if (source is None or source["sha256"] != ref["source_sha256"]
                        or not 1 <= ref["page_start"] <= ref["page_end"] <= source["page_count"]
                        or not compact(ref["quote"]) or not ref.get("source_verified")
                        or ref.get("verification_basis") not in {"native_pdf", "visual_pdf"}):
                    raise ValueError("Invalid or unverified evidence location")
                if "quote_parts" in ref and (not isinstance(ref["quote_parts"], list)
                        or not ref["quote_parts"] or any(not isinstance(p, str) for p in ref["quote_parts"])
                        or not contains_reference_text(ref["quote"], ref)):
                    raise ValueError("quote_parts must be nonempty ordered spans of the verified source quote")


def score_evidence(annotations, runs, ks=(1, 3, 5)):
    validate_annotations(annotations, runs)
    if not ks or min(ks) < 1 or max(ks) > runs["top_k"]:
        raise ValueError("Metric K exceeds collected ranking depth")
    run_map = {q["clause_id"]: q for q in runs["queries"]}
    result = dict(metric_scope="Explicit file identity + physical page span + quote in returned parent context",
                  total_queries=len(annotations["queries"]), strata={})
    # Never pool assistant judgments with human reference labels.
    for status in ("human_verified", "assistant_verified"):
        reviewed = [q for q in annotations["queries"] if q["review_status"] == status]
        eligible = [q for q in reviewed if q["evidence_status"] == "present" and q.get("annotation_complete") is True]
        stratum = dict(reviewed=len(reviewed), scored_positive_queries=len(eligible),
                       reviewed_absent_queries=sum(q["evidence_status"] == "absent" for q in reviewed),
                       partial_or_unknown_queries=sum(q["evidence_status"] == "unknown" or
                           (q["evidence_status"] == "present" and not q.get("annotation_complete")) for q in reviewed), methods={})
        for method in runs["methods"]:
            details = []
            for q in eligible:
                hits = run_map[q["clause_id"]]["methods"][method]
                ranks = [next((i for i, h in enumerate(hits, 1)
                               if any(matches_location(h, ref) for ref in group["alternatives"])), None)
                         for group in q["evidence_groups"]]
                details.append(dict(clause_id=q["clause_id"],group_first_ranks=ranks))
            metrics = dict(num_queries=len(details),num_evidence_groups=sum(len(d["group_first_ranks"]) for d in details))
            for k in ks:
                hits, recall, complete, reciprocal = [], [], [], []
                found_groups = 0
                for d in details:
                    ranks = d["group_first_ranks"]
                    found = [r for r in ranks if r is not None and r <= k]
                    found_groups += len(found)
                    hits.append(bool(found))
                    recall.append(len(found)/len(ranks))
                    complete.append(len(found) == len(ranks))
                    reciprocal.append(1/min(found) if found else 0)
                for key, values in (("hit",hits),("evidence_recall_macro",recall),
                                    ("complete_evidence_hit",complete),("mrr",reciprocal)):
                    metrics[f"{key}@{k}"] = round(sum(values)/len(values),4) if values else None
                metrics[f"evidence_recall_micro@{k}"] = round(found_groups/metrics["num_evidence_groups"],4) if details else None
            stratum["methods"][method] = dict(metrics=metrics,query_details=details)
        result["strata"][status] = stratum
    result["pending_queries"] = sum(q["review_status"] == "pending" for q in annotations["queries"])
    return result
