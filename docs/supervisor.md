# How the dynamic supervisor works

FALCON doesn't run a fixed pipeline. Each investigation builds its own
execution path based on what the agents find along the way. The thing
making that decision is a LangGraph state machine called the supervisor.

## The graph

```
START → triage → account_historian → network_mapper
                                          │
                                  CHECKPOINT_3
                                ┌─────┴─────┐
                          expand            proceed
                            │                  │
                  (loop back to historian)     ▼
                                          pattern_hunter
                                              │
                                       (reconciler if
                                       triage ≠ hunter)
                                              │
                                          CHECKPOINT_4
                              ┌───────────────┼───────────────┐
                       conf<0.25       0.25≤conf≤0.75    conf>0.75
                            │               │                   │
                       auto_close      hitl_pause           case_writer
                            │               │                   │
                           END        (wait for review)         │
                                            │                   │
                                       case_writer ─────────────┤
                                            │                   │
                                          END                  END
```

Two decision points make the path dynamic:

**CHECKPOINT_3 — should we expand the network?** The Network Mapper
returns an `expansion_request` in its output. If it found three or more
linked accounts above a risk threshold, the supervisor spawns more
Account Historians for those accounts and re-enters the graph at
`network_mapper` once they finish. There's a hard cap of three
expansions per investigation; the EXPANSION_CAP_HIT event fires if a
case wants more.

**CHECKPOINT_4 — what verdict?** Three-tier routing based on the final
confidence score: below 0.25 the case auto-closes, above 0.75 it goes
straight to the case writer with a SAR recommendation, in between it
pauses for a human reviewer. The two threshold values
(`CONF_AUTO_CLOSE_BELOW`, `CONF_SAR_ABOVE`) live in
`supervisor/config.py` — see the README's *Tuning knobs* section for
why they're set where they are.

## State, not events

Routing reads from the supervisor's in-memory state object
(`InvestigationState`), never from the database. The DB writes that
happen along the way are audit-only — they let you replay an
investigation but they aren't part of the control loop.

This matters because it means the supervisor is deterministic for a
fixed state. A test can construct a state, call the router, and assert
the routing decision without spinning up Postgres.

## Pause and resume

The HITL pause is the bit that surprises people. When `hitl_pause`
fires, LangGraph's Postgres checkpointer serialises the entire state
to disk, then the supervisor blocks on `interrupt(...)`. The HTTP
request that started the investigation returns immediately with
status `paused_hitl`.

Hours or days later, `POST /investigations/{id}/annotate` calls
`graph.invoke(Command(resume=annotation))`. LangGraph reloads the
checkpoint, the `interrupt` call returns with the annotation, and the
graph continues to `case_writer` as if nothing happened.

The annotation can include an `override_action` — the Case Writer
respects it and the escalation rail validates it. That's the seam
between automated agent work and human judgement.

## Confidence scoring

`compute_confidence` combines four signals into a single 0..1 number:
triage severity, average anomaly density across account profiles,
typology match strength, and a contradiction penalty. The current
weighting is conservative — most genuine alerts land in the 0.25–0.75
HITL band rather than crossing 0.75. The formula lives in
`supervisor/confidence.py` and is the most impactful single knob in
the system; the README's *Tuning knobs* section describes how to
re-weight it.

## Why LangGraph and not a custom loop

LangGraph gives three things we'd otherwise rebuild from scratch:
typed state with a reducer, a persistent checkpointer that handles
serialisation correctly, and the `interrupt`/`Command(resume=)`
primitive that makes HITL clean. Writing all of that ourselves would
be a month of work that doesn't differentiate the system. ADR-001 has
the full comparison against CrewAI and a hand-rolled state machine.
