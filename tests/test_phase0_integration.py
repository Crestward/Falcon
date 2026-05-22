"""Phase 0 integration smoke tests — REQUIRES the docker stack running.

The two tests called out in the Phase 0 deliverable:
  (a) insert a synthetic alert, query it back from Postgres
  (b) round-trip a structured JSON response through the LLM factory (local Qwen3 4B)

Run:
  docker compose --profile local up -d
  python -m data.generate            # one-time
  pytest -m integration tests/test_phase0_integration.py
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.integration


def test_alert_round_trip(db_session) -> None:
    """Phase 0 deliverable (a): write an alert, read it back."""
    from core.models import Account, FraudAlert

    # Pick any seeded account (data.generate must have run).
    account = db_session.execute(select(Account).limit(1)).scalar_one_or_none()
    assert account is not None, "Seed the DB first: python -m data.generate"

    alert_id = "ALERT-PHASE0-SMOKE"
    db_session.execute(
        FraudAlert.__table__.delete().where(FraudAlert.id == alert_id)
    )
    db_session.add(FraudAlert(
        id=alert_id,
        account_id=account.id,
        alert_type="STRUCTURING",
        initial_score=Decimal("0.812"),
        raised_at=datetime.now(UTC) - timedelta(hours=2),
        status="open",
        metadata_json={"source": "phase0_smoke"},
    ))
    db_session.commit()

    fetched = db_session.execute(
        select(FraudAlert).where(FraudAlert.id == alert_id)
    ).scalar_one()
    assert fetched.account_id == account.id
    assert fetched.alert_type == "STRUCTURING"
    assert float(fetched.initial_score) == pytest.approx(0.812)


def test_llm_factory_json_roundtrip() -> None:
    """Phase 0 deliverable (b): round-trip structured JSON through local Qwen3 4B."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from core.llm_factory import get_llm
    from core.schemas import TriageAssessment

    llm = get_llm("triage")
    schema_str = json.dumps(TriageAssessment.model_json_schema(), indent=2)

    messages = [
        SystemMessage(content=(
            "You are a fraud triage agent. Respond ONLY with JSON matching this schema:\n"
            f"{schema_str}\n"
            "Required fields: severity (LOW|MEDIUM|HIGH|CRITICAL), "
            "recommended_depth (SHALLOW|DEEP|FULL), initial_hypothesis (10-2000 chars), "
            "quick_signals (object), justification (10-2000 chars)."
        )),
        HumanMessage(content=(
            "Alert: account AC00012345 received 5 credits between £9,400 and £9,800 in 36 hours, "
            "from 5 distinct counterparties. Account opened 28 days ago. KYC tier 1. "
            "Produce a triage assessment."
        )),
    ]
    response = llm.invoke(messages)
    payload = json.loads(response.content)
    parsed = TriageAssessment.model_validate(payload)
    assert parsed.severity.value in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert parsed.recommended_depth.value in {"SHALLOW", "DEEP", "FULL"}
    assert len(parsed.justification) >= 10
