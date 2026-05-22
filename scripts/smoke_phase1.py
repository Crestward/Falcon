"""Phase 1 smoke test — run FALCON against 5 alerts and print a results table.

Plan §1.5 deliverable. Run with:
    python -m scripts.smoke_phase1
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from supervisor.run import investigate


def _ground_truth() -> dict[str, dict]:
    path = Path(__file__).resolve().parents[1] / "eval" / "ground_truth.json"
    with path.open() as f:
        data = json.load(f)
    return {a["alert_id"]: a for a in data.get("alerts", [])}


def main(n: int = 5) -> None:
    truth = _ground_truth()
    alert_ids = sorted(truth.keys())[:n]
    print(f"\nRunning FALCON Phase 1 smoke on {len(alert_ids)} alerts...\n")

    rows = []
    for alert_id in alert_ids:
        gt = truth[alert_id]
        started = time.perf_counter()
        try:
            final = investigate(alert_id)
            duration = time.perf_counter() - started
            case = final.get("final_case_file")
            rows.append(
                {
                    "alert": alert_id,
                    "expected_typology": gt.get("expected_typology"),
                    "expected_verdict": gt.get("expected_verdict"),
                    "verdict": case.recommended_action.value if case else "ERROR",
                    "confidence": f"{final.get('confidence_score', 0):.2f}",
                    "accounts": len(final.get("account_profiles", {})),
                    "expansions": final.get("expansion_count", 0),
                    "secs": f"{duration:.1f}",
                }
            )
        except Exception as e:  # pragma: no cover - smoke-only diagnostics
            rows.append(
                {
                    "alert": alert_id,
                    "expected_typology": gt.get("expected_typology"),
                    "expected_verdict": gt.get("expected_verdict"),
                    "verdict": f"FAIL: {type(e).__name__}",
                    "confidence": "-",
                    "accounts": 0,
                    "expansions": 0,
                    "secs": f"{time.perf_counter() - started:.1f}",
                }
            )

    print(
        f"{'alert':<12}{'exp typology':<18}{'exp verdict':<14}"
        f"{'verdict':<14}{'conf':<6}{'accts':<6}{'exp':<5}{'secs':<6}"
    )
    print("-" * 80)
    for r in rows:
        print(
            f"{r['alert']:<12}{(r['expected_typology'] or ''):<18}"
            f"{(r['expected_verdict'] or ''):<14}{r['verdict']:<14}"
            f"{r['confidence']:<6}{r['accounts']:<6}{r['expansions']:<5}{r['secs']:<6}"
        )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("-n", type=int, default=5, help="number of alerts to run")
    args = p.parse_args()
    main(args.n)
