"""Shared LLM helpers: JSON-mode call with schema-validated retry.

This is the implementation of the schema rail at the agent boundary (plan §2.3
preview). On Ollama 8B models, JSON-mode produces *syntactically* valid JSON
but the *shape* drifts. We give the model an EXAMPLE instance (not the raw
schema — small models echo schemas back as their answer) plus a field
listing, and we retry once with a stricter reprompt on validation failure.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any, TypeVar
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo

from agents.preamble import FALCON_PREAMBLE
from core.db import session_scope
from core.llm_factory import Role, get_llm
from core.models import AgentTrace
from core.settings import get_settings

# Setting FALCON_CACHE_DEBUG=1 prints cache hit/miss stats from each LLM
# response. Useful for verifying prompt caching is engaging end-to-end.
_CACHE_DEBUG = os.getenv("FALCON_CACHE_DEBUG", "").lower() in {"1", "true", "yes"}

T = TypeVar("T", bound=BaseModel)


def _strip_to_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def _placeholder_for_annotation(annotation: Any) -> Any:
    """Produce a minimal valid placeholder for a type annotation."""
    origin = getattr(annotation, "__origin__", None)
    args = getattr(annotation, "__args__", ())

    if annotation in (str, "str"):
        return "..."
    if annotation in (int,):
        return 0
    if annotation in (float,):
        return 0.0
    if annotation in (bool,):
        return False
    if annotation is UUID or annotation == UUID:
        return "00000000-0000-0000-0000-000000000000"
    if annotation is datetime:
        return datetime.now(UTC).isoformat()
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return next(iter(annotation)).value
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _example_for_model(annotation)

    if origin in (list, set, tuple):
        inner = args[0] if args else str
        return [_placeholder_for_annotation(inner)]
    if origin is dict:
        return {"key": "value"}

    # Union / Optional
    if args:
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            return _placeholder_for_annotation(non_none[0])

    return None


def _example_for_model(model: type[BaseModel]) -> dict[str, Any]:
    example: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        assert isinstance(field, FieldInfo)
        if field.default_factory is not None or field.default is not None:
            # Optional / has default — still include so the LLM sees it.
            pass
        example[name] = _placeholder_for_annotation(field.annotation)
    return example


def _field_guide(model: type[BaseModel]) -> str:
    lines = []
    for name, field in model.model_fields.items():
        type_repr = str(field.annotation).replace("typing.", "")
        required = "REQUIRED" if field.is_required() else "optional"
        lines.append(f"  - {name} ({type_repr}, {required})")
    return "\n".join(lines)


def _persist_trace(
    *,
    investigation_id: uuid.UUID | None,
    agent_name: str | None,
    step: int,
    reasoning_text: str,
    token_count: int | None,
    latency_ms: int,
) -> None:
    """Phase 3.5 — store per-call reasoning for `GET /investigations/{id}/traces`.

    No-op when invoked outside an investigation (unit tests, ad-hoc calls). DB
    errors are swallowed: tracing must never crash an investigation in flight.
    """
    if investigation_id is None or agent_name is None:
        return
    try:
        with session_scope() as s:
            s.add(
                AgentTrace(
                    investigation_id=investigation_id,
                    agent_name=agent_name,
                    step=step,
                    # 32k cap — the Case Writer's full JSON case file commonly
                    # exceeds 8k once the evidence chain + network nodes are
                    # included; truncating mid-JSON breaks downstream pretty
                    # rendering. 32k still bounds rogue runaway outputs.
                    reasoning_text=reasoning_text[:32000],
                    token_count=token_count,
                    latency_ms=latency_ms,
                )
            )
    except Exception:  # noqa: BLE001
        pass


def call_structured(
    role: Role,
    *,
    system_prompt: str,
    user_prompt: str,
    schema: type[T],
    max_retries: int = 1,
    investigation_id: uuid.UUID | None = None,
    agent_name: str | None = None,
) -> T:
    """Ask `role` for JSON, validate against `schema`. Retry once on validation failure."""
    llm = get_llm(role)
    example = json.dumps(_example_for_model(schema), indent=2, default=str)
    field_guide = _field_guide(schema)

    base_system = (
        system_prompt
        + "\n\n----- OUTPUT FORMAT -----\n"
        + "You MUST respond with a single JSON object — an INSTANCE of the schema,\n"
        + "NOT the schema itself. No prose, no markdown fences, no commentary.\n\n"
        + f"Required fields:\n{field_guide}\n\n"
        + "EXAMPLE shape (replace placeholder values with your actual answer):\n"
        + example
    )

    # Prompt caching: on Anthropic-direct, structure the system message as
    # TWO cached blocks:
    #   1. FALCON_PREAMBLE — shared across every role and call (≥2048 tokens,
    #      Haiku 4.5's minimum cachable size). One cache slot serves the
    #      whole system. Pure savings after the first call anywhere.
    #   2. base_system — role-specific (role prompt + field guide + example).
    #      Identical across calls of the same role; a second cache_control
    #      marker makes the prefix (preamble + role block) a longer cache
    #      hit for subsequent same-role calls.
    # The HumanMessage (per-alert state) is NOT cached — it changes every
    # call. Other backends get a plain concatenated string and ignore both
    # markers.
    backend = get_settings().llm_backend
    if backend == "anthropic":
        system_content: Any = [
            {
                "type": "text",
                "text": FALCON_PREAMBLE,
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": base_system,
                "cache_control": {"type": "ephemeral"},
            },
        ]
    else:
        system_content = FALCON_PREAMBLE + "\n\n" + base_system

    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        msgs = [SystemMessage(content=system_content), HumanMessage(content=user_prompt)]
        if attempt > 0 and last_err is not None:
            msgs.append(
                HumanMessage(
                    content=(
                        f"Your previous response failed validation with this error:\n{last_err}\n\n"
                        "Return ONLY a JSON object — an INSTANCE — using the example shape above. "
                        "Do NOT return the schema definition. Do NOT include '$defs', "
                        "'properties', or 'type' keys at the top level."
                    )
                )
            )
        started = time.perf_counter()
        response = llm.invoke(msgs)
        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = (response.response_metadata or {}).get("usage", {}) or {}
        if _CACHE_DEBUG and backend == "anthropic":
            print(
                f"  [cache role={role} input={usage.get('input_tokens', '?')} "
                f"cache_create={usage.get('cache_creation_input_tokens', '?')} "
                f"cache_read={usage.get('cache_read_input_tokens', '?')} "
                f"output={usage.get('output_tokens', '?')}]"
            )
        raw = response.content
        if isinstance(raw, list):
            raw = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in raw)
        token_total: int | None = None
        if usage:
            try:
                token_total = int(usage.get("input_tokens", 0) or 0) + int(
                    usage.get("output_tokens", 0) or 0
                )
            except (TypeError, ValueError):
                token_total = None
        _persist_trace(
            investigation_id=investigation_id,
            agent_name=agent_name or role,
            step=attempt,
            reasoning_text=str(raw),
            token_count=token_total,
            latency_ms=latency_ms,
        )
        try:
            payload = json.loads(_strip_to_json(raw))
            return schema.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = e
            continue

    raise RuntimeError(f"LLM failed schema validation after {max_retries + 1} attempts: {last_err}")
