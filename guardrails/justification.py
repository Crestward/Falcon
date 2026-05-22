"""Justification rail — every tool call payload must carry a justification.

`JustifiedToolCall` (in core/schemas.py) enforces `min_length=10` at
construction time. This module logs *attempts* to call tools without a
justification, before the Pydantic error propagates.
"""
from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError

from core.schemas import JustifiedToolCall
from guardrails.audit import write_security_event


class JustificationViolation(Exception):
    """Raised when a tool call lacks an adequate justification."""


def enforce_justification(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    justification: str,
    actor: str,
    investigation_id: uuid.UUID | None = None,
) -> JustifiedToolCall:
    """Construct (and thereby validate) a JustifiedToolCall, logging
    violations to security_events. The returned object can be passed to
    the dispatch layer."""
    try:
        return JustifiedToolCall(
            tool_name=tool_name,
            arguments=arguments,
            justification=justification,
        )
    except ValidationError as e:
        write_security_event(
            rail="justification",
            actor=actor,
            severity="error",
            detail=(
                f"Tool call rejected — justification missing or too short. "
                f"tool={tool_name!r}"
            ),
            investigation_id=investigation_id,
            payload={"justification_len": len(justification or ""), "tool": tool_name},
        )
        raise JustificationViolation(str(e)) from e
