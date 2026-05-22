"""LangGraph state object for FALCON investigations.

InvestigationState is the source of truth between agents. DB writes from
agents are audit-only; routing reads here, never from the DB (plan §1.3).
"""
from __future__ import annotations

import uuid
from typing import Any, TypedDict

from core.schemas import (
    AccountProfile,
    Annotation,
    CaseFileSchema,
    ContradictionReport,
    NetworkGraph,
    TriageAssessment,
    TypologyAssessment,
)


class InvestigationState(TypedDict, total=False):
    alert_id: str
    investigation_id: uuid.UUID
    primary_account_id: str

    triage_result: TriageAssessment

    # accounts_to_investigate: queue consumed by the historian node; supervisor
    # appends to it on EXPAND.
    accounts_to_investigate: list[str]
    # accounts_investigated: every account profiled this run (history).
    accounts_investigated: list[str]
    # accounts_in_scope: investigated ∪ {n.account_id for n in network.nodes}.
    # Recomputed at every supervisor checkpoint — used by the scope rail.
    accounts_in_scope: list[str]

    account_profiles: dict[str, AccountProfile]
    network_graph: NetworkGraph

    expansion_count: int
    confidence_score: float

    # Phase 2 additions
    typology_assessment: TypologyAssessment
    contradiction_report: ContradictionReport
    human_annotations: list[Annotation]
    hitl_required: bool

    final_case_file: CaseFileSchema

    # Free-form bag for one-off diagnostic data.
    diagnostics: dict[str, Any]
