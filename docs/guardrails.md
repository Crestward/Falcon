# Custom guardrails

FALCON has five guardrails. None of them are framework magic — each one
is a small piece of Python code in `guardrails/` that you can read in a
minute. The point of writing them ourselves is that every line is
auditable, every violation is logged, and every behaviour has a clear
reason for existing.

When a rail trips, it writes a row to `security_events` with the actor,
the payload that triggered it, and the rail name. That table is the
single source of truth a compliance officer (or an interviewer) can
query to see what the system has been protecting against.

## The five rails

### Scope — Network Mapper can't snoop

Network Mapper can only query accounts inside the investigation's
discovered scope (`accounts_investigated ∪ network_graph.nodes`). An
agent that "decided" to look up a random account fails the pre-call
check and the attempt lands in `security_events`. This is the rail
that prevents an LLM with tool access from quietly fishing through
unrelated customer data.

Implementation: `@enforce_scope` decorator on tool calls + per-call
`assert_in_scope` for ad-hoc checks.

### Schema — every message validates

Every inter-agent message is a Pydantic model with
`extra="forbid"`. If a model produces JSON that doesn't match, the
schema rail rejects it and triggers a stricter retry. On a local 8B
model this fires often; on Haiku/Sonnet it almost never does. The
rail is what makes the local-first strategy viable.

The validator also doubles as documentation: the schema files in
`core/schemas.py` are the contract between agents.

### Justification — no silent tool calls

Every tool call carries a `justification` string of at least 10
characters. Empty or missing justifications fail validation before
the tool runs, and the failure is logged. This is the rail that
makes the audit log readable: every entry in `tool_call_logs` says
not just *what* the agent did but *why it claimed to be doing it*.

A real attacker who got prompt-injection access would have to also
fabricate a plausible justification, which makes them visible.

### Escalation — Case Writer can't fabricate a SAR

The hardest constraint in the system. Case Writer's prompt can ask
for `recommended_action == "SAR_FILE"`, but `enforce_escalation`
will downgrade it to `REVIEW` unless there are at least three
evidence items and confidence is above 0.75. The LLM physically
cannot bypass it — it's a function call after the LLM has spoken.

This is the rail that turns the system from "an LLM that suggests
verdicts" into "a system that refuses to file a SAR without
evidence."

### PII — Presidio scrubs outputs

Microsoft Presidio analyses agent outputs before they hit the case
file payload or the logs. Names, emails, account numbers, addresses
get redacted to placeholders. The synthetic data is fake but the
patterns are real, so the rail demonstrates the data-protection
hook a regulated deploy would require.

PII detection is the only rail that's allowed to fail soft — if
Presidio crashes for any reason, the rail logs and returns the
original payload rather than blocking the investigation. The
trade-off is documented in `guardrails/pii.py`.

## Why not NeMo Guardrails

NeMo Guardrails ships a DSL (Colang) for defining rails. It's powerful
but it's the wrong shape for what we need.

Four of our five rails are synchronous validators on structured data.
They run before or after specific function calls, they have nothing
to do with LLM topical filtering, and they're already as small as
they can be — a NeMo rail wrapping them would be more code, not less.

The fifth rail (PII) is closer to NeMo's wheelhouse, but Presidio is
the actual PII engine inside NeMo too, so we'd just be adding an
abstraction over a thing we want to call directly.

The full trade-off lives in [ADR-005](adr/0005-custom-guardrails.md).

## What you can verify

`tests/test_phase2_guardrails.py` has thirteen tests — one per rail,
plus failure-mode cases. `scripts/demo_rails.py` is the deliverable
script: it triggers each rail with a deliberately-broken input and
confirms the row lands in `security_events`. The smoke run output
listed every rail by name.

In production: the `security_events` table is queryable from the
dashboard's metrics view (next iteration) and the Phase 4 `/metrics`
endpoint exposes per-rail counts.
