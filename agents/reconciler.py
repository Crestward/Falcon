"""Reconciler — small LLM call invoked when Triage's hypothesis and Pattern
Hunter's classification disagree.

Per plan §2.1: Triage was designed for alert-metadata reasoning only, so we
do NOT re-run Triage with full investigation state. Instead, a dedicated
reconciler reads both agents' evidence and produces a single reconciled
TypologyAssessment + written rationale, logged as a RECONCILIATION event.
"""
from __future__ import annotations

import uuid

from agents.llm_utils import call_structured
from core.db import session_scope
from core.models import InvestigationEvent
from core.schemas import (
    FraudTypology,
    TriageAssessment,
    TypologyAssessment,
)
from mcp_servers.case_management import queries as case_q

# Keywords in Triage's initial_hypothesis that map to a typology.
# Tight matching: ambiguous hypotheses don't trigger conflict (and shouldn't).
_HYPOTHESIS_KEYWORDS: dict[str, FraudTypology] = {
    "structur": FraudTypology.STRUCTURING,
    "smurf": FraudTypology.STRUCTURING,
    "layer": FraudTypology.LAYERING,
    "takeover": FraudTypology.ACCOUNT_TAKEOVER,
    "ato": FraudTypology.ACCOUNT_TAKEOVER,
    "mule": FraudTypology.MULE_NETWORK,
    "pep": FraudTypology.PEP_EXPOSURE,
    "politically exposed": FraudTypology.PEP_EXPOSURE,
    "sanction": FraudTypology.PEP_EXPOSURE,
}


def triage_implied_typology(triage: TriageAssessment) -> FraudTypology | None:
    hyp = (triage.initial_hypothesis or "").lower()
    for needle, typology in _HYPOTHESIS_KEYWORDS.items():
        if needle in hyp:
            return typology
    return None


def needs_reconciliation(
    triage: TriageAssessment, hunter: TypologyAssessment
) -> bool:
    implied = triage_implied_typology(triage)
    if implied is None:
        return False
    if hunter.primary_typology == FraudTypology.NONE:
        return False
    return implied != hunter.primary_typology


SYSTEM_PROMPT = """You are the Reconciler.

The Triage Agent's initial hypothesis (formed from alert metadata only)
and the Pattern Hunter's classification (formed after a full investigation)
disagree on the fraud typology. Decide which classification the case file
should adopt.

You have access to both agents' evidence. Pattern Hunter has the richer
context, but Triage may have caught a signal the network analysis missed.
Produce a single reconciled TypologyAssessment.

Rules:
  - You MAY adopt the Pattern Hunter typology unchanged, the Triage
    hypothesis, or a third typology if the evidence supports it.
  - Your rationale must explain why the chosen typology wins and why the
    rejected one's evidence is weaker.
  - The matches list must include all five typologies with scores
    reflecting your reconciled assessment.
"""


def reconcile(
    triage: TriageAssessment,
    hunter: TypologyAssessment,
    investigation_id: uuid.UUID,
) -> TypologyAssessment:
    implied = triage_implied_typology(triage)
    user_prompt = (
        f"TRIAGE INITIAL HYPOTHESIS: {triage.initial_hypothesis}\n"
        f"  Implied typology: {implied.value if implied else 'AMBIGUOUS'}\n"
        f"  Triage severity: {triage.severity.value}\n"
        f"  Triage justification: {triage.justification}\n\n"
        f"PATTERN HUNTER PRIMARY: {hunter.primary_typology.value} "
        f"(score {hunter.primary_score:.2f})\n"
        f"  Hunter rationale: {hunter.rationale}\n"
        f"  All detector matches: {[m.model_dump() for m in hunter.matches]}\n\n"
        "Reconcile and return a TypologyAssessment."
    )

    reconciled = call_structured(
        role="pattern_hunter",  # reuse pattern_hunter role; same model
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=TypologyAssessment,
        investigation_id=investigation_id,
        agent_name="reconciler",
    )

    # Log the reconciliation event explicitly.
    with session_scope() as s:
        s.add(
            InvestigationEvent(
                investigation_id=investigation_id,
                event_type="RECONCILIATION",
                actor="reconciler",
                payload={
                    "triage_implied": implied.value if implied else None,
                    "hunter_primary": hunter.primary_typology.value,
                    "reconciled_primary": reconciled.primary_typology.value,
                    "rationale_excerpt": reconciled.rationale[:500],
                },
            )
        )
    case_q.record_decision(
        investigation_id=investigation_id,
        agent_name="reconciler",
        decision_type="RECONCILIATION",
        decision_payload=reconciled.model_dump(mode="json"),
        justification=(
            f"Reconciled Triage({implied.value if implied else 'AMBIG'}) "
            f"vs Hunter({hunter.primary_typology.value}) "
            f"-> {reconciled.primary_typology.value}"
        ),
    )
    return reconciled
