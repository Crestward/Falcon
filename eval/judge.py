"""LLM-as-judge for evidence faithfulness and hallucination scoring.

Per plan §3.3:
  - Judge is NEVER the same model as the agents (Haiku grading Haiku = no signal).
  - Judge is preferably on a different vendor (cross-vendor neutrality).
  - Judge backend is `JUDGE_BACKEND`, completely independent of `LLM_BACKEND`.
  - Recommended: Vertex Gemini 2.5 Pro. Fallback: Anthropic Opus 4.7
    (defensible on capability-gap grounds even though it's co-vendor).
  - Do NOT use Sonnet 4.6 as judge when agents are also Sonnet (co-vendor +
    co-model).

The judge returns scores per claim, which we aggregate into
  - faithfulness_score: 0..1 (mean of per-claim support 0/0.5/1)
  - hallucination_rate: 0..1 (fraction of claims with support 0)

This module ONLY talks to the LLM. The harness is the only caller — the
scorer module is judge-free so it stays unit-testable without keys.
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from agents.llm_utils import _strip_to_json
from core.llm_factory import get_llm
from core.settings import get_settings


class ClaimScore(BaseModel):
    claim: str
    support: int = Field(..., ge=0, le=2, description="0=unsupported, 1=partial, 2=fully supported")
    note: str = ""


class JudgeVerdict(BaseModel):
    claims: list[ClaimScore] = Field(default_factory=list)


JUDGE_SYSTEM = """You are an independent evaluator for a fraud-investigation case file.

For each factual CLAIM in the case file (evidence items, executive summary
sentences, suspicion grounds), decide whether it is supported by the
INVESTIGATION DATA provided.

Score each claim with an integer:
  0 = NOT supported (no evidence in the data, or contradicted by it)
  1 = PARTIALLY supported (related signal present, but specifics not exact)
  2 = FULLY supported (claim is directly evidenced by the data)

Be strict. If a case file says "device fingerprint changed" but the data
shows the same device for 90 days, that is 0, not 1.

Respond with ONE JSON object only:
  {"claims": [{"claim": "...", "support": 0|1|2, "note": "..."}, ...]}

Do not include any prose or markdown fences. Do not invent claims; only score
ones present in the case file.
"""


def _format_investigation_data(
    *,
    typology: str | None,
    confidence: float,
    profiles: list[dict[str, Any]],
    network: dict[str, Any] | None,
) -> str:
    parts = [
        f"PRIMARY TYPOLOGY: {typology or 'NONE'}",
        f"FINAL CONFIDENCE: {confidence:.3f}",
        "",
        f"ACCOUNT PROFILES ({len(profiles)} accounts):",
    ]
    for p in profiles:
        parts.append(
            f"  - {p.get('account_id')}: anomalies={len(p.get('anomalies', []))}, "
            f"flagged_tx={len(p.get('flagged_transaction_ids', []))}, "
            f"counterparties={len(p.get('counterparty_account_ids', []))}"
        )
    if network:
        nodes = network.get("nodes", [])
        edges = network.get("edges", [])
        parts.append("")
        parts.append(f"NETWORK: {len(nodes)} nodes, {len(edges)} edges")
        for n in nodes[:20]:
            parts.append(f"  - node {n.get('account_id')} risk={n.get('risk_score')} role={n.get('role')}")
        for e in edges[:30]:
            parts.append(
                f"  - edge {e.get('source')}->{e.get('target')} "
                f"type={e.get('relationship_type')} src={e.get('source_type')}"
            )
    return "\n".join(parts)


def _format_case_file(case: dict[str, Any]) -> str:
    parts = [
        f"RISK TIER: {case.get('risk_tier')}",
        f"RECOMMENDED ACTION: {case.get('recommended_action')}",
        f"EXECUTIVE SUMMARY: {case.get('executive_summary', '')}",
        f"SUSPICION GROUNDS: {case.get('suspicion_grounds', '')}",
        "",
        "EVIDENCE CHAIN:",
    ]
    for e in case.get("evidence_chain", []):
        parts.append(f"  - [{e.get('evidence_type')}] {e.get('summary')}")
    return "\n".join(parts)


def judge_case(
    *,
    case_file: dict[str, Any],
    typology: str | None,
    confidence: float,
    profiles: list[dict[str, Any]],
    network: dict[str, Any] | None,
) -> dict[str, Any]:
    """Score a case file. Returns dict with faithfulness, hallucination, raw claims.

    Fails soft: if the judge call or parse fails, returns nulls — the eval run
    must continue. The harness records the failure in `notes`.
    """
    settings = get_settings()
    user_prompt = (
        "CASE FILE:\n"
        + _format_case_file(case_file)
        + "\n\n----- INVESTIGATION DATA -----\n"
        + _format_investigation_data(
            typology=typology, confidence=confidence, profiles=profiles, network=network
        )
        + "\n\nReturn the JSON object now."
    )

    try:
        llm = get_llm("judge")
        msgs = [SystemMessage(content=JUDGE_SYSTEM), HumanMessage(content=user_prompt)]
        response = llm.invoke(msgs)
        raw = response.content
        if isinstance(raw, list):
            raw = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in raw
            )
        payload = json.loads(_strip_to_json(raw))
        verdict = JudgeVerdict.model_validate(payload)
    except (Exception, ValidationError) as e:  # noqa: BLE001
        return {
            "faithfulness_score": None,
            "hallucination_rate": None,
            "claims": [],
            "judge_backend": settings.judge_backend,
            "judge_model": _judge_model_name(),
            "note": f"judge_failed: {type(e).__name__}: {e}",
        }

    if not verdict.claims:
        return {
            "faithfulness_score": None,
            "hallucination_rate": None,
            "claims": [],
            "judge_backend": settings.judge_backend,
            "judge_model": _judge_model_name(),
            "note": "judge returned no claims",
        }

    supports = [c.support for c in verdict.claims]
    faithfulness = sum(s / 2.0 for s in supports) / len(supports)
    hallucination = sum(1 for s in supports if s == 0) / len(supports)

    return {
        "faithfulness_score": round(faithfulness, 3),
        "hallucination_rate": round(hallucination, 3),
        "claims": [c.model_dump() for c in verdict.claims],
        "judge_backend": settings.judge_backend,
        "judge_model": _judge_model_name(),
        "note": None,
    }


def _judge_model_name() -> str:
    s = get_settings()
    if s.judge_backend == "vertex":
        return s.vertex_model_judge
    if s.judge_backend == "anthropic":
        return s.anthropic_model_judge
    return s.bedrock_model_judge


def judge_neutrality_flag() -> dict[str, Any]:
    """Implements the §3.3 'sanity check' rule: if the agent backend and judge
    backend are the same vendor, the README must say so explicitly. We
    surface a structured flag so the dashboard renders the disclosure."""
    s = get_settings()
    same_vendor = s.llm_backend == s.judge_backend
    return {
        "agent_backend": s.llm_backend,
        "judge_backend": s.judge_backend,
        "same_vendor": same_vendor,
        "disclosure": (
            "Judge runs the same vendor as the agents — capability-gap defence "
            "applies (judge model is strictly stronger than the agent models)."
            if same_vendor
            else "Judge runs on a different vendor than the agents — cross-vendor neutrality."
        ),
    }
