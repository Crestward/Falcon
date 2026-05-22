"""Phase 2 contradiction detection + checkpoint_4 routing tests."""
from __future__ import annotations

import uuid

from core.schemas import (
    AccountProfile,
    ExpansionRequest,
    FraudTypology,
    NetworkEdge,
    NetworkGraph,
    NetworkNode,
    SeverityTier,
    TriageAssessment,
    TypologyAssessment,
    TypologyMatch,
)
from supervisor.confidence import compute_confidence
from supervisor.contradiction import detect_contradictions
from supervisor.graph import route_after_checkpoint_4


def _net(edges_kw: list[dict] | None = None) -> NetworkGraph:
    edges = [NetworkEdge(**e) for e in (edges_kw or [])]
    nodes = list({n for e in edges for n in (e.source, e.target)})
    return NetworkGraph(
        nodes=[NetworkNode(account_id=n, risk_score=0.5) for n in nodes],
        edges=edges,
        suspicious_clusters=[],
        expansion_request=ExpansionRequest(trigger=False, new_accounts=[]),
    )


def _typ(t: FraudTypology, score: float = 0.7, detectors: list[str] | None = None) -> TypologyAssessment:
    return TypologyAssessment(
        primary_typology=t,
        primary_score=score,
        matches=[TypologyMatch(typology=t, score=score, triggered_detectors=detectors or [])],
        rationale="rationale placeholder text padded out to clear min length",
    )


def _profile_business() -> AccountProfile:
    return AccountProfile(
        account_id="AC1",
        baseline={"transaction_count": 80, "channel_mix": {"card": 70, "transfer": 10}},
        anomalies=[],
        flagged_transaction_ids=[],
        counterparty_account_ids=[],
        semantic_matches=[],
    )


def test_contradiction_structuring_vs_business_pattern() -> None:
    report = detect_contradictions(_typ(FraudTypology.STRUCTURING), {"AC1": _profile_business()}, _net())
    assert any("STRUCTURING" in c for c in report.contradictions)
    assert report.confidence_penalty > 0


def test_contradiction_account_takeover_vs_shared_device() -> None:
    net = _net([{
        "source": "AC1", "target": "AC2",
        "relationship_type": "shared_device", "source_type": "derived", "weight": 1.0,
    }])
    report = detect_contradictions(_typ(FraudTypology.ACCOUNT_TAKEOVER), {}, net)
    assert any("ACCOUNT_TAKEOVER" in c for c in report.contradictions)


def test_contradiction_pep_without_watchlist_hit() -> None:
    report = detect_contradictions(_typ(FraudTypology.PEP_EXPOSURE, detectors=[]), {}, _net())
    assert any("PEP" in c for c in report.contradictions)


def test_no_contradictions_on_clean_inputs() -> None:
    report = detect_contradictions(
        _typ(FraudTypology.LAYERING, detectors=["chain_topology"]),
        {},
        _net([{"source": "AC1", "target": "AC2",
               "relationship_type": "transacted_with", "source_type": "derived", "weight": 0.5}]),
    )
    assert not report.contradictions


# Confidence -----------------------------------------------------------------


def _triage(sev: SeverityTier) -> TriageAssessment:
    return TriageAssessment(
        severity=sev,
        recommended_depth="FULL",  # type: ignore[arg-type]
        initial_hypothesis="hypothesis placeholder text",
        quick_signals={},
        justification="justification placeholder text",
    )


def test_confidence_increases_with_typology_score() -> None:
    low = compute_confidence(_triage(SeverityTier.MEDIUM), {}, _net(), typology=_typ(FraudTypology.NONE, score=0.0))
    high = compute_confidence(_triage(SeverityTier.MEDIUM), {}, _net(), typology=_typ(FraudTypology.LAYERING, score=0.9))
    assert high > low


def test_confidence_decreases_with_contradiction_penalty() -> None:
    from core.schemas import ContradictionReport

    base = compute_confidence(_triage(SeverityTier.HIGH), {}, _net(), typology=_typ(FraudTypology.LAYERING, score=0.5))
    penalised = compute_confidence(
        _triage(SeverityTier.HIGH), {}, _net(),
        typology=_typ(FraudTypology.LAYERING, score=0.5),
        contradictions=ContradictionReport(contradictions=["x", "y"], confidence_penalty=0.2),
    )
    assert penalised < base


# Routing --------------------------------------------------------------------


def test_route_auto_close_on_low_confidence() -> None:
    assert route_after_checkpoint_4({"confidence_score": 0.2}) == "auto_close"


def test_route_hitl_on_medium_confidence() -> None:
    for c in (0.4, 0.55, 0.75):
        assert route_after_checkpoint_4({"confidence_score": c}) == "hitl_pause"


def test_route_case_writer_on_high_confidence() -> None:
    assert route_after_checkpoint_4({"confidence_score": 0.85}) == "case_writer"


_ = uuid
