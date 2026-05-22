"""Escalation rail — Case Writer cannot file an SAR without evidence.

Hard constraint enforced in code, not via prompt. The LLM's recommendation
is treated as a *request*; this rail is the gate that downgrades the action
if the preconditions aren't met. Regulatory grounding: FCA SUP 15.3 and
JMLSG SAR guidance both require articulated suspicion grounds with
supporting evidence — an SAR with thin justification creates regulatory
risk for the bank.
"""
from __future__ import annotations

import uuid

from core.schemas import CaseFileSchema, RecommendedAction, SeverityTier
from guardrails.audit import write_security_event

MIN_EVIDENCE_FOR_SAR: int = 3
MIN_CONFIDENCE_FOR_SAR: float = 0.75


def enforce_escalation(
    case_file: CaseFileSchema,
    *,
    investigation_id: uuid.UUID | None = None,
    actor: str = "case_writer",
) -> CaseFileSchema:
    """Inspect the case file's recommended_action. If it claims SAR_FILE but
    fails the preconditions, downgrade to REVIEW and log a security event.
    Returns the (possibly modified) case file."""
    if case_file.recommended_action != RecommendedAction.SAR_FILE:
        # Other actions (AUTO_CLOSE, REVIEW) have no escalation gate.
        if case_file.sar_ready:
            # sar_ready=True is only consistent with SAR_FILE.
            return case_file.model_copy(update={"sar_ready": False})
        return case_file

    evidence_count = len(case_file.evidence_chain)
    confidence = case_file.confidence
    if evidence_count >= MIN_EVIDENCE_FOR_SAR and confidence > MIN_CONFIDENCE_FOR_SAR:
        # Met both gates. Force sar_ready true for consistency.
        return case_file.model_copy(update={"sar_ready": True})

    write_security_event(
        rail="escalation",
        actor=actor,
        severity="warning",
        detail=(
            f"SAR_FILE downgraded to REVIEW. evidence={evidence_count} "
            f"(need >= {MIN_EVIDENCE_FOR_SAR}); confidence={confidence:.2f} "
            f"(need > {MIN_CONFIDENCE_FOR_SAR})"
        ),
        investigation_id=investigation_id,
        payload={
            "original_action": "SAR_FILE",
            "downgraded_to": "REVIEW",
            "evidence_count": evidence_count,
            "confidence": confidence,
            "min_evidence": MIN_EVIDENCE_FOR_SAR,
            "min_confidence": MIN_CONFIDENCE_FOR_SAR,
        },
    )
    # Downgrade to REVIEW. Adjust risk_tier downward if it was CRITICAL.
    new_tier = case_file.risk_tier
    if new_tier == SeverityTier.CRITICAL:
        new_tier = SeverityTier.HIGH
    return case_file.model_copy(
        update={
            "recommended_action": RecommendedAction.REVIEW,
            "sar_ready": False,
            "risk_tier": new_tier,
        }
    )
