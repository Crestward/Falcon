# ADR-001 — LangGraph over CrewAI for the supervisor

**Status:** Accepted · 2026-04-30

## Context

FALCON needs a supervisor that decides which agent runs next based on
what previous agents found. That decision happens at runtime, not in
advance. We compared three options before starting Phase 1.

- **CrewAI** — opinionated agent crew framework. Agents are
  configured with roles and goals; the framework decides routing.
- **LangGraph** — explicit graph with typed state. Routing is code
  you write; the framework gives you persistence, checkpointing, and
  human-in-the-loop primitives.
- **Hand-rolled state machine** — Python class with `step()`,
  serialise/deserialise to JSON, custom interrupt logic.

## Decision

LangGraph.

## Why

CrewAI's selling point — "you describe the crew and it figures out
the orchestration" — is the wrong shape for a fraud system. The
routing logic *is* the differentiator. Hiding it inside a framework
that decides who speaks next based on goal strings would be the
opposite of what we want to show off in an interview.

Hand-rolling would have meant rebuilding two non-trivial things:
typed state with a reducer (so partial updates merge correctly), and
a persistent checkpointer that survives process restarts. LangGraph
ships both, with a Postgres backend, and the `interrupt`/`Command(resume=)`
primitive that makes the HITL pause clean. That's a month of work
we don't have to do.

## Consequences

- Routing decisions live in `supervisor/graph.py` as conditional
  edges. They read from typed state and are unit-testable without a
  database.
- We're locked into LangGraph's checkpoint format. Migration to a
  different orchestrator would mean re-serialising state, but the
  agents themselves are framework-agnostic Python functions.
- LangGraph's msgpack deserialisation emits warnings for our custom
  Pydantic types. Cosmetic; fix is to register the types in
  `allowed_msgpack_modules` on the checkpointer when convenient.
