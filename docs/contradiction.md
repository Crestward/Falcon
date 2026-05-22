# Contradiction detection

Most multi-agent systems quietly average over conflicting signals.
FALCON flags the conflict, penalises confidence, and forces the case
writer to address it on the page.

## The two places conflicts surface

There are two distinct conflict types and they're handled differently.

**Triage vs Pattern Hunter — the hypothesis conflict.** Triage runs
first with only seven days of data; its `initial_hypothesis` is an
educated guess, not a verdict. Later, after the full historian and
network passes, Pattern Hunter classifies the typology from
deterministic detectors. If those two disagree — Triage thought
*account takeover*, Hunter found *structuring* — the supervisor
invokes a small reconciler call. The reconciler sees both agents'
evidence and produces a single reconciled `TypologyAssessment`. The
event is logged as `RECONCILIATION` so it's reviewable later.

The reconciler is *not* Triage replayed. Triage was designed to look
at alert metadata; running it again with full investigation state
would change what the agent does, which is worse than asking a
separate small model to arbitrate.

**Behaviour vs typology — the evidence conflict.** This is the more
interesting case and it lives at `CHECKPOINT_4` before the routing
decision. `detect_contradictions` compares Pattern Hunter's
classification against Account Historian and Network Mapper outputs:

- Pattern Hunter says *structuring*, but Account Historian's
  90-day baseline shows consistent legitimate business deposits at
  similar amounts? Contradiction.
- Pattern Hunter says *account takeover*, but Network Mapper found
  the same device fingerprint active for two years? Contradiction.

Each detection lowers confidence by 0.10, capped at a 0.30 total
penalty. The penalty is what changes the routing — a borderline
SAR case with a contradiction drops back into the HITL band where
a human reviewer can break the tie.

## What the case writer does with it

The case writer's prompt explicitly receives the contradiction list
and is told: *for each contradiction, the `contradictions_addressed`
field must include a one-line note explaining how this case file
accounts for it.* That field then renders in the dashboard's case
file panel and in the SAR-shaped output.

The point isn't to be clever. It's that ignoring conflicting evidence
in a fraud case is the failure mode that gets banks fined. The system
shouldn't produce a confident verdict while pretending the conflict
didn't exist.

## Why a small LLM call instead of pure rules

The reconciler is an LLM, the behaviour-vs-typology detector is rules.
That split is deliberate.

Behaviour vs typology has clear, narrow rules — *if structuring is
flagged but baseline shows consistent merchant deposits, that's a
contradiction*. We can write that as code and we should, because
deterministic checks are faster and don't drift.

Triage vs Hunter is messier. The hypothesis from triage is free text
("account behaviour suggests possible takeover from a new device").
Mapping that to one of five typologies, weighing two pieces of
evidence, and writing a reconciliation rationale is what an LLM is
good at and what rules would be brittle at. So that piece gets a
small model call.

## What you can verify

`tests/test_phase2_contradiction.py` covers the rules-based detector
with nine scenarios — structuring with high merchant diversity, ATO
with a known device, mule network with a single shared address,
etc. `tests/test_phase2_pattern_hunter.py` covers the reconciler
seam.

In production: contradiction count per investigation is exposed in
the `/metrics` endpoint, and every contradiction lands in
`investigation_events` with the full payload for audit.
