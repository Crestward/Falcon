"""Confidence formula. Phase 2 (CHECKPOINT_4) version.

Inputs:
  base from triage severity     (LOW .2, MEDIUM .4, HIGH .6, CRITICAL .8)
  + 0.10 if any anomalies across all profiles
  + 0.10 if network expansion fired
  + min(0.20, 0.02 * total_flagged_transactions)
  + 0.25 * typology.primary_score       (Pattern Hunter contribution)
  - contradiction.confidence_penalty   (capped at 0.3)
  clamped to [0, 1]

The Pattern Hunter contribution is what makes CHECKPOINT_4 routing more
meaningful than CHECKPOINT_3's interim score — typology-grounded evidence
moves the needle on confidence in a defensible way.
"""
from __future__ import annotations

from core.schemas import (
    AccountProfile,
    ContradictionReport,
    NetworkGraph,
    SeverityTier,
    TriageAssessment,
    TypologyAssessment,
)

_SEVERITY_BASE: dict[SeverityTier, float] = {
    SeverityTier.LOW: 0.2,
    SeverityTier.MEDIUM: 0.4,
    SeverityTier.HIGH: 0.6,
    SeverityTier.CRITICAL: 0.8,
}


def compute_confidence(
    triage: TriageAssessment,
    profiles: dict[str, AccountProfile],
    network: NetworkGraph | None,
    typology: TypologyAssessment | None = None,
    contradictions: ContradictionReport | None = None,
) -> float:
    score = _SEVERITY_BASE[triage.severity]

    if any(len(p.anomalies) > 0 for p in profiles.values()):
        score += 0.10

    if network is not None and network.expansion_request.trigger:
        score += 0.10

    total_flagged = sum(len(p.flagged_transaction_ids) for p in profiles.values())
    score += min(0.20, 0.02 * total_flagged)

    if typology is not None:
        score += 0.25 * typology.primary_score

    if contradictions is not None:
        score -= contradictions.confidence_penalty

    return round(max(0.0, min(1.0, score)), 3)
