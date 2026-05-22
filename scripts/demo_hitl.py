"""HITL pause-and-resume demonstration.

Runs one investigation, expects it to pause at HITL (medium confidence).
Then submits an annotation programmatically and confirms the case file
is produced.

Run:  python -m scripts.demo_hitl ALERT0005
"""
from __future__ import annotations

import argparse
import sys
import uuid

from core.schemas import Annotation, RecommendedAction
from supervisor.run import investigate, resume


def main(alert_id: str) -> int:
    print(f"\nStarting investigation for {alert_id}...")
    result = investigate(alert_id)
    inv_id = uuid.UUID(result["investigation_id"])
    print(f"  status: {result['status']}")
    if result["status"] != "paused_hitl":
        print("  (case did not require HITL — pick an alert that lands in the 0.4–0.75 band)")
        return 1

    print("\nInvestigation paused. Submitting demo annotation...")
    ann = Annotation(
        reviewer_id="demo-reviewer",
        note=(
            "Reviewer override: confirmed PEP exposure; treat as REVIEW with "
            "elevated confidence for downstream EDD escalation."
        ),
        override_action=RecommendedAction.REVIEW,
        override_confidence=0.7,
    )
    result2 = resume(inv_id, ann)
    print(f"  resumed status: {result2['status']}")
    cf = result2["state"].get("final_case_file")
    if cf is None:
        print("  no case file produced after resume.")
        return 1
    print(f"  final verdict: {cf.recommended_action.value} (confidence {cf.confidence:.2f})")
    print(f"  evidence items: {len(cf.evidence_chain)}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("alert_id", default="ALERT0005", nargs="?")
    args = p.parse_args()
    sys.exit(main(args.alert_id))
