"""Pattern Hunter — typology classification with evidence mapping.

Detector logic is pure-Python in `agents/detectors.py` (Pattern Hunter does
not ask an LLM to compute graph centrality or count anomalies; that would
be unreliable). The LLM's only job is to write a coherent rationale tying
detector evidence to a primary typology. Deterministic outputs from the
detectors are spliced over the LLM's response post-hoc — same pattern as
Account Historian.
"""
from __future__ import annotations

import uuid

from agents.detectors import pick_primary, run_all_detectors
from agents.llm_utils import call_structured
from core.schemas import (
    AccountProfile,
    NetworkGraph,
    TypologyAssessment,
)
from mcp_servers.case_management import queries as case_q

SYSTEM_PROMPT = """You are the Pattern Hunter agent.

Five typology detectors have already been run for you in Python. Each
produced a score in [0,1] and concrete evidence strings. Your job is to
write a coherent rationale that ties the highest-scoring typology to its
evidence, and return a `TypologyAssessment`.

You MUST NOT change the detector scores or the primary typology — those
are deterministic. You may add interpretive context in `rationale`.

If every detector scored below 0.2, the primary typology is NONE; explain
that the network and behavioural signals available do not unambiguously
match any of the five typologies and recommend further evidence collection.

Your rationale should:
  - Name the primary typology and cite its detector evidence.
  - Briefly note any near-second typology if the gap is < 0.2 (it may
    matter for the reconciliation step).
  - Stay under 600 words.
"""


def pattern_hunter(
    profiles: dict[str, AccountProfile],
    network: NetworkGraph,
    investigation_id: uuid.UUID,
) -> TypologyAssessment:
    matches = run_all_detectors(profiles, network, investigation_id)
    primary = pick_primary(matches)

    user_prompt = (
        "DETECTOR RESULTS (scores 0-1, higher = stronger):\n"
        + "\n".join(
            f"  - {m.typology.value}: score={m.score:.2f}, "
            f"detectors={m.triggered_detectors}, evidence={m.evidence}"
            for m in matches
        )
        + f"\n\nDETERMINISTIC PRIMARY: {primary.typology.value} "
        f"(score {primary.score:.2f})\n\n"
        "Produce a TypologyAssessment. primary_typology and primary_score "
        f"MUST equal {primary.typology.value} and {primary.score:.2f}. "
        "Include all five matches in the `matches` list."
    )

    assessment = call_structured(
        role="pattern_hunter",
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=TypologyAssessment,
        investigation_id=investigation_id,
        agent_name="pattern_hunter",
    )
    # Authoritative: detector results override anything the LLM said.
    assessment = assessment.model_copy(
        update={
            "primary_typology": primary.typology,
            "primary_score": primary.score,
            "matches": matches,
        }
    )

    case_q.record_decision(
        investigation_id=investigation_id,
        agent_name="pattern_hunter",
        decision_type="TYPOLOGY_ASSESSMENT",
        decision_payload=assessment.model_dump(mode="json"),
        justification=f"Primary typology: {primary.typology.value} (score {primary.score:.2f})",
    )
    return assessment
