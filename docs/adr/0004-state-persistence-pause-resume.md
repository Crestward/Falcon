# ADR-004 — Investigation state persistence enables pause-and-resume

**Status:** Accepted · 2026-05-05

## Context

Fraud cases routinely pause for human review — sometimes for hours,
sometimes overnight. The system has to be able to suspend a running
investigation, return control to the caller, and pick up later
without losing any of the agent state.

## Decision

LangGraph's Postgres checkpointer is the persistence layer. State is
serialised at every supervisor node boundary. The HITL pause uses
LangGraph's `interrupt(...)` primitive; resume is
`graph.invoke(Command(resume=annotation))` with the same thread id.

## Why

LangGraph's checkpointer is the only existing system that handles
the hard parts: typed-state serialisation, deterministic replay from
a checkpoint, and the interrupt/resume contract. Building this
ourselves was the option we explicitly rejected in ADR-001.

Postgres as the checkpoint backend keeps everything in one place.
The checkpoint tables (`checkpoints`, `checkpoint_writes`,
`checkpoint_blobs`) live alongside the investigation tables; a
single backup captures both.

## Consequences

- Every investigation has a thread id (its UUID) that the
  checkpointer keys off. The supervisor configures it once in
  `_thread_config` and never has to touch it again.
- A paused case sits in the database forever until resumed. The
  dashboard's `/investigations?status=paused_hitl` view is the
  reviewer's inbox.
- Process crashes are recoverable. Kill the supervisor mid-run,
  restart, call `resume`, and the case continues from the last
  checkpoint. Demonstrated in `scripts/demo_hitl.py`.
- The msgpack serialisation produces benign warnings for our
  Pydantic types because LangGraph doesn't auto-register custom
  classes. Deserialisation still works; the fix is to register
  types in `allowed_msgpack_modules` on the checkpointer.
