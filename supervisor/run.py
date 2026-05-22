"""Entry points for FALCON investigations.

`investigate(alert_id)` runs one investigation end-to-end with a Postgres
checkpointer. If the graph reaches the HITL_PAUSE node, it returns with
status='paused_hitl' — the case can be resumed later via `resume(...)`.

`resume(investigation_id, annotation)` re-enters the graph from the
HITL_PAUSE checkpoint with the reviewer's annotation injected into state.
"""
from __future__ import annotations

import uuid
from typing import Any

from langgraph.types import Command
from sqlalchemy import select

from core.db import session_scope
from core.models import FraudAlert, Investigation
from core.schemas import Annotation
from supervisor.checkpointer import get_checkpointer
from supervisor.graph import build_graph
from supervisor.state import InvestigationState


def _start_investigation(alert_id: str) -> tuple[uuid.UUID, str]:
    with session_scope() as s:
        alert = s.execute(
            select(FraudAlert).where(FraudAlert.id == alert_id)
        ).scalar_one()
        inv = Investigation(alert_id=alert.id, status="running")
        s.add(inv)
        s.flush()
        return inv.id, alert.account_id


def _thread_config(investigation_id: uuid.UUID, recursion_limit: int = 50) -> dict[str, Any]:
    return {
        "configurable": {"thread_id": str(investigation_id)},
        "recursion_limit": recursion_limit,
    }


def begin_investigation(alert_id: str) -> tuple[uuid.UUID, str]:
    """Create the Investigation row, return (id, primary_account_id).

    Public foreground entry point so an API caller can hand the id back to
    the client immediately and have the supervisor run in the background.
    """
    return _start_investigation(alert_id)


def run_investigation_graph(
    investigation_id: uuid.UUID, alert_id: str, primary_account_id: str
) -> dict[str, Any]:
    """Run the supervisor graph for an already-created investigation."""
    initial: InvestigationState = {
        "alert_id": alert_id,
        "investigation_id": investigation_id,
        "primary_account_id": primary_account_id,
    }
    with get_checkpointer() as cp:
        graph = build_graph(checkpointer=cp)
        config = _thread_config(investigation_id)
        result = graph.invoke(initial, config=config)
        return _final_status(result, graph, config)


def investigate(alert_id: str) -> dict[str, Any]:
    """Run a full investigation start-to-finish. Returns immediately if it
    hits HITL with status='paused_hitl'."""
    inv_id, primary = begin_investigation(alert_id)
    return run_investigation_graph(inv_id, alert_id, primary)


def resume(
    investigation_id: uuid.UUID,
    annotation: Annotation | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resume a paused investigation by injecting the reviewer's annotation."""
    payload = annotation.model_dump(mode="json") if isinstance(annotation, Annotation) else annotation
    with get_checkpointer() as cp:
        graph = build_graph(checkpointer=cp)
        config = _thread_config(investigation_id)
        result = graph.invoke(Command(resume=payload), config=config)
        return _final_status(result, graph, config)


def _final_status(result: Any, graph: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Normalise the return so the caller can tell `paused` from `done`."""
    # When an interrupt fires, langgraph returns the parent state; we look up
    # the snapshot to check whether the graph is paused or finished.
    snap = graph.get_state(config)
    is_paused = bool(snap.next)
    return {
        "investigation_id": config["configurable"]["thread_id"],
        "status": "paused_hitl" if is_paused else "completed",
        "state": result,
    }
