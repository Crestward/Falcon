"""Network Mapper — recursive graph traversal + cluster identification.

The traversal itself (recursive CTE) is computed in Postgres for speed. The
LLM's only jobs are (a) decide which clusters are suspicious, and (b) decide
whether to ask the supervisor to expand. The expansion request is returned in
state — the supervisor routes on it. See plan §1.3.
"""
from __future__ import annotations

import uuid
from typing import Any

from agents.llm_utils import call_structured
from agents.tools import call_tool
from core.schemas import (
    ExpansionRequest,
    NetworkEdge,
    NetworkGraph,
    NetworkNode,
)
from guardrails import assert_in_scope
from mcp_servers.case_management import queries as case_q

_EXPANSION_THRESHOLD_NODES = 3
_EXPANSION_THRESHOLD_RISK = 0.5  # mean risk_score across discovered nodes


def _materialise_graph(raw: dict[str, Any]) -> tuple[list[NetworkNode], list[NetworkEdge]]:
    nodes = [
        NetworkNode(
            account_id=n["account_id"], risk_score=n["risk_score"], role=n.get("role")
        )
        for n in raw.get("nodes", [])
    ]
    edges = [
        NetworkEdge(
            source=e["source"],
            target=e["target"],
            relationship_type=e["relationship_type"],
            source_type=e["source_type"],
            weight=e["weight"],
        )
        for e in raw.get("edges", [])
    ]
    return nodes, edges


SYSTEM_PROMPT = """You are the Network Mapper agent.

A recursive graph traversal has already been computed for you. Your job:
  1. Identify suspicious clusters — groups of accounts whose collective
     relationship pattern is unusual (e.g. hub-and-spoke, dense rings,
     or many shared-device edges across otherwise unrelated accounts).
  2. Decide whether the supervisor should EXPAND the investigation to cover
     newly discovered accounts. Suggest expansion when the traversal reveals
     accounts that look central to a cluster but are not yet under
     investigation.

You MUST NOT invent accounts. Only use account_ids that appear in the
traversal results.
"""


def network_mapper(
    seed_account_id: str,
    accounts_already_investigated: list[str],
    investigation_id: uuid.UUID,
    max_hops: int = 2,
    scope: list[str] | None = None,
) -> NetworkGraph:
    # Scope rail — Network Mapper can only seed traversal from an account
    # already inside the investigation. `scope` defaults to the accounts
    # already investigated; supervisor passes the full accounts_in_scope set.
    if scope is not None:
        assert_in_scope(
            seed_account_id,
            actor="network_mapper",
            scope=scope,
            investigation_id=investigation_id,
            extra={"max_hops": max_hops},
        )
    raw = call_tool(
        investigation_id=investigation_id,
        agent_name="network_mapper",
        tool_name="network_graph.traverse_network",
        arguments={"seed_account_id": seed_account_id, "max_hops": max_hops},
        justification=f"Traverse {max_hops}-hop neighbourhood of {seed_account_id}",
    )

    nodes, edges = _materialise_graph(raw)
    candidate_new_accounts = [
        n.account_id for n in nodes if n.account_id not in set(accounts_already_investigated)
    ]
    mean_risk = (
        sum(n.risk_score for n in nodes) / len(nodes) if nodes else 0.0
    )

    user_prompt = (
        f"SEED ACCOUNT: {seed_account_id}\n"
        f"MAX HOPS: {max_hops}\n"
        f"ACCOUNTS ALREADY INVESTIGATED: {accounts_already_investigated}\n"
        f"DISCOVERED NODES: {[n.model_dump() for n in nodes]}\n"
        f"DISCOVERED EDGES: {[e.model_dump() for e in edges]}\n"
        f"CANDIDATE NEW ACCOUNTS: {candidate_new_accounts}\n"
        f"MEAN RISK SCORE: {mean_risk:.3f}\n\n"
        "Produce a NetworkGraph. The nodes and edges fields must echo the "
        "discovered values verbatim. Populate suspicious_clusters with "
        "lists of account_ids that form coherent suspicious groupings. "
        "Set expansion_request based on whether the supervisor should "
        "spawn Account Historians for the new accounts."
    )

    graph = call_structured(
        role="network_mapper",
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=NetworkGraph,
        investigation_id=investigation_id,
        agent_name="network_mapper",
    )

    # The LLM is allowed to refine clusters and the expansion rationale, but
    # the deterministic fields (nodes/edges) are trusted from the SQL result.
    expansion = graph.expansion_request
    # Hard-floor: only request expansion if heuristics agree there is enough
    # signal to do so — saves on infinite-loop risk from over-eager LLMs.
    heuristic_expand = (
        len(candidate_new_accounts) >= _EXPANSION_THRESHOLD_NODES
        and mean_risk >= _EXPANSION_THRESHOLD_RISK
    )
    if not heuristic_expand:
        expansion = ExpansionRequest(
            trigger=False,
            new_accounts=[],
            rationale=(
                f"Heuristic floor not met: {len(candidate_new_accounts)} "
                f"candidates (need {_EXPANSION_THRESHOLD_NODES}+), "
                f"mean_risk={mean_risk:.2f} (need {_EXPANSION_THRESHOLD_RISK}+)"
            ),
        )
    else:
        # Constrain to the actually-discovered new accounts, even if the
        # LLM hallucinated others.
        kept = [a for a in expansion.new_accounts if a in candidate_new_accounts]
        if not kept:
            kept = candidate_new_accounts
        expansion = expansion.model_copy(update={"new_accounts": kept, "trigger": True})

    graph = graph.model_copy(
        update={
            "nodes": nodes,
            "edges": edges,
            "expansion_request": expansion,
        }
    )

    case_q.record_decision(
        investigation_id=investigation_id,
        agent_name="network_mapper",
        decision_type="NETWORK_GRAPH",
        decision_payload=graph.model_dump(mode="json"),
        justification=(
            f"{len(nodes)} nodes / {len(edges)} edges; "
            f"expansion={'YES' if expansion.trigger else 'NO'}"
        ),
    )
    return graph
