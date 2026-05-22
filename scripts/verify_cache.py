"""Standalone proof that prompt caching is live.

Makes 6 sequential Anthropic calls with FALCON_PREAMBLE in the system
block. Prints `cache_creation_input_tokens` and `cache_read_input_tokens`
straight from the API response on every call. First call should CREATE
the cache; subsequent calls should READ from it.

Run:
    python -m scripts.verify_cache
"""
from __future__ import annotations

from anthropic import Anthropic

from agents.preamble import FALCON_PREAMBLE
from core.settings import get_settings


def main() -> None:
    s = get_settings()
    if not s.anthropic_api_key:
        raise SystemExit("ANTHROPIC_API_KEY is not set.")
    client = Anthropic(api_key=s.anthropic_api_key)
    model = s.anthropic_model_triage  # whichever Haiku you have configured

    print(f"\nVerifying prompt caching against model={model}\n")
    print(f"{'call':<6}{'input':<10}{'cache_create':<16}{'cache_read':<14}{'output':<10}")
    print("-" * 56)

    total_create = total_read = 0
    for i in range(1, 7):
        m = client.messages.create(
            model=model,
            max_tokens=50,
            system=[
                {
                    "type": "text",
                    "text": FALCON_PREAMBLE,
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            messages=[{"role": "user", "content": f"Reply with exactly: pong {i}"}],
        )
        u = m.usage
        total_create += u.cache_creation_input_tokens or 0
        total_read += u.cache_read_input_tokens or 0
        print(
            f"{i:<6}{u.input_tokens:<10}"
            f"{u.cache_creation_input_tokens:<16}"
            f"{u.cache_read_input_tokens:<14}"
            f"{u.output_tokens:<10}"
        )

    print("-" * 56)
    print(f"TOTAL cache_create: {total_create} tokens")
    print(f"TOTAL cache_read:   {total_read} tokens")
    print()
    if total_read > 0:
        # Haiku 4.5 read pricing is 10% of input price; saved vs writing fresh
        # would be the same tokens at full price.
        approx_saved = total_read * 0.9
        print(f"Approx input tokens *not* charged at full rate: {approx_saved:.0f}")
        print("--> Cache is live. If your dashboard still says 'not using",
              "prompt caching', wait for the org-level aggregation window or",
              "make a few more calls.")
    else:
        print("--> Cache did NOT engage. Check that FALCON_PREAMBLE >= 4096 tokens.")


if __name__ == "__main__":
    main()
