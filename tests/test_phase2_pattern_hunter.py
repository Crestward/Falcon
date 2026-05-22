"""Phase 2 typology detector + reconciler tests — no LLM, no DB."""
from __future__ import annotations

from agents.detectors import (
    detect_layering,
    detect_mule_network,
    detect_structuring,
    pick_primary,
)
from agents.reconciler import needs_reconciliation, triage_implied_typology
from core.schemas import (
    AccountProfile,
    ExpansionRequest,
    FraudTypology,
    InvestigationDepth,
    NetworkEdge,
    NetworkGraph,
    NetworkNode,
    SeverityTier,
    TriageAssessment,
    TypologyAssessment,
    TypologyMatch,
)


def _empty_network() -> NetworkGraph:
    return NetworkGraph(
        nodes=[], edges=[], suspicious_clusters=[],
        expansion_request=ExpansionRequest(trigger=False, new_accounts=[]),
    )


def _profile_with_flagged(n_flagged: int, cash: int = 0) -> AccountProfile:
    return AccountProfile(
        account_id="AC1",
        baseline={
            "transaction_count": max(n_flagged, 1),
            "channel_mix": {"cash": cash, "card": max(0, 10 - cash)},
        },
        anomalies=[],
        flagged_transaction_ids=list(range(n_flagged)),
        counterparty_account_ids=[],
        semantic_matches=[],
    )


def test_structuring_detector_high_score_when_flagged_and_cash() -> None:
    profiles = {"AC1": _profile_with_flagged(8, cash=10)}
    m = detect_structuring(profiles)
    assert m.typology == FraudTypology.STRUCTURING
    assert m.score > 0.3


def test_structuring_detector_low_score_on_clean_profile() -> None:
    profiles = {"AC1": _profile_with_flagged(0, cash=0)}
    m = detect_structuring(profiles)
    assert m.score == 0.0


def test_layering_detector_recognises_chain_topology() -> None:
    nodes = [NetworkNode(account_id=f"AC{i}", risk_score=0.5) for i in range(5)]
    edges = [
        NetworkEdge(
            source=f"AC{i}", target=f"AC{i+1}",
            relationship_type="transacted_with", source_type="derived", weight=0.5,
        )
        for i in range(4)
    ]
    net = NetworkGraph(nodes=nodes, edges=edges, suspicious_clusters=[],
                      expansion_request=ExpansionRequest(trigger=False, new_accounts=[]))
    m = detect_layering({}, net)
    assert m.score >= 0.5
    assert "chain" in " ".join(m.evidence).lower()


def test_mule_detector_recognises_shared_device_hub() -> None:
    nodes = [NetworkNode(account_id=f"AC{i}", risk_score=0.5) for i in range(4)]
    edges = [
        NetworkEdge(source="AC0", target=f"AC{i}", relationship_type="shared_device",
                    source_type="derived", weight=1.0)
        for i in range(1, 4)
    ]
    net = NetworkGraph(nodes=nodes, edges=edges, suspicious_clusters=[],
                      expansion_request=ExpansionRequest(trigger=False, new_accounts=[]))
    m = detect_mule_network({}, net)
    assert m.score >= 0.4


def test_pick_primary_returns_none_below_threshold() -> None:
    matches = [
        TypologyMatch(typology=FraudTypology.STRUCTURING, score=0.1),
        TypologyMatch(typology=FraudTypology.LAYERING, score=0.15),
    ]
    p = pick_primary(matches)
    assert p.typology == FraudTypology.NONE


def test_pick_primary_returns_top() -> None:
    matches = [
        TypologyMatch(typology=FraudTypology.STRUCTURING, score=0.3),
        TypologyMatch(typology=FraudTypology.LAYERING, score=0.7),
    ]
    assert pick_primary(matches).typology == FraudTypology.LAYERING


# Reconciler ----------------------------------------------------------------


def _triage(text: str) -> TriageAssessment:
    return TriageAssessment(
        severity=SeverityTier.HIGH,
        recommended_depth=InvestigationDepth.DEEP,
        initial_hypothesis=text,
        quick_signals={},
        justification="justification placeholder string",
    )


def _hunter(typology: FraudTypology, score: float = 0.6) -> TypologyAssessment:
    return TypologyAssessment(
        primary_typology=typology,
        primary_score=score,
        matches=[],
        rationale="hunter rationale placeholder text " * 5,
    )


def test_reconciler_detects_keyword_implied_typology() -> None:
    assert triage_implied_typology(_triage("likely structuring activity")) == FraudTypology.STRUCTURING
    assert triage_implied_typology(_triage("possible account takeover")) == FraudTypology.ACCOUNT_TAKEOVER
    assert triage_implied_typology(_triage("PEP exposure suspected")) == FraudTypology.PEP_EXPOSURE
    assert triage_implied_typology(_triage("vague concerns")) is None


def test_reconciler_needed_when_hypotheses_conflict() -> None:
    t = _triage("layering chain pattern observed")
    h = _hunter(FraudTypology.MULE_NETWORK)
    assert needs_reconciliation(t, h)


def test_reconciler_not_needed_when_agreed() -> None:
    t = _triage("layering chain pattern observed")
    h = _hunter(FraudTypology.LAYERING)
    assert not needs_reconciliation(t, h)


def test_reconciler_not_needed_when_triage_ambiguous() -> None:
    t = _triage("unspecified concerns require deeper review")
    h = _hunter(FraudTypology.STRUCTURING)
    assert not needs_reconciliation(t, h)
