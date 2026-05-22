"""Phase 3 evaluation harness.

Runs FALCON against every alert in `eval/ground_truth.json`, scores each
case file against ground truth (deterministic + LLM-judge), persists an
`EvaluationRun` plus per-alert `EvaluationResult` rows, and writes a
self-contained JSON to `eval/results/<run_id>.json` for the dashboard.

Usage:
    python -m eval.run_evaluation [--label haiku-4.5] [--limit 30]
                                  [--no-judge] [--no-resume]

Notes:
  - The harness auto-resumes any HITL pause with a neutral annotation. This
    is the eval-only path; production HITL requires real reviewer input.
  - The judge is a separate LLM controlled by `JUDGE_BACKEND` (see
    plan §3.3). Set `--no-judge` for a fast deterministic-only run.
  - The git SHA is recorded best-effort; missing git is not fatal.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from core.db import session_scope
from core.models import (
    EvaluationResult,
    EvaluationRun,
    Investigation,
)
from core.schemas import Annotation
from core.settings import get_settings
from eval.judge import judge_case, judge_neutrality_flag
from eval.scoring import aggregate, score_alert
from supervisor.run import investigate, resume

_GROUND_TRUTH = Path(__file__).resolve().parent / "ground_truth.json"
_RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _load_ground_truth() -> list[dict[str, Any]]:
    with _GROUND_TRUTH.open() as f:
        data = json.load(f)
    return data.get("alerts", [])


def _git_sha() -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        )
        return out.decode().strip()
    except Exception:  # noqa: BLE001
        return None


def _verdict_from_state(state: dict[str, Any], status: str) -> str:
    """Map (state, status) -> one of AUTO_CLOSE | REVIEW | SAR_FILE."""
    cf = state.get("final_case_file")
    if cf is not None:
        action = getattr(cf, "recommended_action", None)
        return action.value if action is not None else "REVIEW"
    if status == "auto_closed":
        return "AUTO_CLOSE"
    if status == "paused_hitl":
        return "REVIEW"
    return "REVIEW"


def _typology_from_state(state: dict[str, Any]) -> str:
    t = state.get("typology_assessment")
    if t is None:
        return "NONE"
    return t.primary_typology.value


def _network_accounts_from_state(state: dict[str, Any]) -> list[str]:
    """Per the eval spec: 'what % of linked accounts were discovered?' is the
    set of nodes in the discovered network graph, not the in-scope union."""
    graph = state.get("network_graph")
    if graph is None:
        return []
    return [n.account_id for n in graph.nodes]


def _evidence_text(case_file: Any) -> str:
    if case_file is None:
        return ""
    return "\n".join(
        [getattr(case_file, "executive_summary", "") or "",
         getattr(case_file, "suspicion_grounds", "") or ""]
        + [e.summary for e in getattr(case_file, "evidence_chain", []) or []]
    )


def _case_file_dict(case_file: Any) -> dict[str, Any] | None:
    if case_file is None:
        return None
    return case_file.model_dump(mode="json")


def _state_to_judge_input(state: dict[str, Any]) -> tuple[list[dict], dict | None]:
    profiles_obj = state.get("account_profiles", {}) or {}
    profiles = [p.model_dump(mode="json") for p in profiles_obj.values()]
    graph_obj = state.get("network_graph")
    network = graph_obj.model_dump(mode="json") if graph_obj is not None else None
    return profiles, network


def _final_status(status: str, state: dict[str, Any]) -> str:
    """Re-fetch investigation status after possible auto-resume."""
    if state.get("final_case_file") is not None:
        return "completed"
    return status


def _run_one(
    alert_id: str,
    *,
    auto_resume: bool,
) -> tuple[dict[str, Any], str, float]:
    """Investigate one alert. Returns (final_state, final_status, elapsed_sec)."""
    started = time.perf_counter()
    result = investigate(alert_id)
    status = result.get("status", "completed")
    state = result.get("state", {})

    if status == "paused_hitl" and auto_resume:
        annotation = Annotation(
            reviewer_id="eval_harness",
            note="Auto-resume by Phase 3 evaluation harness; no override applied.",
        )
        # New investigation_id is in the result.
        inv_id = uuid.UUID(result["investigation_id"])
        result = resume(inv_id, annotation)
        status = result.get("status", "completed")
        state = result.get("state", {})

    elapsed = time.perf_counter() - started
    return state, _final_status(status, state), elapsed


def run_evaluation(
    *,
    label: str | None = None,
    limit: int | None = None,
    use_judge: bool = True,
    auto_resume: bool = True,
) -> dict[str, Any]:
    settings = get_settings()
    truth_alerts = _load_ground_truth()
    if limit is not None:
        truth_alerts = truth_alerts[:limit]
    truth_by_id = {a["alert_id"]: a for a in truth_alerts}

    backend = settings.llm_backend
    if backend == "anthropic":
        backend_detail = settings.anthropic_model_triage
    elif backend == "vertex":
        backend_detail = settings.vertex_model_default
    elif backend == "bedrock":
        backend_detail = settings.bedrock_model_default
    else:
        backend_detail = "ollama"
    run_label = label or f"{backend}:{backend_detail}"

    # Persist the EvaluationRun header up front so the run is discoverable
    # even if the loop crashes halfway.
    with session_scope() as s:
        ev_run = EvaluationRun(backend=backend, git_sha=_git_sha(), summary={"label": run_label})
        s.add(ev_run)
        s.flush()
        run_id = ev_run.id

    per_alert: list[dict[str, Any]] = []
    scores = []
    print(f"\nFALCON Phase 3 evaluation — {len(truth_alerts)} alerts, backend={run_label}\n")
    print(
        f"{'alert':<11}{'exp v':<10}{'got v':<10}{'exp typ':<16}{'got typ':<16}"
        f"{'recall':<8}{'evid':<7}{'faith':<7}{'hallu':<7}{'secs':<6}"
    )
    print("-" * 110)

    for alert_id in sorted(truth_by_id.keys()):
        gt = truth_by_id[alert_id]
        try:
            state, status, elapsed = _run_one(alert_id, auto_resume=auto_resume)
        except Exception as e:  # noqa: BLE001
            per_alert.append(
                {
                    "alert_id": alert_id,
                    "error": f"{type(e).__name__}: {e}",
                    "elapsed_sec": None,
                }
            )
            print(f"{alert_id:<11}ERROR  {type(e).__name__}: {e}")
            continue

        got_verdict = _verdict_from_state(state, status)
        got_typology = _typology_from_state(state)
        found_accounts = _network_accounts_from_state(state)
        case_file = state.get("final_case_file")
        evidence_text = _evidence_text(case_file)

        score = score_alert(
            alert_id=alert_id,
            ground_truth=gt,
            got_verdict=got_verdict,
            got_typology=got_typology,
            found_network_accounts=found_accounts,
            case_evidence_text=evidence_text,
        )
        scores.append(score)

        judge_payload: dict[str, Any] = {
            "faithfulness_score": None,
            "hallucination_rate": None,
            "claims": [],
            "note": "judge disabled" if not use_judge else None,
        }
        if use_judge and case_file is not None:
            profiles, network = _state_to_judge_input(state)
            judge_payload = judge_case(
                case_file=_case_file_dict(case_file) or {},
                typology=got_typology,
                confidence=float(state.get("confidence_score", 0.0)),
                profiles=profiles,
                network=network,
            )

        # Look up investigation_id from the state for FK.
        # `_run_one` does not return it; rely on most-recent investigation for alert.
        inv_id = _latest_investigation_for_alert(alert_id)

        row = {
            **score.as_dict(),
            "status": status,
            "elapsed_sec": round(elapsed, 2),
            "faithfulness_score": judge_payload.get("faithfulness_score"),
            "hallucination_rate": judge_payload.get("hallucination_rate"),
            "judge_claims": judge_payload.get("claims"),
            "judge_note": judge_payload.get("note"),
            "investigation_id": str(inv_id) if inv_id else None,
        }
        per_alert.append(row)

        with session_scope() as s:
            s.add(
                EvaluationResult(
                    run_id=run_id,
                    alert_id=alert_id,
                    investigation_id=inv_id,
                    verdict_correct=score.verdict_correct,
                    typology_correct=score.typology_correct,
                    network_recall=score.network_recall,
                    faithfulness_score=judge_payload.get("faithfulness_score"),
                    hallucination_rate=judge_payload.get("hallucination_rate"),
                    metrics_json={
                        "evidence_recall": score.evidence_recall,
                        "network_precision": score.network_precision,
                        "got_verdict": score.got_verdict,
                        "expected_verdict": score.expected_verdict,
                        "got_typology": score.got_typology,
                        "expected_typology": score.expected_typology,
                        "judge_note": judge_payload.get("note"),
                    },
                )
            )

        print(
            f"{alert_id:<11}{score.expected_verdict:<10}{score.got_verdict:<10}"
            f"{score.expected_typology:<16}{score.got_typology:<16}"
            f"{score.network_recall:<8.2f}{score.evidence_recall:<7.2f}"
            f"{(judge_payload.get('faithfulness_score') or 0):<7.2f}"
            f"{(judge_payload.get('hallucination_rate') or 0):<7.2f}{elapsed:<6.1f}"
        )

    summary = aggregate(scores)
    faith_values = [
        r.get("faithfulness_score") for r in per_alert if r.get("faithfulness_score") is not None
    ]
    hallu_values = [
        r.get("hallucination_rate") for r in per_alert if r.get("hallucination_rate") is not None
    ]
    summary["faithfulness_mean"] = (
        round(sum(faith_values) / len(faith_values), 3) if faith_values else None
    )
    summary["hallucination_mean"] = (
        round(sum(hallu_values) / len(hallu_values), 3) if hallu_values else None
    )
    summary["label"] = run_label
    summary["judge"] = judge_neutrality_flag() if use_judge else {"enabled": False}
    summary["completed_at"] = datetime.now(UTC).isoformat()

    with session_scope() as s:
        run_row = s.get(EvaluationRun, run_id)
        if run_row is not None:
            run_row.summary = summary
            run_row.completed_at = datetime.now(UTC)

    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _RESULTS_DIR / f"{run_label.replace(':', '_').replace('/', '_')}_{run_id}.json"
    payload = {
        "run_id": str(run_id),
        "label": run_label,
        "backend": backend,
        "started_at": summary["completed_at"],
        "summary": summary,
        "results": per_alert,
    }
    with out_path.open("w") as f:
        json.dump(payload, f, indent=2, default=str)

    print("-" * 110)
    print(f"Verdict accuracy:       {summary.get('verdict_accuracy')}")
    print(f"Typology accuracy:      {summary.get('typology_accuracy')}")
    print(f"Network recall (mean):  {summary.get('network_recall_mean')}")
    print(f"Evidence recall (mean): {summary.get('evidence_recall_mean')}")
    print(f"Faithfulness (mean):    {summary.get('faithfulness_mean')}")
    print(f"Hallucination (mean):   {summary.get('hallucination_mean')}")
    print(f"False-positive rate:    {summary.get('false_positive_rate')}")
    print(f"False-negative rate:    {summary.get('false_negative_rate')}")
    print(f"\nResults written to:     {out_path}")
    return payload


def _latest_investigation_for_alert(alert_id: str) -> uuid.UUID | None:
    """The harness needs the investigation_id to populate the FK on
    `evaluation_results`. We can't easily thread it back from `investigate()`,
    so we look it up — newest investigation for the alert wins."""
    with session_scope() as s:
        row = s.execute(
            select(Investigation.id)
            .where(Investigation.alert_id == alert_id)
            .order_by(Investigation.started_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        return row


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--label", default=None, help="Override the run label (default: backend:model)")
    p.add_argument("--limit", type=int, default=None, help="Limit number of alerts")
    p.add_argument("--no-judge", action="store_true", help="Skip LLM-judge scoring (faster)")
    p.add_argument("--no-resume", action="store_true", help="Do not auto-resume HITL pauses")
    args = p.parse_args()
    run_evaluation(
        label=args.label,
        limit=args.limit,
        use_judge=not args.no_judge,
        auto_resume=not args.no_resume,
    )


if __name__ == "__main__":
    main()
