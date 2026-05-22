"""Recursive-CTE graph traversal backing network-graph-mcp.

UNIONs three edge sources at query time (plan §1.3):
  1. account_network_edges      — declared/derived edges from data.generate
  2. transactions.counterparty  — derived "money flowed A→B" edges
  3. accounts.holder_address    — derived "shared physical address" edges
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from core.db import session_scope

_TRAVERSE_SQL = text(
    """
WITH RECURSIVE edges AS (
    -- declared/derived edges already stored
    SELECT source_account_id AS src, target_account_id AS tgt,
           relationship_type, source_type, weight
    FROM account_network_edges
    UNION ALL
    SELECT target_account_id AS src, source_account_id AS tgt,
           relationship_type, source_type, weight
    FROM account_network_edges

    UNION ALL
    -- transaction-derived edges (any account that sent or received money)
    SELECT account_id AS src, counterparty_account_id AS tgt,
           'transacted_with' AS relationship_type, 'derived' AS source_type,
           CAST(0.5 AS float) AS weight
    FROM transactions
    WHERE counterparty_account_id IS NOT NULL

    UNION ALL
    -- shared-address self-join (only when address is non-null and shared)
    SELECT a.id AS src, b.id AS tgt,
           'shared_address' AS relationship_type, 'derived' AS source_type,
           CAST(0.7 AS float) AS weight
    FROM accounts a
    JOIN accounts b
      ON a.holder_address = b.holder_address
     AND a.id <> b.id
    WHERE a.holder_address IS NOT NULL
),
walk AS (
    SELECT CAST(:seed AS text) AS account_id, 0 AS hop,
           ARRAY[CAST(:seed AS text)] AS path
    UNION ALL
    SELECT e.tgt, walk.hop + 1, walk.path || e.tgt
    FROM walk
    JOIN edges e ON e.src = walk.account_id
    WHERE walk.hop < :max_hops
      AND NOT (e.tgt = ANY(walk.path))     -- prevent cycles
)
SELECT DISTINCT account_id, MIN(hop) AS hop
FROM walk
GROUP BY account_id
ORDER BY hop ASC, account_id ASC;
"""
)


_EDGES_SQL = text(
    """
WITH nodes AS (SELECT UNNEST(CAST(:ids AS text[])) AS id)
SELECT * FROM (
    SELECT source_account_id AS src, target_account_id AS tgt,
           relationship_type, source_type, CAST(weight AS float) AS weight
    FROM account_network_edges
    WHERE source_account_id IN (SELECT id FROM nodes)
      AND target_account_id IN (SELECT id FROM nodes)
    UNION ALL
    SELECT account_id AS src, counterparty_account_id AS tgt,
           'transacted_with' AS relationship_type, 'derived' AS source_type,
           CAST(0.5 AS float) AS weight
    FROM transactions
    WHERE account_id IN (SELECT id FROM nodes)
      AND counterparty_account_id IN (SELECT id FROM nodes)
    UNION ALL
    SELECT a.id AS src, b.id AS tgt,
           'shared_address' AS relationship_type, 'derived' AS source_type,
           CAST(0.7 AS float) AS weight
    FROM accounts a
    JOIN accounts b
      ON a.holder_address = b.holder_address AND a.id <> b.id
    WHERE a.id IN (SELECT id FROM nodes)
      AND b.id IN (SELECT id FROM nodes)
      AND a.holder_address IS NOT NULL
) e;
"""
)


def traverse_network(seed_account_id: str, max_hops: int = 2) -> dict[str, Any]:
    """Return reachable nodes + edges within `max_hops` of `seed_account_id`."""
    with session_scope() as s:
        node_rows = s.execute(
            _TRAVERSE_SQL, {"seed": seed_account_id, "max_hops": max_hops}
        ).all()
        node_ids = [r.account_id for r in node_rows]
        if not node_ids:
            return {"nodes": [], "edges": []}
        edge_rows = s.execute(_EDGES_SQL, {"ids": node_ids}).all()
        # Per-node degree as a crude centrality proxy
        deg: dict[str, int] = {nid: 0 for nid in node_ids}
        edges = []
        seen_edge: set[tuple[str, str, str]] = set()
        for e in edge_rows:
            key = tuple(sorted([e.src, e.tgt]) + [e.relationship_type])
            if key in seen_edge:
                continue
            seen_edge.add(key)
            deg[e.src] = deg.get(e.src, 0) + 1
            deg[e.tgt] = deg.get(e.tgt, 0) + 1
            edges.append(
                {
                    "source": e.src,
                    "target": e.tgt,
                    "relationship_type": e.relationship_type,
                    "source_type": e.source_type,
                    "weight": float(e.weight),
                }
            )

        max_deg = max(deg.values()) if deg else 1
        nodes = [
            {
                "account_id": nid,
                "risk_score": round(deg[nid] / max_deg, 3) if max_deg else 0.0,
                "role": "hub" if deg[nid] >= 0.6 * max_deg and max_deg >= 3 else "leaf",
                "hop": next(r.hop for r in node_rows if r.account_id == nid),
            }
            for nid in node_ids
        ]
        return {"nodes": nodes, "edges": edges}
