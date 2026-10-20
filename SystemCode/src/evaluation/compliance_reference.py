"""Exploratory agreement with assistant labels, separate from human gold accuracy."""


VERDICTS = {"COMPLIANT", "NON_COMPLIANT", "INSUFFICIENT_EVIDENCE",
            "CONFLICTING_EVIDENCE", "SCORED", "UNSUPPORTED_RULE"}


def compare_assistant_reference(cases, results, reference):
    if reference.get("schema_version") != 2 or reference.get("scope") != "full_source_document_review":
        raise ValueError("Expected calibrated full-source reference schema 2")
    case_map = {c["clause_id"]: c for c in cases}
    rows = reference["queries"]
    ids = [q["clause_id"] for q in rows]
    prediction_ids = [r["clause_id"] for r in results]
    if (len(ids) != len(set(ids)) or set(ids) != set(case_map)
            or len(prediction_ids) != len(set(prediction_ids)) or set(prediction_ids) != set(case_map)):
        raise ValueError("Reference and prediction IDs must match cases exactly")
    predictions = {r["clause_id"]: r for r in results}
    details = []
    for q in rows:
        if q["requirement"] != case_map[q["clause_id"]]["requirement"]:
            raise ValueError("Reference requirement changed")
        if q["review_status"] != "assistant_verified" or not q.get("review_note"):
            raise ValueError("Assistant comparison requires documented assistant references")
        expected = q["expected_verdict"]
        if expected is not None and expected not in VERDICTS:
            raise ValueError("Invalid reference verdict")
        if q.get("expected_score") is not None and expected != "SCORED":
            raise ValueError("Score reference requires SCORED")
        pred = predictions[q["clause_id"]]
        details.append(dict(clause_id=q["clause_id"], expected_verdict=expected,
            predicted_verdict=pred["verdict"],
            verdict_agrees=(expected == pred["verdict"]) if expected is not None else None,
            expected_score=q.get("expected_score"), predicted_score=pred.get("awarded_score"),
            score_agrees=(pred["verdict"] == "SCORED" and pred.get("awarded_score") == q["expected_score"])
                if q.get("expected_score") is not None else None,
            review_note=q["review_note"]))
    judged = [d for d in details if d["verdict_agrees"] is not None]
    scored = [d for d in details if d["score_agrees"] is not None]
    return dict(evaluation_scope="assistant_reference_diagnostic_comparison",
                total_cases=len(cases), assistant_reference_cases=len(judged),
                excluded_subjective_cases=len(cases)-len(judged),
                assistant_reference_verdict_agreement=sum(d["verdict_agrees"] for d in judged)/len(judged) if judged else None,
                assistant_score_cases=len(scored),
                assistant_reference_score_agreement=sum(d["score_agrees"] for d in scored)/len(scored) if scored else None,
                formal_human_gold_accuracy=None, details=details,
                note="Exploratory agreement, not human gold accuracy. Full-source reference vs actual Top-K predictions: disagreements may arise from retrieval, missing tender context, extraction or rules. Abstention is not proof of noncompliance.")
