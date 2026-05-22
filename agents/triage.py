"""Triage Agent — the cheap fast pass. 7-day signals only.

Lightweight by design: this agent decides *how deep* to investigate, not what
the answer is. Heavy lifting happens in Account Historian. See plan §1.1.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from agents.llm_utils import call_structured
from agents.tools import call_tool
from core.db import session_scope
from core.models import Account, FraudAlert
from core.schemas import TriageAssessment
from mcp_servers.case_management import queries as case_q


def _load_alert_context(alert_id: str) -> dict[str, Any]:
    with session_scope() as s:
        alert = s.execute(
            select(FraudAlert).where(FraudAlert.id == alert_id)
        ).scalar_one()
        account = s.get(Account, alert.account_id)
        return {
            "alert_id": alert.id,
            "alert_type": alert.alert_type,
            "initial_score": float(alert.initial_score),
            "raised_at": alert.raised_at.isoformat(),
            "alert_metadata": dict(alert.metadata_json or {}),
            "account_id": alert.account_id,
            "account_type": account.account_type if account else None,
            "kyc_tier": account.kyc_tier if account else None,
            "country": account.country if account else None,
            "open_date": account.open_date.isoformat() if account else None,
        }


SYSTEM_PROMPT = """You are the Triage Agent in a multi-agent fraud investigation system.

Your job: assign a severity tier (LOW/MEDIUM/HIGH/CRITICAL) and recommended
investigation depth (SHALLOW/DEEP/FULL) based on alert metadata and a 7-day
quick signal scan.

You do NOT perform the full investigation — Account Historian does that with
the 90-day baseline. Your job is to size the response, not solve the case.

Guidelines:
- CRITICAL severity only for clear high-magnitude indicators (very large
  amounts, sanctioned counterparties, rule score >= 0.9).
- SHALLOW depth = brief review only; reserve for low-risk plausible-false-positive cases.
- FULL depth = full network expansion is justified.
- The `justification` field must reference the specific signals you used.
"""


def triage(alert_id: str, investigation_id: uuid.UUID) -> TriageAssessment:
    context = _load_alert_context(alert_id)
    account_id = context["account_id"]

    signals = call_tool(
        investigation_id=investigation_id,
        agent_name="triage",
        tool_name="transaction_store.get_quick_signals",
        arguments={"account_id": account_id, "days": 7},
        justification="Triage requires 7-day activity profile to size the investigation",
    )

    user_prompt = (
        "ALERT CONTEXT:\n"
        f"{context}\n\n"
        "QUICK SIGNALS (last 7 days):\n"
        f"{signals}\n\n"
        "Produce a TriageAssessment."
    )

    assessment = call_structured(
        role="triage",
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=TriageAssessment,
        investigation_id=investigation_id,
        agent_name="triage",
    )
    # Triage is responsible for stamping its own quick signals into the
    # assessment so downstream agents see what it actually looked at.
    if not assessment.quick_signals:
        assessment = assessment.model_copy(update={"quick_signals": signals})

    case_q.record_decision(
        investigation_id=investigation_id,
        agent_name="triage",
        decision_type="TRIAGE_ASSESSMENT",
        decision_payload=assessment.model_dump(mode="json"),
        justification=assessment.justification,
    )
    return assessment
