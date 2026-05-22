# ADR-006 — Local-first LLM strategy with one env-var swap

**Status:** Accepted · 2026-04-28 · Updated 2026-05-12 (Anthropic added)

## Context

The build budget was zero API spend during development and a credible
production story at the end. Cloud-only would have burned thousands
of dollars over forty-eight days of iteration. Local-only would have
left the system tied to a single laptop's GPU.

## Decision

`LLM_BACKEND` environment variable controls which family of chat
models the agents use:

- `ollama` — local Qwen3 / Llama 3.1, free, dev loop
- `anthropic` — direct Claude API with prompt caching, the primary
  production backend
- `bedrock` — Claude via AWS, for AWS-aligned deployments
- `vertex` — Gemini via Google Cloud, for UK data residency

Every agent goes through `core/llm_factory.get_llm(role)`. No agent
ever imports `ChatOllama`, `ChatAnthropic`, etc. directly. The
factory is the single seam.

## Why

The factory pattern gives us the cheapest dev loop (Ollama, no API
cost) and the best production backend (Anthropic Haiku 4.5 with
prompt caching) without changing any agent code. Switching is one
line in `.env`.

The original plan was Ollama-dev / Bedrock-demo. Real-world testing
showed local 8B models were too slow for the iteration speed we
needed, so we added Anthropic direct as a third option mid-build.
Adding it cost ten lines in the factory because the seam already
existed. That's the payoff for picking the abstraction at the start.

Vertex was added later for the UK data-residency story (`europe-west2`).
Same pattern, same ten lines.

## Consequences

- Embeddings are **not** part of the swap. `nomic-embed-text`
  always runs locally because the column width (`vector(768)`) is
  pinned to its output and Bedrock's Titan embeddings are 1024 or
  1536. Documented in the stack table and in
  `data/embed_patterns.py`.
- The `judge` role has its own backend variable (`JUDGE_BACKEND`),
  independent of `LLM_BACKEND`, because the LLM-as-judge must be a
  different model (and preferably a different vendor) from the
  agents — see ADR/the evaluation doc.
- Prompt caching is Anthropic-specific and lives in
  `agents/llm_utils.py`. Other backends silently ignore the
  `cache_control` markers.
- Per-role model assignment is in `core/settings.py` so we can
  split — e.g. cheap Haiku for triage, stronger Sonnet for the
  case writer — when we have evidence that the split helps.
