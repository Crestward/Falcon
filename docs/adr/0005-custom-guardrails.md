# ADR-005 — Custom guardrails over NeMo Guardrails

**Status:** Accepted · 2026-05-08

## Context

FALCON needs five specific rails: scope, schema, justification,
escalation, PII. The obvious framework choice is NeMo Guardrails —
it ships a DSL (Colang) for defining LLM rails and an evaluation
runtime. The alternative is to write each rail as plain Python in
`guardrails/`.

## Decision

Plain Python in `guardrails/`. No NeMo, no Colang.

## Why

Four of the five rails are synchronous validators on structured
data. They run before or after a specific function call and have
nothing to do with topical LLM filtering — which is what NeMo's
runtime is good at. Wrapping a Pydantic validation call inside a
Colang rail would be more code, not less.

The fifth rail (PII) uses Microsoft Presidio, which is the same
PII engine NeMo wraps internally. We can call it directly without
adding the framework over the top.

The deeper reason is auditability. Each rail is twenty to fifty
lines of Python you can read in an interview. NeMo's runtime is
another layer to defend ("why is this rail in Colang and not Python?").
The bank's risk function would rather review explicit code than a
DSL config.

## Consequences

- All five rails live under `guardrails/` and log violations to a
  shared `security_events` table via `write_security_event`.
- Each rail has its own unit test file under `tests/`, and
  `scripts/demo_rails.py` triggers all five in a single run.
- If we needed an LLM-mediated topical filter later (e.g. "block
  the agent from discussing a customer's medical history"), that's
  the case where NeMo would actually pay off, and we'd add it as a
  sixth rail rather than retrofitting the others.
