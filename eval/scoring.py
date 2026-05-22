"""Deterministic scoring against ground truth — no LLM calls.

The judge (LLM-based) lives in `eval.judge`; this module is pure functions
so it is fast, cheap, and unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AlertScore:
    """Deterministic per-alert scores. The LLM-judge metrics (faithfulness,
    hallucination) are layered on top in `eval.judge` and merged in the harness."""

    alert_id: str
    expected_verdict: str
    got_verdict: str
    expected_typology: str
    got_typology: str
    verdict_correct: bool
    typology_correct: bool
    network_recall: float
    network_precision: float
    evidence_recall: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "expected_verdict": self.expected_verdict,
            "got_verdict": self.got_verdict,
            "expected_typology": self.expected_typology,
            "got_typology": self.got_typology,
            "verdict_correct": self.verdict_correct,
            "typology_correct": self.typology_correct,
            "network_recall": round(self.network_recall, 3),
            "network_precision": round(self.network_precision, 3),
            "evidence_recall": round(self.evidence_recall, 3),
        }


def _recall(found: set[str], expected: set[str]) -> float:
    if not expected:
        return 1.0
    return len(found & expected) / len(expected)


def _precision(found: set[str], expected: set[str]) -> float:
    if not found:
        # No predictions and no expectations is vacuous-perfect; predictions
        # without expectations are vacuous-zero (we found things we shouldn't).
        return 1.0 if not expected else 0.0
    return len(found & expected) / len(found)


def _evidence_recall(case_evidence_text: str, expected_evidence: list[str]) -> float:
    """Lenient: each expected evidence line counts as 'recalled' if any
    meaningful keyword from it appears in the case file evidence text. This
    is intentionally generous — LLM-generated wording will differ. The
    LLM-judge faithfulness score is the strict version."""
    if not expected_evidence:
        return 1.0
    haystack = case_evidence_text.lower()
    hits = 0
    for expected in expected_evidence:
        keywords = [w for w in expected.lower().split() if len(w) > 4]
        if not keywords:
            keywords = [expected.lower()[:8]]
        # Hit if half-or-more of the meaningful keywords appear.
        matched = sum(1 for kw in keywords if kw in haystack)
        if matched >= max(1, len(keywords) // 2):
            hits += 1
    return hits / len(expected_evidence)


def score_alert(
    *,
    alert_id: str,
    ground_truth: dict[str, Any],
    got_verdict: str,
    got_typology: str,
    found_network_accounts: list[str],
    case_evidence_text: str,
) -> AlertScore:
    """Deterministic scoring for one alert."""
    expected_verdict = ground_truth.get("expected_verdict", "AUTO_CLOSE")
    expected_typology = ground_truth.get("expected_typology", "NONE")
    expected_accounts = set(ground_truth.get("expected_network_accounts") or [])
    expected_evidence = ground_truth.get("expected_evidence") or []

    found = set(found_network_accounts or [])
    return AlertScore(
        alert_id=alert_id,
        expected_verdict=expected_verdict,
        got_verdict=got_verdict,
        expected_typology=expected_typology,
        got_typology=got_typology,
        verdict_correct=(expected_verdict == got_verdict),
        typology_correct=(expected_typology == got_typology),
        network_recall=_recall(found, expected_accounts),
        network_precision=_precision(found, expected_accounts),
        evidence_recall=_evidence_recall(case_evidence_text, expected_evidence),
    )


def aggregate(scores: list[AlertScore]) -> dict[str, Any]:
    """Roll per-alert scores up to the summary the README/dashboard publish."""
    n = len(scores)
    if n == 0:
        return {"total": 0}

    genuine = [s for s in scores if s.expected_verdict != "AUTO_CLOSE"]
    clean = [s for s in scores if s.expected_verdict == "AUTO_CLOSE"]
    # False positive: clean alert raised to REVIEW or SAR_FILE.
    fp = [s for s in clean if s.got_verdict in {"REVIEW", "SAR_FILE"}]
    # False negative: genuine alert dropped to AUTO_CLOSE.
    fn = [s for s in genuine if s.got_verdict == "AUTO_CLOSE"]

    by_typology: dict[str, dict[str, Any]] = {}
    for s in scores:
        bucket = by_typology.setdefault(
            s.expected_typology,
            {"n": 0, "verdict_correct": 0, "typology_correct": 0, "network_recall_sum": 0.0},
        )
        bucket["n"] += 1
        bucket["verdict_correct"] += int(s.verdict_correct)
        bucket["typology_correct"] += int(s.typology_correct)
        bucket["network_recall_sum"] += s.network_recall

    for bucket in by_typology.values():
        bucket["verdict_accuracy"] = round(bucket["verdict_correct"] / bucket["n"], 3)
        bucket["typology_accuracy"] = round(bucket["typology_correct"] / bucket["n"], 3)
        bucket["network_recall_mean"] = round(bucket["network_recall_sum"] / bucket["n"], 3)
        bucket.pop("network_recall_sum")

    confusion: dict[str, dict[str, int]] = {}
    for s in scores:
        row = confusion.setdefault(s.expected_verdict, {})
        row[s.got_verdict] = row.get(s.got_verdict, 0) + 1

    return {
        "total": n,
        "verdict_accuracy": round(sum(s.verdict_correct for s in scores) / n, 3),
        "typology_accuracy": round(sum(s.typology_correct for s in scores) / n, 3),
        "network_recall_mean": round(sum(s.network_recall for s in scores) / n, 3),
        "network_precision_mean": round(sum(s.network_precision for s in scores) / n, 3),
        "evidence_recall_mean": round(sum(s.evidence_recall for s in scores) / n, 3),
        "false_positive_rate": round(len(fp) / len(clean), 3) if clean else None,
        "false_negative_rate": round(len(fn) / len(genuine), 3) if genuine else None,
        "by_typology": by_typology,
        "confusion_matrix": confusion,
    }
