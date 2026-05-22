"""LangGraph wiring for FALCON (Phase 2).

  START -> triage -> account_historian -> network_mapper
        -> (CHECKPOINT_3) EXPAND -> account_historian | PROCEED -> pattern_hunter
                                                                          |
                                                              optional: reconciler
                                                                          |
        -> (CHECKPOINT_4)  contradiction_detect + confidence recompute
              < 0.4   -> auto_close   -> END
              0.4-0.75 -> hitl_pause  -> case_writer -> END   (interrupt; resume via API)
              > 0.75  -> case_writer  -> END
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agents.account_historian import historian
from agents.case_writer import case_writer as case_writer_fn
from agents.network_mapper import network_mapper
from agents.pattern_hunter import pattern_hunter
from agents.reconciler import needs_reconciliation, reconcile
from agents.triage import triage as triage_fn
from core.db import session_scope
from core.models import InvestigationEvent
from supervisor.confidence import compute_confidence
from supervisor.config import (
    CONF_AUTO_CLOSE_BELOW,
    CONF_SAR_ABOVE,
    MAX_EXPANSIONS,
    NETWORK_MAX_HOPS,
)
from supervisor.contradiction import detect_contradictions
from supervisor.state import InvestigationState

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _emit(investigation_id: uuid.UUID, event_type: str, payload: dict | None = None) -> None:
    with session_scope() as s:
        s.add(
            InvestigationEvent(
                investigation_id=investigation_id,
                event_type=event_type,
                actor="supervisor",
                payload=payload or {},
            )
        )


def _recompute_scope(state: InvestigationState) -> list[str]:
    investigated = set(state.get("accounts_investigated", []))
    network = state.get("network_graph")
    node_ids = {n.account_id for n in network.nodes} if network else set()
    return sorted(investigated | node_ids)


# ----------------------------------------------------------------------------
# Nodes
# ----------------------------------------------------------------------------


def node_triage(state: InvestigationState) -> InvestigationState:
    investigation_id = state["investigation_id"]
    assessment = triage_fn(state["alert_id"], investigation_id)
    _emit(investigation_id, "TRIAGE_COMPLETED", {"severity": assessment.severity.value})
    primary = state["primary_account_id"]
    return {
        "triage_result": assessment,
        "accounts_to_investigate": [primary],
        "accounts_investigated": [],
        "accounts_in_scope": [primary],
        "account_profiles": {},
        "expansion_count": 0,
    }


def node_account_historian(state: InvestigationState) -> InvestigationState:
    investigation_id = state["investigation_id"]
    depth = state["triage_result"].recommended_depth
    profiles = dict(state.get("account_profiles", {}))
    investigated = list(state.get("accounts_investigated", []))

    queue = [a for a in state.get("accounts_to_investigate", []) if a not in profiles]
    for account_id in queue:
        profile = historian(account_id, depth, investigation_id)
        profiles[account_id] = profile
        investigated.append(account_id)
        _emit(
            investigation_id,
            "ACCOUNT_HISTORIAN_COMPLETED",
            {"account_id": account_id, "anomaly_count": len(profile.anomalies)},
        )

    new_state: InvestigationState = {
        "account_profiles": profiles,
        "accounts_investigated": investigated,
        "accounts_to_investigate": [],
    }
    new_state["accounts_in_scope"] = _recompute_scope({**state, **new_state})
    return new_state


def node_network_mapper(state: InvestigationState) -> InvestigationState:
    investigation_id = state["investigation_id"]
    graph = network_mapper(
        seed_account_id=state["primary_account_id"],
        accounts_already_investigated=state.get("accounts_investigated", []),
        investigation_id=investigation_id,
        max_hops=NETWORK_MAX_HOPS,
        scope=state.get("accounts_in_scope"),
    )
    _emit(
        investigation_id,
        "NETWORK_MAPPER_COMPLETED",
        {
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "expand": graph.expansion_request.trigger,
        },
    )
    out: InvestigationState = {"network_graph": graph}
    out["accounts_in_scope"] = _recompute_scope({**state, **out})
    return out


def route_after_network(state: InvestigationState) -> Literal["expand", "proceed"]:
    """CHECKPOINT_3 — pure routing decision."""
    graph = state["network_graph"]
    expand_count = state.get("expansion_count", 0)
    if not graph.expansion_request.trigger:
        return "proceed"
    if expand_count >= MAX_EXPANSIONS:
        return "proceed"
    return "expand"


def node_expand(state: InvestigationState) -> InvestigationState:
    graph = state["network_graph"]
    new_accounts = [
        a for a in graph.expansion_request.new_accounts
        if a not in set(state.get("accounts_investigated", []))
    ]
    _emit(
        state["investigation_id"],
        "EXPANSION_REQUESTED",
        {"new_accounts": new_accounts, "rationale": graph.expansion_request.rationale},
    )
    return {
        "accounts_to_investigate": new_accounts,
        "expansion_count": state.get("expansion_count", 0) + 1,
    }


def node_pattern_hunter(state: InvestigationState) -> InvestigationState:
    investigation_id = state["investigation_id"]
    profiles = state.get("account_profiles", {})
    network = state["network_graph"]
    assessment = pattern_hunter(profiles, network, investigation_id)

    # If Triage's hypothesis disagrees with Hunter's classification, reconcile.
    triage = state["triage_result"]
    if needs_reconciliation(triage, assessment):
        _emit(
            investigation_id,
            "CLASSIFICATION_CONFLICT",
            {
                "triage_hypothesis": triage.initial_hypothesis,
                "hunter_primary": assessment.primary_typology.value,
            },
        )
        assessment = reconcile(triage, assessment, investigation_id)

    _emit(
        investigation_id,
        "PATTERN_HUNTER_COMPLETED",
        {
            "primary_typology": assessment.primary_typology.value,
            "primary_score": assessment.primary_score,
        },
    )
    return {"typology_assessment": assessment}


def node_checkpoint_4(state: InvestigationState) -> InvestigationState:
    """CHECKPOINT_4 step 1+2: contradiction detection then confidence recompute.

    The router (`route_after_checkpoint_4`) does the 3-tier routing on the
    confidence written here.
    """
    investigation_id = state["investigation_id"]
    typology = state["typology_assessment"]
    profiles = state.get("account_profiles", {})
    network = state.get("network_graph")
    triage = state["triage_result"]

    contradictions = detect_contradictions(typology, profiles, network) if network else None
    confidence = compute_confidence(
        triage=triage,
        profiles=profiles,
        network=network,
        typology=typology,
        contradictions=contradictions,
    )

    if contradictions and contradictions.contradictions:
        _emit(
            investigation_id,
            "CONTRADICTION_DETECTED",
            {
                "count": len(contradictions.contradictions),
                "penalty": contradictions.confidence_penalty,
                "items": contradictions.contradictions,
            },
        )

    hitl_required = CONF_AUTO_CLOSE_BELOW <= confidence <= CONF_SAR_ABOVE
    _emit(
        investigation_id,
        "CHECKPOINT_4_DECISION",
        {
            "confidence": confidence,
            "hitl_required": hitl_required,
            "route": (
                "auto_close" if confidence < CONF_AUTO_CLOSE_BELOW
                else "hitl_pause" if hitl_required
                else "case_writer"
            ),
        },
    )
    return {
        "contradiction_report": contradictions,
        "confidence_score": confidence,
        "hitl_required": hitl_required,
    }


def route_after_checkpoint_4(
    state: InvestigationState,
) -> Literal["auto_close", "hitl_pause", "case_writer"]:
    conf = state.get("confidence_score", 0.0)
    if conf < CONF_AUTO_CLOSE_BELOW:
        return "auto_close"
    if conf <= CONF_SAR_ABOVE:
        return "hitl_pause"
    return "case_writer"


def node_auto_close(state: InvestigationState) -> InvestigationState:
    """Low-confidence path: mark investigation as auto_closed, no case file."""
    investigation_id = state["investigation_id"]
    from datetime import UTC, datetime

    from core.models import Investigation

    with session_scope() as s:
        inv = s.get(Investigation, investigation_id)
        if inv is not None:
            inv.status = "auto_closed"
            inv.confidence_score = state.get("confidence_score", 0.0)
            inv.completed_at = datetime.now(UTC)
    _emit(investigation_id, "AUTO_CLOSED", {"confidence": state.get("confidence_score", 0.0)})
    return {}


def node_hitl_pause(state: InvestigationState) -> InvestigationState:
    """HITL pause node — interrupts execution until annotation arrives.

    Sets `investigations.status='paused_hitl'` so the FastAPI listing endpoint
    can find this case, then raises GraphInterrupt via langgraph's interrupt().
    On resume, the value passed to `Command(resume=...)` becomes the return
    value of interrupt() and we add it to state as `human_annotations`.
    """
    investigation_id = state["investigation_id"]
    from datetime import UTC, datetime

    from core.models import Investigation

    with session_scope() as s:
        inv = s.get(Investigation, investigation_id)
        if inv is not None and inv.status != "paused_hitl":
            inv.status = "paused_hitl"
            inv.state_json = {**(inv.state_json or {}), "paused_at": datetime.now(UTC).isoformat()}
    _emit(investigation_id, "HITL_PAUSE", {"confidence": state.get("confidence_score", 0.0)})

    # Block here until resumed externally with an Annotation dict.
    annotation_payload: Any = interrupt(
        {
            "investigation_id": str(investigation_id),
            "confidence": state.get("confidence_score", 0.0),
            "primary_typology": (
                state["typology_assessment"].primary_typology.value
                if state.get("typology_assessment")
                else None
            ),
            "prompt": "Reviewer annotation required to proceed.",
        }
    )

    # On resume, flip status back to running and stash the annotation.
    from core.schemas import Annotation

    with session_scope() as s:
        inv = s.get(Investigation, investigation_id)
        if inv is not None:
            inv.status = "running"
    _emit(investigation_id, "HITL_RESUMED", {"annotation_present": annotation_payload is not None})

    annotations = list(state.get("human_annotations", []))
    if annotation_payload:
        if isinstance(annotation_payload, dict):
            annotations.append(Annotation.model_validate(annotation_payload))
        elif isinstance(annotation_payload, Annotation):
            annotations.append(annotation_payload)
    return {"human_annotations": annotations}


def node_case_writer(state: InvestigationState) -> InvestigationState:
    investigation_id = state["investigation_id"]
    graph = state.get("network_graph")
    if (
        graph is not None
        and graph.expansion_request.trigger
        and state.get("expansion_count", 0) >= MAX_EXPANSIONS
    ):
        _emit(
            investigation_id,
            "EXPANSION_CAP_HIT",
            {
                "expansion_count": state.get("expansion_count", 0),
                "requested": graph.expansion_request.new_accounts,
            },
        )

    # Confidence has already been written by checkpoint_4 in Phase 2.
    confidence = state.get("confidence_score", 0.0)
    case_file = case_writer_fn(
        investigation_id=investigation_id,
        alert_id=state["alert_id"],
        triage=state["triage_result"],
        profiles=state.get("account_profiles", {}),
        network=state["network_graph"],
        confidence=confidence,
        typology=state.get("typology_assessment"),
        contradictions=state.get("contradiction_report"),
        annotations=state.get("human_annotations", []),
    )
    _emit(
        investigation_id,
        "CASE_WRITER_COMPLETED",
        {"recommended_action": case_file.recommended_action.value, "confidence": confidence},
    )
    return {"final_case_file": case_file}


# ----------------------------------------------------------------------------
# Graph assembly
# ----------------------------------------------------------------------------


def build_graph(checkpointer: Any | None = None):
    g = StateGraph(InvestigationState)
    g.add_node("triage", node_triage)
    g.add_node("account_historian", node_account_historian)
    g.add_node("network_mapper", node_network_mapper)
    g.add_node("expand", node_expand)
    g.add_node("pattern_hunter", node_pattern_hunter)
    g.add_node("checkpoint_4", node_checkpoint_4)
    g.add_node("auto_close", node_auto_close)
    g.add_node("hitl_pause", node_hitl_pause)
    g.add_node("case_writer", node_case_writer)

    g.add_edge(START, "triage")
    g.add_edge("triage", "account_historian")
    g.add_edge("account_historian", "network_mapper")
    g.add_conditional_edges(
        "network_mapper",
        route_after_network,
        {"expand": "expand", "proceed": "pattern_hunter"},
    )
    g.add_edge("expand", "account_historian")
    g.add_edge("pattern_hunter", "checkpoint_4")
    g.add_conditional_edges(
        "checkpoint_4",
        route_after_checkpoint_4,
        {
            "auto_close": "auto_close",
            "hitl_pause": "hitl_pause",
            "case_writer": "case_writer",
        },
    )
    g.add_edge("hitl_pause", "case_writer")
    g.add_edge("auto_close", END)
    g.add_edge("case_writer", END)

    return g.compile(checkpointer=checkpointer)
