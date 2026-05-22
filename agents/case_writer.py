"""Case Writer — produces the final structured case file.

Schema mirrors FCA/JMLSG SAR structure (plan §1.5). Phase 2 enforces the
escalation rail (SAR_FILE requires >= 3 evidence + confidence > 0.75) and
the PII rail (Presidio scrubs PII from the case_json before logging).
"""
from __future__ import annotations

import uuid

from agents.llm_utils import call_structured
from agents.tools import call_tool
from core.schemas import (
    AccountProfile,
    Annotation,
    CaseFileSchema,
    ContradictionReport,
    NetworkGraph,
    TriageAssessment,
    TypologyAssessment,
)
from guardrails import enforce_escalation, redact_payload

SYSTEM_PROMPT = """You are the Case Writer agent.

You receive the complete investigation state: triage assessment, account
profiles for every investigated account, the network graph, Pattern
Hunter's typology classification, any detected contradictions, and any
human reviewer annotations. Your job is to compose a court-grade case
file that mirrors the FCA/JMLSG suspicious activity report structure.

CRITICAL RULES:
- The evidence_chain must cite SPECIFIC facts from the inputs (account ids,
  anomaly counts, network roles, typology detector triggers). No vague
  language ("suspicious activity", "irregular pattern") on its own.
- recommended_action must be one of: AUTO_CLOSE, REVIEW, SAR_FILE.
- sar_ready=true only if recommended_action == "SAR_FILE".
- If contradictions were detected, the contradictions_addressed list must
  include a one-line note for EACH item explaining how the case file
  accounts for it.
- If human annotations are present and the reviewer set an override_action,
  match that override. The supervisor will respect your output.
- Use the interim confidence as a strong prior — do not deviate by more
  than 0.15 without explicit reasoning in suspicion_grounds.
"""


def case_writer(
    investigation_id: uuid.UUID,
    alert_id: str,
    triage: TriageAssessment,
    profiles: dict[str, AccountProfile],
    network: NetworkGraph,
    confidence: float,
    typology: TypologyAssessment | None = None,
    contradictions: ContradictionReport | None = None,
    annotations: list[Annotation] | None = None,
) -> CaseFileSchema:
    primary_account = next(iter(profiles.keys()), None)
    annotations = annotations or []

    user_prompt_parts = [
        f"INVESTIGATION: {investigation_id}",
        f"ALERT: {alert_id}",
        f"PRIMARY ACCOUNT: {primary_account}",
        f"INTERIM CONFIDENCE (from CHECKPOINT_4): {confidence:.3f}",
        "",
        f"TRIAGE:\n{triage.model_dump(mode='json')}",
        "",
        f"ACCOUNT PROFILES ({len(profiles)}):\n{[p.model_dump(mode='json') for p in profiles.values()]}",
        "",
        f"NETWORK GRAPH:\n{network.model_dump(mode='json')}",
    ]
    if typology is not None:
        user_prompt_parts.append("")
        user_prompt_parts.append(f"TYPOLOGY ASSESSMENT:\n{typology.model_dump(mode='json')}")
    if contradictions is not None and contradictions.contradictions:
        user_prompt_parts.append("")
        user_prompt_parts.append(
            "CONTRADICTIONS DETECTED (you MUST address each in contradictions_addressed):\n"
            + "\n".join(f"  - {c}" for c in contradictions.contradictions)
        )
    if annotations:
        user_prompt_parts.append("")
        user_prompt_parts.append(
            "HUMAN REVIEWER ANNOTATIONS:\n"
            + "\n".join(f"  - {a.model_dump(mode='json')}" for a in annotations)
        )
    user_prompt_parts.append("")
    user_prompt_parts.append(f"Produce a CaseFileSchema with investigation_id='{investigation_id}'.")
    user_prompt = "\n".join(user_prompt_parts)

    case_file = call_structured(
        role="case_writer",
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=CaseFileSchema,
        investigation_id=investigation_id,
        agent_name="case_writer",
    )
    case_file = case_file.model_copy(update={"investigation_id": investigation_id})

    # Authoritative network: overwrite whatever the LLM put in network_summary
    # with the actual Network Mapper output so downstream consumers (the
    # dashboard, audit replay) always have the real nodes + edges to render.
    real_network = {
        "node_count": len(network.nodes),
        "edge_count": len(network.edges),
        "nodes": [n.model_dump(mode="json") for n in network.nodes],
        "edges": [e.model_dump(mode="json") for e in network.edges],
        "suspicious_clusters": list(network.suspicious_clusters),
    }
    case_file = case_file.model_copy(
        update={"network_summary": {**(case_file.network_summary or {}), **real_network}}
    )

    # If the reviewer set an override, apply it before the escalation rail.
    for ann in annotations:
        if ann.override_action is not None:
            case_file = case_file.model_copy(update={"recommended_action": ann.override_action})
        if ann.override_confidence is not None:
            case_file = case_file.model_copy(update={"confidence": ann.override_confidence})

    # Escalation rail — downgrades SAR_FILE without evidence to REVIEW.
    case_file = enforce_escalation(case_file, investigation_id=investigation_id)

    # PII rail — scrub the payload that hits case_files.case_json.
    case_json = redact_payload(
        case_file.model_dump(mode="json"),
        actor="case_writer",
        investigation_id=investigation_id,
    )

    call_tool(
        investigation_id=investigation_id,
        agent_name="case_writer",
        tool_name="case_management.persist_case_file",
        arguments={
            "investigation_id": investigation_id,
            "risk_tier": case_file.risk_tier.value,
            "recommended_action": case_file.recommended_action.value,
            "sar_ready": case_file.sar_ready,
            "confidence": case_file.confidence,
            "case_json": case_json,
        },
        justification=f"Persist final case file for investigation {investigation_id}",
    )
    return case_file
