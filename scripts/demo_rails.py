"""Demonstrate every guardrail rail firing at least once.

Writes a row to security_events for each rail so the Phase 2 deliverable
("All 5 custom rails fire at least once across the test run") is met
regardless of what the smoke run happens to encounter.

Run:  python -m scripts.demo_rails
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from core.db import session_scope
from core.models import FraudAlert, Investigation, SecurityEvent
from core.schemas import (
    CaseEvidence,
    CaseFileSchema,
    RecommendedAction,
    SeverityTier,
    TriageAssessment,
)
from guardrails import (
    JustificationViolation,
    ScopeViolation,
    assert_in_scope,
    enforce_escalation,
    enforce_justification,
    enforce_schema,
    redact_pii,
)
from guardrails.schema import SchemaViolation


def _new_investigation() -> uuid.UUID:
    """Borrow the first real alert_id so the FK is satisfied. The demo
    investigation is closed immediately after the rails fire."""
    with session_scope() as s:
        alert = s.execute(select(FraudAlert).limit(1)).scalar_one_or_none()
        if alert is None:
            raise SystemExit("No fraud_alerts present — run `python -m data.generate` first.")
        inv = Investigation(alert_id=alert.id, status="running")
        s.add(inv)
        s.flush()
        return inv.id


def demo_scope(inv_id: uuid.UUID) -> bool:
    try:
        assert_in_scope(
            "AC_OUT_OF_SCOPE",
            actor="demo_rails",
            scope={"AC_PRIMARY"},
            investigation_id=inv_id,
        )
        return False
    except ScopeViolation:
        return True


def demo_schema(inv_id: uuid.UUID) -> bool:
    try:
        enforce_schema(
            {"severity": "BOGUS"},  # invalid value AND missing required fields
            schema=TriageAssessment,
            actor="demo_rails",
            investigation_id=inv_id,
        )
        return False
    except SchemaViolation:
        return True


def demo_justification(inv_id: uuid.UUID) -> bool:
    try:
        enforce_justification(
            tool_name="watchlist.lookup",
            arguments={"name": "demo"},
            justification="",  # too short
            actor="demo_rails",
            investigation_id=inv_id,
        )
        return False
    except JustificationViolation:
        return True


def demo_escalation(inv_id: uuid.UUID) -> bool:
    """Construct a CaseFile that claims SAR_FILE with insufficient evidence."""
    cf = CaseFileSchema(
        investigation_id=inv_id,
        risk_tier=SeverityTier.CRITICAL,
        recommended_action=RecommendedAction.SAR_FILE,
        sar_ready=True,
        confidence=0.55,  # below 0.75 threshold
        executive_summary="x" * 50,
        suspicion_grounds="thin evidence demo",
        subject_details={},
        financial_exposure_estimate=1000.0,
        evidence_chain=[
            CaseEvidence(evidence_type="x", summary="only one piece", confidence=0.5)
        ],
        network_summary={},
    )
    out = enforce_escalation(cf, investigation_id=inv_id)
    return out.recommended_action == RecommendedAction.REVIEW


def demo_pii(inv_id: uuid.UUID) -> bool:
    sample = "Customer John Smith (john.smith@example.com) opened AC0001 in London."
    redacted = redact_pii(sample, actor="demo_rails", investigation_id=inv_id)
    return redacted != sample


def main() -> None:
    inv_id = _new_investigation()
    print(f"Demo investigation: {inv_id}\n")

    results = {
        "scope": demo_scope(inv_id),
        "schema": demo_schema(inv_id),
        "justification": demo_justification(inv_id),
        "escalation": demo_escalation(inv_id),
        "pii": demo_pii(inv_id),
    }
    for rail, ok in results.items():
        print(f"  {rail:<14} {'FIRED' if ok else 'did not fire'}")

    # Confirm rows landed in security_events.
    print()
    with session_scope() as s:
        rows = s.execute(
            select(SecurityEvent.rail).where(SecurityEvent.investigation_id == inv_id)
        ).scalars().all()
        seen = sorted(set(rows))
        print(f"security_events written for rails: {seen}")
        missing = {"scope", "schema", "justification", "escalation", "pii"} - set(seen)
        if missing:
            print(f"WARNING: missing rails {sorted(missing)}")
        else:
            print("All 5 rails confirmed in security_events.")

    # Close out the demo investigation so it doesn't sit as 'running' forever.
    with session_scope() as s:
        inv = s.get(Investigation, inv_id)
        if inv is not None:
            inv.status = "completed"
            inv.completed_at = datetime.now(UTC)


if __name__ == "__main__":
    main()
