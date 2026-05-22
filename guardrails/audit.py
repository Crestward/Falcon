"""Shared writer for `security_events` rows.

Every guardrail uses this to log violations and near-misses uniformly.
"""
from __future__ import annotations

import uuid
from typing import Any

from core.db import session_scope
from core.models import SecurityEvent


def write_security_event(
    *,
    rail: str,
    actor: str,
    detail: str,
    severity: str = "warning",
    investigation_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Idempotent, side-effect-only. Never raises — guardrails must not
    cascade-fail the investigation when the audit log is down."""
    try:
        with session_scope() as s:
            s.add(
                SecurityEvent(
                    investigation_id=investigation_id,
                    rail=rail,
                    severity=severity,
                    actor=actor,
                    detail=detail[:2000],
                    payload=payload or {},
                )
            )
    except Exception:
        # Audit failure should not break the investigation. Swallow.
        pass
