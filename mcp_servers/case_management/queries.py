"""case-management-mcp queries — write & read case files and decisions."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from core.db import session_scope
from core.models import AgentDecision, CaseFile, Investigation


def persist_case_file(
    investigation_id: uuid.UUID,
    risk_tier: str,
    recommended_action: str,
    sar_ready: bool,
    confidence: float,
    case_json: dict[str, Any],
) -> uuid.UUID:
    with session_scope() as s:
        cf = CaseFile(
            investigation_id=investigation_id,
            risk_tier=risk_tier,
            recommended_action=recommended_action,
            sar_ready=sar_ready,
            confidence=confidence,
            case_json=case_json,
        )
        s.add(cf)
        inv = s.get(Investigation, investigation_id)
        if inv is not None:
            inv.status = "completed"
            inv.completed_at = datetime.now(UTC)
            inv.confidence_score = confidence
        s.flush()
        return cf.id


def record_decision(
    investigation_id: uuid.UUID,
    agent_name: str,
    decision_type: str,
    decision_payload: dict[str, Any],
    justification: str,
    confidence: float | None = None,
) -> int:
    with session_scope() as s:
        d = AgentDecision(
            investigation_id=investigation_id,
            agent_name=agent_name,
            decision_type=decision_type,
            decision_payload=decision_payload,
            justification=justification,
            confidence=confidence,
        )
        s.add(d)
        s.flush()
        return d.id


def get_case(investigation_id: uuid.UUID) -> dict[str, Any] | None:
    with session_scope() as s:
        cf = s.execute(
            select(CaseFile).where(CaseFile.investigation_id == investigation_id)
        ).scalar_one_or_none()
        if cf is None:
            return None
        return {
            "id": str(cf.id),
            "investigation_id": str(cf.investigation_id),
            "risk_tier": cf.risk_tier,
            "recommended_action": cf.recommended_action,
            "sar_ready": cf.sar_ready,
            "confidence": float(cf.confidence),
            "case_json": cf.case_json,
            "created_at": cf.created_at.isoformat(),
        }
