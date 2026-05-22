"""FastAPI app — Phase 4 production surface.

Endpoints:
  GET  /                                          Vanilla HTML dashboard
  GET  /health                                    Liveness
  GET  /alerts                                    Browse fraud alerts (with current investigation status)
  POST /investigations                            Trigger investigation from alert_id (runs in background)
  GET  /investigations[?status=...]               List investigations
  GET  /investigations/{id}                       Full investigation + case file
  GET  /investigations/{id}/events                Timeline of supervisor events
  GET  /investigations/{id}/traces[?agent=...]    Per-agent reasoning chain
  POST /investigations/{id}/annotate              Submit reviewer annotation + resume
  GET  /investigations/{id}/resume                Force resume without annotation
  GET  /cases/{case_id}                           Retrieve a completed case file by case-file id
  GET  /eval/latest                               Latest EvaluationRun summary + per-alert rows
  GET  /metrics                                   Observability — avg durations, per-agent latency, slow runs

Run:  uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import logging
import statistics
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("falcon.api")

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel
from sqlalchemy import func, select

from core.db import session_scope
from core.models import (
    AgentTrace,
    CaseFile,
    EvaluationResult,
    EvaluationRun,
    FraudAlert,
    Investigation,
    InvestigationEvent,
    ToolCallLog,
)
from core.schemas import Annotation
from supervisor.run import (
    begin_investigation,
    investigate as investigate_alert,
    resume as resume_investigation,
    run_investigation_graph,
)

app = FastAPI(title="FALCON", version="0.4.0")

_DASHBOARD_PATH = Path(__file__).resolve().parent / "static" / "dashboard.html"
_FAVICON_PATH = Path(__file__).resolve().parent / "static" / "favicon.svg"
_DEMO_CACHE_DIR = Path(__file__).resolve().parents[1] / "demo_cache"
_DEMO_TYPOLOGIES = {"STRUCTURING", "LAYERING", "ACCOUNT_TAKEOVER", "MULE_NETWORK", "PEP_EXPOSURE"}


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------


class _AnnotateBody(BaseModel):
    reviewer_id: str
    note: str
    override_action: str | None = None
    override_confidence: float | None = None


class _InvestigateBody(BaseModel):
    alert_id: str


# ---------------------------------------------------------------------------
# Serialisers
# ---------------------------------------------------------------------------


def _serialise_inv(inv: Investigation) -> dict[str, Any]:
    return {
        "id": str(inv.id),
        "alert_id": inv.alert_id,
        "status": inv.status,
        "confidence_score": float(inv.confidence_score) if inv.confidence_score is not None else None,
        "expansion_count": inv.expansion_count,
        "started_at": inv.started_at.isoformat() if inv.started_at else None,
        "completed_at": inv.completed_at.isoformat() if inv.completed_at else None,
        "state_metadata": inv.state_json or {},
    }


def _serialise_case(case: CaseFile) -> dict[str, Any]:
    return {
        "id": str(case.id),
        "investigation_id": str(case.investigation_id),
        "risk_tier": case.risk_tier,
        "recommended_action": case.recommended_action,
        "sar_ready": case.sar_ready,
        "confidence": float(case.confidence),
        "case_json": case.case_json,
        "created_at": case.created_at.isoformat() if case.created_at else None,
    }


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    """Phase 4.2 — vanilla HTML+JS dashboard. The frontend is a single
    self-contained file under api/static/dashboard.html — no build step.

    `no-store` because the page embeds the JS bundle inline; cached HTML
    means the user keeps seeing an old dashboard after every UI change.
    """
    if not _DASHBOARD_PATH.exists():
        return HTMLResponse(
            "<h1>FALCON</h1><p>Dashboard HTML not packaged with this build.</p>",
            status_code=200,
        )
    return HTMLResponse(
        _DASHBOARD_PATH.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/favicon.svg", include_in_schema=False)
def favicon_svg() -> Response:
    """SVG favicon — gradient F mark matching the sidebar brand."""
    if not _FAVICON_PATH.exists():
        return Response(status_code=404)
    return FileResponse(
        _FAVICON_PATH,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon_ico() -> Response:
    """Browsers auto-request /favicon.ico. We serve the SVG with the
    correct content-type so it still renders on browsers that follow
    the redirect."""
    return favicon_svg()


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


@app.get("/alerts")
def list_alerts(limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    """Browse fraud alerts. Annotates each alert with the latest
    investigation status (if any) so the dashboard can decide
    'trigger' vs 'open'."""
    with session_scope() as s:
        rows = s.execute(
            select(FraudAlert).order_by(FraudAlert.raised_at.desc()).limit(limit)
        ).scalars().all()
        # One round-trip for latest investigation per alert (cheap on 30).
        latest = {}
        invs = s.execute(
            select(Investigation).order_by(Investigation.started_at.desc())
        ).scalars().all()
        for inv in invs:
            latest.setdefault(inv.alert_id, inv)

        out = []
        for a in rows:
            inv = latest.get(a.id)
            out.append({
                "id": a.id,
                "account_id": a.account_id,
                "alert_type": a.alert_type,
                "initial_score": float(a.initial_score),
                "raised_at": a.raised_at.isoformat() if a.raised_at else None,
                "status": a.status,
                "latest_investigation": _serialise_inv(inv) if inv else None,
            })
        return out


# ---------------------------------------------------------------------------
# Investigations
# ---------------------------------------------------------------------------


def _run_graph_bg(investigation_id: uuid.UUID, alert_id: str, primary_account: str) -> None:
    """Background task. The HTTP caller has already returned 202 with the
    investigation_id; this runs the supervisor graph. On crash we mark the
    row as `failed` so the dashboard surfaces it and log the traceback.
    """
    try:
        run_investigation_graph(investigation_id, alert_id, primary_account)
    except Exception as e:  # noqa: BLE001
        logger.exception("Investigation %s crashed: %s", investigation_id, e)
        try:
            with session_scope() as s:
                inv = s.get(Investigation, investigation_id)
                if inv is not None and inv.status == "running":
                    inv.status = "failed"
                    inv.completed_at = datetime.now(UTC)
                    inv.state_json = {
                        **(inv.state_json or {}),
                        "error": f"{type(e).__name__}: {e}",
                    }
        except Exception:  # noqa: BLE001
            logger.exception("Failed to mark investigation %s as failed", investigation_id)


@app.post("/investigations", status_code=202)
def trigger_investigation(
    body: _InvestigateBody, background: BackgroundTasks
) -> dict[str, Any]:
    """Start a fresh investigation against an alert id.

    Creates the Investigation row foreground so we can hand the id back
    to the client immediately, then schedules the supervisor graph to
    run in the background. The dashboard attaches to the run by id and
    polls `/investigations/{id}/events` for the live agent feed.
    """
    try:
        inv_id, primary_account = begin_investigation(body.alert_id)
    except Exception as e:  # noqa: BLE001
        # most likely cause: alert_id doesn't exist (scalar_one fails)
        raise HTTPException(status_code=404, detail=f"alert {body.alert_id!r} not found") from e
    background.add_task(_run_graph_bg, inv_id, body.alert_id, primary_account)
    return {
        "status": "accepted",
        "alert_id": body.alert_id,
        "investigation_id": str(inv_id),
        "message": "Investigation started; subscribe to /investigations/{id}/events for the live feed.",
    }


@app.get("/investigations")
def list_investigations(
    status: str | None = Query(None, description="e.g. paused_hitl, running, completed"),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    with session_scope() as s:
        stmt = select(Investigation).order_by(Investigation.started_at.desc()).limit(limit)
        if status:
            stmt = stmt.where(Investigation.status == status)
        rows = s.execute(stmt).scalars().all()
        return [_serialise_inv(r) for r in rows]


@app.get("/investigations/{investigation_id}")
def get_investigation(investigation_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as s:
        inv = s.get(Investigation, investigation_id)
        if inv is None:
            raise HTTPException(status_code=404, detail="investigation not found")
        case = s.execute(
            select(CaseFile).where(CaseFile.investigation_id == investigation_id)
        ).scalar_one_or_none()
        return {
            **_serialise_inv(inv),
            "case_file": _serialise_case(case) if case is not None else None,
        }


@app.get("/investigations/{investigation_id}/events")
def list_events(investigation_id: uuid.UUID) -> list[dict[str, Any]]:
    """Phase 4.1 — supervisor event timeline. The live viewer polls this
    every 2s to render the LangGraph node-activation feed."""
    with session_scope() as s:
        rows = s.execute(
            select(InvestigationEvent)
            .where(InvestigationEvent.investigation_id == investigation_id)
            .order_by(InvestigationEvent.occurred_at.asc(), InvestigationEvent.id.asc())
        ).scalars().all()
        return [
            {
                "id": r.id,
                "event_type": r.event_type,
                "actor": r.actor,
                "payload": r.payload,
                "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
            }
            for r in rows
        ]


@app.post("/investigations/{investigation_id}/annotate")
def annotate(investigation_id: uuid.UUID, body: _AnnotateBody) -> dict[str, Any]:
    with session_scope() as s:
        inv = s.get(Investigation, investigation_id)
        if inv is None:
            raise HTTPException(status_code=404, detail="investigation not found")
        if inv.status != "paused_hitl":
            raise HTTPException(
                status_code=409,
                detail=f"investigation status is {inv.status!r}, expected 'paused_hitl'",
            )

    annotation = Annotation(
        reviewer_id=body.reviewer_id,
        note=body.note,
        override_action=body.override_action,  # type: ignore[arg-type]
        override_confidence=body.override_confidence,
    )
    result = resume_investigation(investigation_id, annotation)
    return result


@app.get("/investigations/{investigation_id}/traces")
def list_traces(
    investigation_id: uuid.UUID,
    agent: str | None = Query(None, description="Filter by agent name"),
) -> list[dict[str, Any]]:
    """Phase 3.5 — return the full reasoning chain for one investigation."""
    with session_scope() as s:
        stmt = (
            select(AgentTrace)
            .where(AgentTrace.investigation_id == investigation_id)
            .order_by(AgentTrace.recorded_at.asc(), AgentTrace.id.asc())
        )
        if agent:
            stmt = stmt.where(AgentTrace.agent_name == agent)
        rows = s.execute(stmt).scalars().all()
        return [
            {
                "id": r.id,
                "agent_name": r.agent_name,
                "step": r.step,
                "reasoning_text": r.reasoning_text,
                "token_count": r.token_count,
                "latency_ms": r.latency_ms,
                "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
            }
            for r in rows
        ]


@app.get("/investigations/{investigation_id}/resume")
def resume_endpoint(investigation_id: uuid.UUID) -> dict[str, Any]:
    """Force-resume without an annotation. Useful after a crash."""
    return resume_investigation(investigation_id, None)


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@app.get("/cases/{case_id}")
def get_case(case_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as s:
        case = s.get(CaseFile, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case file not found")
        return _serialise_case(case)


# ---------------------------------------------------------------------------
# Eval
# ---------------------------------------------------------------------------


@app.get("/eval/latest")
def latest_eval() -> dict[str, Any]:
    """Latest evaluation run summary plus per-alert results."""
    with session_scope() as s:
        run = s.execute(
            select(EvaluationRun).order_by(EvaluationRun.started_at.desc()).limit(1)
        ).scalar_one_or_none()
        if run is None:
            return {"run": None, "results": []}
        rows = s.execute(
            select(EvaluationResult).where(EvaluationResult.run_id == run.id)
        ).scalars().all()
        return {
            "run": {
                "id": str(run.id),
                "backend": run.backend,
                "git_sha": run.git_sha,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                "summary": run.summary or {},
            },
            "results": [
                {
                    "alert_id": r.alert_id,
                    "investigation_id": str(r.investigation_id) if r.investigation_id else None,
                    "verdict_correct": r.verdict_correct,
                    "typology_correct": r.typology_correct,
                    "network_recall": float(r.network_recall) if r.network_recall is not None else None,
                    "faithfulness_score": float(r.faithfulness_score) if r.faithfulness_score is not None else None,
                    "hallucination_rate": float(r.hallucination_rate) if r.hallucination_rate is not None else None,
                    "metrics": r.metrics_json,
                }
                for r in rows
            ],
        }


# ---------------------------------------------------------------------------
# Demo cache — pre-recorded investigation runs per typology
# ---------------------------------------------------------------------------


@app.get("/demo/{typology}")
def demo_replay(typology: str) -> dict[str, Any]:
    """Return a pre-captured investigation snapshot for the landing-page demo.

    The dashboard hits this first when a recruiter clicks a typology card;
    the cached payload is animated client-side with zero LLM cost. Live
    triggering is the opt-in fallback. See demo_cache/README.md.
    """
    typology = typology.upper()
    if typology not in _DEMO_TYPOLOGIES:
        raise HTTPException(status_code=404, detail=f"unknown typology {typology!r}")
    path = _DEMO_CACHE_DIR / f"{typology}.json"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"no cached demo for {typology}. Run `python -m scripts.capture_demo_runs --typology {typology}`.",
        )
    try:
        import json
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        logger.exception("Failed to read demo cache for %s: %s", typology, e)
        raise HTTPException(status_code=500, detail=f"failed to read demo cache: {e}") from e


# ---------------------------------------------------------------------------
# Metrics — Phase 4.3 observability
# ---------------------------------------------------------------------------


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1)))))
    return s[k]


@app.get("/metrics")
def metrics(slow_factor: float = Query(2.0, ge=1.1, le=10.0)) -> dict[str, Any]:
    """Phase 4.3 — observability surface.

    Computes: investigation count by status, duration percentiles,
    per-agent latency p50/p95/n, tool-call volume by tool, and the list
    of investigations whose total latency was > `slow_factor` × median
    (anomaly flag).
    """
    with session_scope() as s:
        # Status counts
        status_rows = s.execute(
            select(Investigation.status, func.count(Investigation.id)).group_by(Investigation.status)
        ).all()
        status_counts = {row[0]: int(row[1]) for row in status_rows}

        # Durations for completed investigations
        completed = s.execute(
            select(Investigation.id, Investigation.started_at, Investigation.completed_at).where(
                Investigation.completed_at.is_not(None)
            )
        ).all()
        durations: list[tuple[str, float]] = [
            (str(r[0]), (r[2] - r[1]).total_seconds()) for r in completed
        ]
        duration_values = [d for _, d in durations]
        duration_summary = {
            "count": len(duration_values),
            "p50_sec": _percentile(duration_values, 50),
            "p95_sec": _percentile(duration_values, 95),
            "max_sec": max(duration_values) if duration_values else None,
        }
        median = duration_summary["p50_sec"] or 0.0
        slow = [
            {"investigation_id": iid, "duration_sec": round(d, 1)}
            for iid, d in durations
            if median > 0 and d > slow_factor * median
        ]

        # Per-agent latency from agent_traces
        agent_rows = s.execute(
            select(AgentTrace.agent_name, AgentTrace.latency_ms).where(
                AgentTrace.latency_ms.is_not(None)
            )
        ).all()
        per_agent: dict[str, list[int]] = {}
        for name, lat in agent_rows:
            per_agent.setdefault(name, []).append(int(lat))
        agent_latency = {
            name: {
                "n": len(values),
                "p50_ms": _percentile(values, 50),
                "p95_ms": _percentile(values, 95),
                "mean_ms": int(statistics.fmean(values)) if values else None,
            }
            for name, values in per_agent.items()
        }

        # Tool call volume
        tool_rows = s.execute(
            select(ToolCallLog.tool_name, func.count(ToolCallLog.id)).group_by(ToolCallLog.tool_name)
        ).all()
        tool_counts = {row[0]: int(row[1]) for row in tool_rows}

        # Approx token cost — sum of token_count across traces
        total_tokens_row = s.execute(
            select(func.coalesce(func.sum(AgentTrace.token_count), 0))
        ).scalar_one()

        return {
            "investigations_by_status": status_counts,
            "duration": duration_summary,
            "slow_investigations": slow,
            "slow_factor": slow_factor,
            "per_agent_latency": agent_latency,
            "tool_call_counts": tool_counts,
            "total_tokens": int(total_tokens_row or 0),
        }
