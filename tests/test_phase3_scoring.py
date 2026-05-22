"""Phase 3 — deterministic scorer. Pure functions, no DB, no LLM."""
from __future__ import annotations

from eval.scoring import aggregate, score_alert


def _gt(**over):
    base = {
        "expected_verdict": "SAR_FILE",
        "expected_typology": "STRUCTURING",
        "expected_network_accounts": ["AC0001", "AC0002", "AC0003"],
        "expected_evidence": [
            "7 near-threshold credits within 72h",
            "Account opened within 60 days of pattern",
        ],
    }
    base.update(over)
    return base


def test_perfect_match():
    s = score_alert(
        alert_id="ALERT0001",
        ground_truth=_gt(),
        got_verdict="SAR_FILE",
        got_typology="STRUCTURING",
        found_network_accounts=["AC0001", "AC0002", "AC0003"],
        case_evidence_text="7 near-threshold credits within 72h on a recently opened account",
    )
    assert s.verdict_correct
    assert s.typology_correct
    assert s.network_recall == 1.0
    assert s.network_precision == 1.0
    assert s.evidence_recall >= 0.5


def test_wrong_verdict_and_typology():
    s = score_alert(
        alert_id="ALERT0099",
        ground_truth=_gt(),
        got_verdict="AUTO_CLOSE",
        got_typology="ACCOUNT_TAKEOVER",
        found_network_accounts=[],
        case_evidence_text="",
    )
    assert not s.verdict_correct
    assert not s.typology_correct
    assert s.network_recall == 0.0


def test_partial_network_recall_and_precision():
    s = score_alert(
        alert_id="A",
        ground_truth=_gt(expected_network_accounts=["AC1", "AC2", "AC3", "AC4"]),
        got_verdict="SAR_FILE",
        got_typology="STRUCTURING",
        found_network_accounts=["AC1", "AC2", "AC9"],
        case_evidence_text="anything",
    )
    assert s.network_recall == 0.5  # 2 of 4
    assert round(s.network_precision, 2) == round(2 / 3, 2)


def test_clean_alert_with_no_expected_accounts_recall_one():
    s = score_alert(
        alert_id="ALERT_CLEAN",
        ground_truth={
            "expected_verdict": "AUTO_CLOSE",
            "expected_typology": "NONE",
            "expected_network_accounts": [],
            "expected_evidence": [],
        },
        got_verdict="AUTO_CLOSE",
        got_typology="NONE",
        found_network_accounts=[],
        case_evidence_text="",
    )
    assert s.network_recall == 1.0
    assert s.network_precision == 1.0
    assert s.evidence_recall == 1.0


def test_false_positive_on_clean_alert():
    s = score_alert(
        alert_id="ALERT_CLEAN",
        ground_truth={
            "expected_verdict": "AUTO_CLOSE",
            "expected_typology": "NONE",
            "expected_network_accounts": [],
            "expected_evidence": [],
        },
        got_verdict="SAR_FILE",
        got_typology="STRUCTURING",
        found_network_accounts=["AC1"],
        case_evidence_text="something",
    )
    assert not s.verdict_correct
    # Precision is 0 — we found accounts where none were expected.
    assert s.network_precision == 0.0


def test_aggregate_basics():
    scores = [
        score_alert(
            alert_id=f"A{i}",
            ground_truth=_gt(),
            got_verdict="SAR_FILE",
            got_typology="STRUCTURING",
            found_network_accounts=["AC0001", "AC0002", "AC0003"],
            case_evidence_text="7 near-threshold credits within 72h",
        )
        for i in range(3)
    ]
    # one clean alert correctly closed
    scores.append(
        score_alert(
            alert_id="A_clean",
            ground_truth={
                "expected_verdict": "AUTO_CLOSE",
                "expected_typology": "NONE",
                "expected_network_accounts": [],
                "expected_evidence": [],
            },
            got_verdict="AUTO_CLOSE",
            got_typology="NONE",
            found_network_accounts=[],
            case_evidence_text="",
        )
    )
    agg = aggregate(scores)
    assert agg["total"] == 4
    assert agg["verdict_accuracy"] == 1.0
    assert agg["typology_accuracy"] == 1.0
    assert agg["false_positive_rate"] == 0.0
    assert agg["false_negative_rate"] == 0.0
    assert "STRUCTURING" in agg["by_typology"]
    assert agg["by_typology"]["STRUCTURING"]["n"] == 3


def test_aggregate_handles_empty():
    assert aggregate([]) == {"total": 0}
