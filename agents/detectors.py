"""Pure-Python typology detectors backing Pattern Hunter.

Each detector returns a `TypologyMatch` with a 0–1 score and human-readable
evidence strings. Scores are heuristic, not probabilistic — they exist to
rank typologies relative to each other and to give the supervisor a
confidence ingredient at CHECKPOINT_4.

These are not trained models. They are explainable rules that we can defend
in an interview line by line; in production they would be a first-pass
filter before a learned classifier.
"""
from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from typing import Any

from agents.tools import call_tool
from core.db import session_scope
from core.models import Account
from core.schemas import (
    AccountProfile,
    FraudTypology,
    NetworkGraph,
    TypologyMatch,
)

# ----------------------------------------------------------------------------
# STRUCTURING — many transactions clustered just under a round threshold.
# ----------------------------------------------------------------------------


def detect_structuring(profiles: dict[str, AccountProfile]) -> TypologyMatch:
    triggers: list[str] = []
    score = 0.0
    flagged_total = 0
    cash_heavy = False
    for account_id, p in profiles.items():
        flagged = len(p.flagged_transaction_ids)
        flagged_total += flagged
        if flagged >= 5:
            triggers.append(f"{account_id}: {flagged} high-z transactions")
            score += 0.2
        channels = p.baseline.get("channel_mix", {}) if isinstance(p.baseline, dict) else {}
        cash = channels.get("cash", 0) if isinstance(channels, dict) else 0
        total_ch = sum(channels.values()) if isinstance(channels, dict) and channels else 0
        if total_ch > 0 and cash / total_ch >= 0.5 and cash >= 8:
            triggers.append(f"{account_id}: cash channel dominant ({cash}/{total_ch})")
            score += 0.25
            cash_heavy = True
    # Semantic match against the typology corpus reinforces the heuristic.
    for p in profiles.values():
        for m in p.semantic_matches:
            if m.get("typology") == "STRUCTURING" and m.get("distance", 1.0) < 0.4:
                triggers.append(f"semantic match to {m.get('source_scenario_id')}")
                score += 0.2
                break
    return TypologyMatch(
        typology=FraudTypology.STRUCTURING,
        score=min(1.0, score),
        evidence=triggers[:8],
        triggered_detectors=(
            ["high_flagged_count"] if flagged_total >= 5 else []
        ) + (["cash_heavy"] if cash_heavy else []),
    )


# ----------------------------------------------------------------------------
# LAYERING — chain topology in the discovered network.
# ----------------------------------------------------------------------------


def detect_layering(profiles: dict[str, AccountProfile], network: NetworkGraph) -> TypologyMatch:
    if not network.nodes:
        return TypologyMatch(typology=FraudTypology.LAYERING, score=0.0)
    # Chain-like topology: most nodes have degree 1 or 2 (hub degree small).
    deg: dict[str, int] = defaultdict(int)
    for e in network.edges:
        deg[e.source] += 1
        deg[e.target] += 1
    if not deg:
        return TypologyMatch(typology=FraudTypology.LAYERING, score=0.0)
    max_deg = max(deg.values())
    n_nodes = len(network.nodes)
    chain_like = max_deg <= 3 and n_nodes >= 3
    triggers: list[str] = []
    score = 0.0
    if chain_like:
        triggers.append(f"chain topology: {n_nodes} nodes, max degree {max_deg}")
        score += 0.4
    # Look for transacted_with edges (indicates fund flow)
    txn_edges = [e for e in network.edges if e.relationship_type == "transacted_with"]
    if len(txn_edges) >= n_nodes - 1:
        triggers.append(f"{len(txn_edges)} transaction edges across {n_nodes} accounts")
        score += 0.3
    for p in profiles.values():
        for m in p.semantic_matches:
            if m.get("typology") == "LAYERING" and m.get("distance", 1.0) < 0.4:
                triggers.append(f"semantic match to {m.get('source_scenario_id')}")
                score += 0.2
                break
    return TypologyMatch(
        typology=FraudTypology.LAYERING,
        score=min(1.0, score),
        evidence=triggers[:8],
        triggered_detectors=(["chain_topology"] if chain_like else [])
        + (["txn_edge_density"] if len(txn_edges) >= n_nodes - 1 else []),
    )


# ----------------------------------------------------------------------------
# ACCOUNT_TAKEOVER — behavioural shift in a previously stable account.
# ----------------------------------------------------------------------------


def detect_account_takeover(profiles: dict[str, AccountProfile]) -> TypologyMatch:
    triggers: list[str] = []
    score = 0.0
    for account_id, p in profiles.items():
        anomalies = len(p.anomalies)
        if anomalies >= 2:
            triggers.append(f"{account_id}: {anomalies} anomaly windows")
            score += 0.25
        for m in p.semantic_matches:
            if m.get("typology") == "ACCOUNT_TAKEOVER" and m.get("distance", 1.0) < 0.4:
                triggers.append(f"semantic match to {m.get('source_scenario_id')}")
                score += 0.3
                break
    return TypologyMatch(
        typology=FraudTypology.ACCOUNT_TAKEOVER,
        score=min(1.0, score),
        evidence=triggers[:8],
        triggered_detectors=["anomaly_burst"] if score > 0 else [],
    )


