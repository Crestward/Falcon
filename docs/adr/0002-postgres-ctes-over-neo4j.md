# ADR-002 — PostgreSQL recursive CTEs over Neo4j

**Status:** Accepted · 2026-04-30

## Context

The Network Mapper traverses account-to-account relationships up to two
hops. The natural fit for graph traversal is a graph database — Neo4j
being the obvious option. But everything else FALCON does (transactions,
investigation state, evaluation results, audit logs) lives in Postgres.

## Decision

PostgreSQL recursive CTEs against a single `account_network_edges`
table, plus runtime-derived edges from transactions and shared
addresses.

## Why

The traversal pattern is shallow — two hops, dozens of nodes per case,
not millions. Postgres handles it comfortably and the query plan is
something a DBA on the bank's team can read.

Adding Neo4j would have meant a second database with its own backup
story, its own monitoring, its own ETL to keep the graph and the
transactions in sync. The benefit would have been Cypher syntax for
queries, which reads nicely but doesn't justify the operational cost
for a system this size.

Single source of truth was the bigger win. Every piece of state — graph
edges, agent decisions, case files, eval results — sits in one
Postgres instance with one backup and one set of credentials. A
recruiter cloning the repo runs `docker compose up` and gets the whole
data layer in one container.

## Consequences

- Network Mapper's recursive CTE `UNION ALL`s three edge sources
  (declared edges, transaction-derived edges, address-derived edges).
  The query is in `mcp_servers/network_graph/queries.py` and is the
  single piece of "graph algorithm" code in the system.
- If a future scenario needs deeper traversal (10+ hops, large
  fan-out, centrality computation), we'd reach for Neo4j or NetworkX
  at that point. The MCP boundary means the swap is local — agents
  call the same `traverse_network` tool either way.
- pgvector gets to ride along for the fraud-pattern embeddings.
  Two extensions, one database.
