"""Phase 2 guardrails tests — pure logic, no DB writes required."""
from __future__ import annotations

import uuid

import pytest

from core.schemas import (
    CaseEvidence,
    CaseFileSchema,
    RecommendedAction,
    SeverityTier,
)
from guardrails import (
    JustificationViolation,
    ScopeViolation,
    assert_in_scope,
    enforce_escalation,
    enforce_justification,
    enforce_schema,
    is_in_scope,
    redact_pii,
    scan_for_pii,
)
from guardrails.schema import SchemaViolation

# Scope rail ----------------------------------------------------------------


def test_scope_blocks_outside_account() -> None:
    with pytest.raises(ScopeViolation):
        assert_in_scope("AC_OUT", actor="t", scope={"AC1", "AC2"})


def test_scope_allows_inside_account() -> None:
    assert_in_scope("AC1", actor="t", scope={"AC1", "AC2"})


def test_is_in_scope() -> None:
    assert is_in_scope("AC1", {"AC1", "AC2"})
    assert not is_in_scope("AC3", {"AC1", "AC2"})


# Schema rail ---------------------------------------------------------------


def test_schema_rejects_invalid_payload() -> None:
    from core.schemas import TriageAssessment

    with pytest.raises(SchemaViolation):
        enforce_schema({"severity": "BOGUS"}, schema=TriageAssessment, actor="t")


# Justification rail --------------------------------------------------------


def test_justification_rail_rejects_empty() -> None:
    with pytest.raises(JustificationViolation):
        enforce_justification(
            tool_name="x.y", arguments={}, justification="", actor="t"
        )


def test_justification_rail_accepts_real() -> None:
    out = enforce_justification(
        tool_name="x.y",
        arguments={"a": 1},
        justification="legitimate reason for this tool call",
        actor="t",
    )
    assert out.tool_name == "x.y"


# Escalation rail -----------------------------------------------------------


def _cf(action: RecommendedAction, confidence: float, n_evidence: int) -> CaseFileSchema:
    return CaseFileSchema(
        investigation_id=uuid.uuid4(),
        risk_tier=SeverityTier.CRITICAL if action == RecommendedAction.SAR_FILE else SeverityTier.MEDIUM,
        recommended_action=action,
        sar_ready=(action == RecommendedAction.SAR_FILE),
        confidence=confidence,
        executive_summary="x" * 50,
        suspicion_grounds="grounds",
        subject_details={},
        financial_exposure_estimate=0.0,
        evidence_chain=[
            CaseEvidence(evidence_type=f"e{i}", summary=f"item {i}", confidence=0.5)
            for i in range(n_evidence)
        ],
        network_summary={},
    )


def test_escalation_downgrades_sar_with_thin_evidence() -> None:
    cf = _cf(RecommendedAction.SAR_FILE, confidence=0.9, n_evidence=2)
    out = enforce_escalation(cf)
    assert out.recommended_action == RecommendedAction.REVIEW
    assert out.sar_ready is False


def test_escalation_downgrades_sar_with_low_confidence() -> None:
    cf = _cf(RecommendedAction.SAR_FILE, confidence=0.5, n_evidence=5)
    out = enforce_escalation(cf)
    assert out.recommended_action == RecommendedAction.REVIEW


def test_escalation_allows_sar_when_gated_met() -> None:
    cf = _cf(RecommendedAction.SAR_FILE, confidence=0.8, n_evidence=3)
    out = enforce_escalation(cf)
    assert out.recommended_action == RecommendedAction.SAR_FILE
    assert out.sar_ready is True


def test_escalation_passes_through_review_and_close() -> None:
    cf = _cf(RecommendedAction.REVIEW, confidence=0.5, n_evidence=1)
    assert enforce_escalation(cf).recommended_action == RecommendedAction.REVIEW
    cf2 = _cf(RecommendedAction.AUTO_CLOSE, confidence=0.1, n_evidence=0)
    assert enforce_escalation(cf2).recommended_action == RecommendedAction.AUTO_CLOSE


# PII rail ------------------------------------------------------------------


def test_pii_scan_detects_email_and_person() -> None:
    res = scan_for_pii("Customer John Smith (john.smith@example.com) opened account.")
    types = {r["entity_type"] for r in res}
    assert "EMAIL_ADDRESS" in types
    assert "PERSON" in types


def test_pii_redact_replaces_email() -> None:
    txt = "Contact john.smith@example.com for details."
    out = redact_pii(txt)
    assert "john.smith@example.com" not in out


def test_pii_redact_empty_string_safe() -> None:
    assert redact_pii("") == ""
    assert redact_pii(None) is None  # type: ignore[arg-type]