# ----------------------------------------------------------------------------
# MULE_NETWORK — shared device/ip across multiple accounts, funnel topology.
# ----------------------------------------------------------------------------


def detect_mule_network(profiles: dict[str, AccountProfile], network: NetworkGraph) -> TypologyMatch:
    if not network.nodes:
        return TypologyMatch(typology=FraudTypology.MULE_NETWORK, score=0.0)
    shared = [
        e for e in network.edges
        if e.relationship_type in ("shared_device", "shared_ip", "shared_address")
    ]
    deg: dict[str, int] = defaultdict(int)
    for e in network.edges:
        deg[e.source] += 1
        deg[e.target] += 1
    n_nodes = len(network.nodes)
    triggers: list[str] = []
    score = 0.0
    if len(shared) >= 3:
        triggers.append(f"{len(shared)} shared-identity edges across cluster")
        score += 0.4
    # Funnel/hub: at least one node with degree >= n_nodes - 1 (touches everyone)
    if deg and max(deg.values()) >= max(2, n_nodes - 1):
        hub = max(deg, key=deg.get)
        triggers.append(f"hub account {hub} (degree {deg[hub]} of {n_nodes - 1} possible)")
        score += 0.3
    for p in profiles.values():
        for m in p.semantic_matches:
            if m.get("typology") == "MULE_NETWORK" and m.get("distance", 1.0) < 0.4:
                triggers.append(f"semantic match to {m.get('source_scenario_id')}")
                score += 0.2
                break
    return TypologyMatch(
        typology=FraudTypology.MULE_NETWORK,
        score=min(1.0, score),
        evidence=triggers[:8],
        triggered_detectors=(["shared_identity"] if len(shared) >= 3 else [])
        + (["hub_topology"] if deg and max(deg.values()) >= max(2, n_nodes - 1) else []),
    )


# ----------------------------------------------------------------------------
# PEP_EXPOSURE — watchlist lookup on each person/entity in the network.
# ----------------------------------------------------------------------------


def detect_pep_exposure(
    profiles: dict[str, AccountProfile],
    network: NetworkGraph,
    investigation_id: uuid.UUID,
) -> TypologyMatch:
    """Watchlist lookup per holder_name + per beneficial_owner_id. The
    watchlist takes (name, country) — never account IDs."""
    account_ids = set(profiles.keys()) | {n.account_id for n in network.nodes}
    if not account_ids:
        return TypologyMatch(typology=FraudTypology.PEP_EXPOSURE, score=0.0)

    with session_scope() as s:
        accounts = s.query(Account).filter(Account.id.in_(list(account_ids))).all()
        # Materialise needed fields out of the session.
        people: list[tuple[str, str | None]] = []
        for a in accounts:
            if a.holder_name:
                people.append((a.holder_name, a.country))
            if a.beneficial_owner_id:
                # In this dataset beneficial_owner_id is a name-like id, not
                # an account FK; treat it as a name string for lookup.
                people.append((a.beneficial_owner_id, a.country))

    triggers: list[str] = []
    score = 0.0
    hits: list[dict[str, Any]] = []
    seen_names: set[tuple[str, str | None]] = set()
    for name, country in people:
        key = (name, country)
        if key in seen_names:
            continue
        seen_names.add(key)
        matches = call_tool(
            investigation_id=investigation_id,
            agent_name="pattern_hunter",
            tool_name="watchlist.lookup",
            arguments={"name": name, "country": country},
            justification=f"PEP screening for network member {name!r}",
        )
        for m in matches:
            if m.get("list_type") in ("PEP", "SANCTIONS"):
                hits.append(m)
                triggers.append(
                    f"{name}: {m['list_type']} watchlist hit ({m.get('name')})"
                )
                score += 0.4 if m.get("list_type") == "PEP" else 0.6
    return TypologyMatch(
        typology=FraudTypology.PEP_EXPOSURE,
        score=min(1.0, score),
        evidence=triggers[:8],
        triggered_detectors=["watchlist_hit"] if hits else [],
    )


# ----------------------------------------------------------------------------
# Aggregator
# ----------------------------------------------------------------------------


def run_all_detectors(
    profiles: dict[str, AccountProfile],
    network: NetworkGraph,
    investigation_id: uuid.UUID,
) -> list[TypologyMatch]:
    return [
        detect_structuring(profiles),
        detect_layering(profiles, network),
        detect_account_takeover(profiles),
        detect_mule_network(profiles, network),
        detect_pep_exposure(profiles, network, investigation_id),
    ]


def pick_primary(matches: list[TypologyMatch]) -> TypologyMatch:
    """Highest-scoring typology wins. If everything is near-zero, return NONE."""
    if not matches:
        return TypologyMatch(typology=FraudTypology.NONE, score=0.0)
    top = max(matches, key=lambda m: m.score)
    if top.score < 0.2:
        return TypologyMatch(
            typology=FraudTypology.NONE,
            score=0.0,
            evidence=["No detector score crossed 0.2 threshold"],
        )
    return top


# silence unused-import warning when Counter helpers aren't reached
_ = Counter
