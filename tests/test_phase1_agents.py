"""Phase 1 unit tests — pure logic, no LLM, no DB writes."""
from __future__ import annotations

from agents.account_historian import _compute_stats, _detect_anomaly_windows
from core.schemas import (
    AccountProfile,
    ExpansionRequest,
    NetworkGraph,
    SeverityTier,
    TriageAssessment,
)
from supervisor.confidence import compute_confidence
from supervisor.config import CONF_AUTO_CLOSE_BELOW, CONF_SAR_ABOVE, MAX_EXPANSIONS


def _triage(severity: SeverityTier) -> TriageAssessment:
    return TriageAssessment(
        severity=severity,
        recommended_depth="FULL",  # type: ignore[arg-type]
        initial_hypothesis="hypothesis for test purposes",
        quick_signals={},
        justification="justification for test purposes",
    )


def _empty_network(expand: bool = False) -> NetworkGraph:
    return NetworkGraph(
        nodes=[], edges=[], suspicious_clusters=[],
        expansion_request=ExpansionRequest(trigger=expand, new_accounts=[]),
    )


def test_compute_stats_empty_history() -> None:
    out = _compute_stats([])
    assert out["transaction_count"] == 0
    assert out["flagged_transaction_ids"] == []


def test_compute_stats_flags_outliers() -> None:
    history = [
        {"id": i, "amount": 100.0, "direction": "debit", "channel": "card",
         "merchant": "m", "merchant_category": "groceries",
         "counterparty_account_id": None, "timestamp": "2026-01-01T00:00:00+00:00"}
        for i in range(20)
    ]
    history.append({
        "id": 999, "amount": 100000.0, "direction": "debit", "channel": "wire",
        "merchant": "m", "merchant_category": "transfer",
        "counterparty_account_id": "AC9", "timestamp": "2026-01-02T00:00:00+00:00",
    })
    out = _compute_stats(history)
    assert 999 in out["flagged_transaction_ids"]
    assert out["counterparty_unique"] == 1


def test_detect_anomaly_windows_clusters_bursts() -> None:
    history = [
        {"id": 1, "amount": 10, "direction": "debit", "channel": "x",
         "merchant": None, "merchant_category": None,
         "counterparty_account_id": None, "timestamp": "2026-01-01T00:00:00+00:00"},
        {"id": 2, "amount": 10, "direction": "debit", "channel": "x",
         "merchant": None, "merchant_category": None,
         "counterparty_account_id": None, "timestamp": "2026-01-01T01:00:00+00:00"},
        {"id": 3, "amount": 10, "direction": "debit", "channel": "x",
         "merchant": None, "merchant_category": None,
         "counterparty_account_id": None, "timestamp": "2026-02-01T00:00:00+00:00"},
    ]
    windows = _detect_anomaly_windows(history, [1, 2, 3])
    assert len(windows) == 2  # one burst + one isolated


def test_confidence_bounds_and_monotonic() -> None:
    base = compute_confidence(_triage(SeverityTier.LOW), {}, _empty_network())
    high = compute_confidence(_triage(SeverityTier.CRITICAL), {}, _empty_network(expand=True))
    assert 0.0 <= base <= 1.0
    assert 0.0 <= high <= 1.0
    assert high > base


def test_confidence_clamped_to_one() -> None:
    profiles = {
        "AC1": AccountProfile(
            account_id="AC1", baseline={}, anomalies=[],
            flagged_transaction_ids=list(range(1000)),
            counterparty_account_ids=[], semantic_matches=[],
        )
    }
    c = compute_confidence(_triage(SeverityTier.CRITICAL), profiles, _empty_network(expand=True))
    assert c <= 1.0


def test_confidence_thresholds_consistent() -> None:
    assert CONF_AUTO_CLOSE_BELOW < CONF_SAR_ABOVE
    assert 0.0 <= CONF_AUTO_CLOSE_BELOW <= CONF_SAR_ABOVE <= 1.0


def test_max_expansions_is_positive() -> None:
    assert MAX_EXPANSIONS >= 1
