"""Phase 2 smoke — run FALCON against N alerts (default 30) and report.

Run:  python -m scripts.smoke_phase2 [-n 30]
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


def main(n: int) -> None:
    truth = _ground_truth()
    alert_ids = sorted(truth.keys())[:n]
    print(f"\nFALCON Phase 2 smoke — {len(alert_ids)} alerts\n")
    print(
        f"{'alert':<11}{'exp typ':<16}{'got typ':<16}"
        f"{'exp verd':<12}{'got verd':<12}{'conf':<6}{'contra':<8}{'status':<14}{'secs':<6}"
    )
    print("-" * 110)

    summary = {
        "total": 0,
        "completed": 0,
        "paused_hitl": 0,
        "auto_closed": 0,
        "verdict_match": 0,
        "typology_match": 0,
        "with_contradictions": 0,
    }
    for alert_id in alert_ids:
        gt = truth[alert_id]
        started = time.perf_counter()
        try:
            result = investigate(alert_id)
        except Exception as e:  # pragma: no cover
            print(
                f"{alert_id:<11}{(gt.get('expected_typology') or ''):<16}{'ERROR':<16}"
                f"{(gt.get('expected_verdict') or ''):<12}{type(e).__name__:<12}"
            )
            continue
        elapsed = time.perf_counter() - started

        state = result.get("state", {})
        status = result.get("status", "?")
        cf = state.get("final_case_file")
        typology = state.get("typology_assessment")
        contradictions = state.get("contradiction_report")

        got_verd = cf.recommended_action.value if cf else ("AUTO_CLOSE" if status != "paused_hitl" else "PAUSED")
        got_typ = typology.primary_typology.value if typology else "-"
        conf = state.get("confidence_score", 0.0)
        n_contra = len(contradictions.contradictions) if contradictions else 0

        summary["total"] += 1
        summary[status] = summary.get(status, 0) + 1
        if gt.get("expected_verdict") == got_verd:
            summary["verdict_match"] += 1
        if gt.get("expected_typology") == got_typ:
            summary["typology_match"] += 1
        if n_contra:
            summary["with_contradictions"] += 1

        print(
            f"{alert_id:<11}{(gt.get('expected_typology') or ''):<16}{got_typ:<16}"
            f"{(gt.get('expected_verdict') or ''):<12}{got_verd:<12}"
            f"{conf:<6.2f}{n_contra:<8}{status:<14}{elapsed:<6.1f}"
        )

    print("-" * 110)
    print()
    print(f"Total runs:           {summary['total']}")
    print(f"Completed:            {summary.get('completed', 0)}")
    print(f"Paused (HITL):        {summary.get('paused_hitl', 0)}")
    print(f"Auto-closed:          {summary.get('auto_closed', 0)}")
    print(f"Verdict matches:      {summary['verdict_match']} / {summary['total']}")
    print(f"Typology matches:     {summary['typology_match']} / {summary['total']}")
    print(f"Runs w/ contradiction:{summary['with_contradictions']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("-n", type=int, default=30)
    args = p.parse_args()
    main(args.n)
