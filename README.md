# FALCON

**F**raud **A**utonomous **L**ong-horizon **C**ase **O**fficer **N**etwork

A multi-agent fraud investigation system that turns raw bank alerts
into structured case files. A LangGraph supervisor orchestrates five
specialist agents;Triage, Account Historian, Network Mapper, Pattern
Hunter, Case Writer, over a Postgres + pgvector evidence store, with
custom guardrails enforcing scoped tool access, justified calls, and
Presidio-based PII redaction.

---

## The problem

Banks spend hours per fraud alert on manual investigation. An analyst
pulls transaction history, checks the network of linked accounts,
looks for known patterns, writes up the case. Most of that work is
deterministic and most of it is repetitive, but the deterministic
parts produce too many false positives, so a human still has to
read every case. FALCON automates the gathering and the reasoning,
and pauses for a human only when the verdict is genuinely uncertain.

## What FALCON does

When an alert fires, FALCON runs a full investigation end-to-end:

1. **Triage** sizes the response from a seven-day quick scan.
2. **Account Historian** runs the 90-day behavioural baseline,
   flags anomalies, and runs pgvector semantic search against
   known fraud-pattern descriptions.
3. **Network Mapper** traverses the account network — shared
   devices, shared IPs, beneficial owners, transaction flows — and
   decides whether to expand the investigation.
4. **Pattern Hunter** runs five typology detectors (structuring,
   layering, account takeover, mule network, PEP exposure) and
   classifies the case.
5. **Case Writer** composes a SAR-shaped case file with an
   evidence chain, suspicion grounds, and a recommended action.

The path through those agents isn't fixed. A LangGraph supervisor
decides at runtime whether to expand the network, whether to pause
for human review, and which verdict to recommend — three-tier
routing on a combined confidence score (auto-close / human review /
file a SAR).

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│  Dashboard (vanilla HTML)  ←──── FastAPI ────→  Postgres+pgvector  │
│                                                       ▲            │
│                                                       │            │
│                            LangGraph supervisor       │            │
│                                  │                    │            │
│        ┌─────────┬────────┬──────┴───────┬─────────┐  │            │
│        ▼         ▼        ▼              ▼         ▼  │            │
│     triage  historian  mapper      pattern_hunter  case_writer     │
│        │         │        │              │         │               │
│        ▼         ▼        ▼              ▼         ▼               │
│      ┌────────────────────────────────────────────┐                │
│      │  4 FastMCP tool servers (HTTP)             │                │
│      │  transaction_store · network_graph         │                │
│      │  watchlist · case_management               │                │
│      └────────────────────────────────────────────┘                │
│                          │                                         │
│                          ▼                                         │
│                       Postgres                                     │
└────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
                  OpenTelemetry → Jaeger
                  Prompt cache → Anthropic
                  Evaluation runs → eval/results/*.json
```

Every tool call is justified, scope-checked, and logged. Every agent
boundary is Pydantic-validated. Every investigation can pause for
human review and resume hours later from a Postgres checkpoint.

## The dashboard demo

The landing page at `http://localhost:8000` shows a typology picker
(structuring, layering, account takeover, mule network, PEP exposure).
Clicking a card opens a brief about that pattern; pressing **Start**
animates a real investigation end-to-end — the supervisor's event
timeline, each agent's structured output, and the final SAR draft.

The runs you see are **replays of real investigations** captured ahead
of time and stored in `demo_cache/*.json` so the public demo doesn't
make live API calls. To run one yourself against your own API key,
clone the repo, fill in `.env`, bring up the stack, and trigger
`POST /investigations` against any alert — the full flow runs live in
~15–30 seconds.

## Evaluation results

From a headline run on Haiku 4.5 agents with Opus 4.7 as judge,
all 30 synthetic alerts:

| Metric | Value |
|---|---|
| Verdict accuracy | **24%** |
| Typology accuracy | **52%** |
| Network recall (mean) | **95%** |
| Faithfulness (mean) | **77%** |
| Hallucination rate (mean) | **12%** |

Pattern Hunter and Network Mapper are both performing well; the
verdict number is dragged down by a known tuning gap in the
confidence formula — most genuine alerts land in the human-review
band rather than crossing the SAR threshold, and the case writer
clamps to within 0.15 of that interim confidence. The numbers are
the honest, untuned baseline; the **Tuning knobs** section below
tells you what to change to lift them.

Full methodology: [docs/evaluation.md](docs/evaluation.md).
Dashboard: `eval/results/index.html` (rendered by
`python -m eval.dashboard`).

## Technical deep dives

- [How the dynamic supervisor works](docs/supervisor.md)
- [The contradiction detection system](docs/contradiction.md)
- [Custom guardrails — what each rail protects and why](docs/guardrails.md)
- [Evaluation methodology](docs/evaluation.md)
- [Red team report — five attacks on the system](docs/security/red_team_report.md)

## Running locally

```bash
# 1. Bring the stack up (Postgres + Jaeger + 4 MCP servers + API)
docker compose --profile local up --build

# 2. Seed the database
python -m data.generate
python -m data.embed_patterns

# 3. Open the dashboard
# http://localhost:8000
```

Three model backends are supported via `LLM_BACKEND` in `.env`:

- `ollama` — local Qwen3 / Llama 3.1, no API keys needed
- `anthropic` — direct Claude API
- `vertex` — Gemini in `europe-west2` for UK data residency

## Tuning knobs

The current verdict accuracy and FPR are sensitive to a small handful
of numbers. They're deliberately conservative in this repo so the
honest baseline is the one you see. If you want to push them, edit
these and re-run the eval (`python -m eval.run_evaluation`):

| Knob | File | Default | What lowering / raising it does |
|---|---|---|---|
| `CONF_AUTO_CLOSE_BELOW` | `supervisor/config.py` | `0.25` | Lower → more clean alerts auto-close, lower FPR. Raise → more cases pause for HITL. |
| `CONF_SAR_ABOVE` | `supervisor/config.py` | `0.75` | Lower → more genuine cases land directly as SAR_FILE, higher verdict accuracy. Raise → almost everything routes through HITL. |
| Case Writer confidence clamp | `agents/case_writer.py` SYSTEM_PROMPT | "do not deviate by more than 0.15" | Loosen the deviation cap (or remove the rule) so the LLM can move the verdict further from interim confidence. |
| `compute_confidence` weights | `supervisor/confidence.py` | typology gets `+0.25 × score` | Increase the typology weight so a strong Pattern Hunter signal pulls confidence above 0.75. |
| Auto-resume in eval | `eval/run_evaluation.py:_run_one` | submits a neutral REVIEW annotation | Switch to recording HITL pauses as a separate verdict bucket; clean alerts that pause stop counting as false positives. |

None of these change the framework — they change where the supervisor
decides cases sit. Treat the defaults as the starting point.


## Repo structure

```
falcon/
├── agents/           Triage, Historian, Mapper, Pattern Hunter, Case Writer, Reconciler
├── supervisor/       LangGraph graph, state, confidence, checkpointer
├── mcp_servers/      Four FastMCP tool servers
├── guardrails/       Scope, schema, justification, escalation, PII rails
├── eval/             Ground truth, scorer, LLM-as-judge, dashboard renderer
├── api/              FastAPI app + single-file HTML dashboard
├── data/             Synthetic data generation
├── demo_cache/       Pre-recorded investigation runs served by the dashboard
├── docs/             Deep dives, ADRs, red team report
└── tests/            88 unit tests across phases 0–4
```

