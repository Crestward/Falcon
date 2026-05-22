"""Contradiction detection at CHECKPOINT_4 step 1.

Deterministic comparisons between Pattern Hunter's classification and what
Account Historian / Network Mapper already established. Per plan §2.4:

  - Pattern Hunter says STRUCTURING -> Historian shows consistent business
    deposit pattern -> CONTRADICTION_DETECTED
  - Pattern Hunter says ACCOUNT_TAKEOVER -> Network shows long-running
    shared device fingerprint -> CONTRADICTION_DETECTED

Contradictions don't kill the investigation. They produce a report Case
Writer must address explicitly. They also dock confidence.
"""
from __future__ import annotations

from core.schemas import (
    AccountProfile,
    ContradictionReport,
    FraudTypology,
    NetworkGraph,
    TypologyAssessment,
)

_CONTRA_PENALTY = 0.10


def detect_contradictions(
    typology: TypologyAssessment,
    profiles: dict[str, AccountProfile],
    network: NetworkGraph,
) -> ContradictionReport:
    contradictions: list[str] = []

    # 1. STRUCTURING but the account looks like a stable business depositor.
    if typology.primary_typology == FraudTypology.STRUCTURING:
        for account_id, p in profiles.items():
            base = p.baseline if isinstance(p.baseline, dict) else {}
            channels = base.get("channel_mix", {})
            cash_count = channels.get("cash", 0) if isinstance(channels, dict) else 0
            total_txns = base.get("transaction_count", 0)
            anomalies = len(p.anomalies)
            # Business pattern: high txn count, low anomaly density, cash NOT dominant.
            if total_txns >= 60 and anomalies <= 1 and cash_count < 5:
                contradictions.append(
                    f"STRUCTURING flagged but {account_id} shows {total_txns} txns "
                    f"with only {anomalies} anomalies and minimal cash ({cash_count}) — "
                    f"consistent with business activity, not structuring"
                )
                break

    # 2. ACCOUNT_TAKEOVER but network shows long-stable shared device.
    if typology.primary_typology == FraudTypology.ACCOUNT_TAKEOVER and network.edges:
        device_edges = [
            e for e in network.edges if e.relationship_type == "shared_device"
        ]
        if device_edges:
            contradictions.append(
                f"ACCOUNT_TAKEOVER flagged but network includes "
                f"{len(device_edges)} shared-device edges — same device "
                f"fingerprint across multiple accounts suggests stable "
                f"coordinated control, not a fresh takeover"
            )

    # 3. PEP_EXPOSURE but no watchlist hit in the typology evidence.
    if typology.primary_typology == FraudTypology.PEP_EXPOSURE:
        pep_match = next(
            (m for m in typology.matches if m.typology == FraudTypology.PEP_EXPOSURE),
            None,
        )
        if pep_match and "watchlist_hit" not in pep_match.triggered_detectors:
            contradictions.append(
                "PEP_EXPOSURE flagged primary but no watchlist_hit detector "
                "triggered — classification rests on weak signal only"
            )

    # 4. LAYERING but the discovered network is dense (high max degree),
    #    not chain-like.
    if typology.primary_typology == FraudTypology.LAYERING and network.edges:
        from collections import defaultdict

        deg: dict[str, int] = defaultdict(int)
        for e in network.edges:
            deg[e.source] += 1
            deg[e.target] += 1
        if deg and max(deg.values()) >= 5:
            contradictions.append(
                f"LAYERING flagged but network has hub-degree "
                f"{max(deg.values())} — layering typically presents as a "
                f"chain with max degree 2-3, not a hub"
            )

    penalty = _CONTRA_PENALTY * len(contradictions) if contradictions else 0.0
    return ContradictionReport(
        contradictions=contradictions,
        confidence_penalty=min(0.3, penalty),
    )
