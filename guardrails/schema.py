"""Schema rail — every inter-agent boundary validates against Pydantic.

The actual retry-on-failure loop lives in `agents/llm_utils.call_structured`;
this module exists so callers can log schema violations to the audit log
uniformly (e.g. when a validation eventually exhausts retries).
"""
from __future__ import annotations

import uuid
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from guardrails.audit import write_security_event

T = TypeVar("T", bound=BaseModel)


class SchemaViolation(Exception):
    """Raised when a payload cannot be validated against the expected schema."""


def enforce_schema(
    payload: dict,
    *,
    schema: type[T],
    actor: str,
    investigation_id: uuid.UUID | None = None,
) -> T:
    """Validate `payload` against `schema`. Log + raise on failure."""
    try:
        return schema.model_validate(payload)
    except ValidationError as e:
        write_security_event(
            rail="schema",
            actor=actor,
            severity="error",
            detail=f"Schema validation failed for {schema.__name__}: {e}",
            investigation_id=investigation_id,
            payload={"errors": e.errors()},
        )
        raise SchemaViolation(str(e)) from e
