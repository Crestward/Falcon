# ADR-003 — FastMCP as the tool interface layer

**Status:** Accepted · 2026-04-30

## Context

Agents need to call tools — query transactions, look up watchlists,
traverse the network, persist case files. The shape of that interface
matters for security (every call must be auditable) and for cloud
portability (the bank will want to move these to AWS Lambdas or GKE
services).

## Decision

Four FastMCP servers, one per tool family. Each is a separate Python
process exposing tools over HTTP using the Model Context Protocol.

## Why

MCP is the standard Anthropic and the broader ecosystem are
converging on. It gives us a clean separation between *the agent*
and *the tool*: agents don't import database libraries, they call
named tools over HTTP.

In dev, the agent process calls the MCP query functions directly
(in-process) for speed. In production, every tool call goes over
HTTP to a separately-scaled service. The seam is at
`agents/tools.py` — switching from in-process to HTTP is a
five-line change.

The four servers map naturally to the bank's existing IT structure:
the transaction store team owns one MCP, the network analytics team
owns another, the case management team owns the third, sanctions
ops owns the fourth. They can deploy them independently, version
them independently, and rotate credentials independently.

## Consequences

- Every tool call is justified, logged, and scope-checked. The
  justification rail wraps every call before dispatch; the scope
  rail blocks Network Mapper from looking at out-of-scope accounts.
- The MCP servers all share `core/` models — they're not fully
  independent processes, they share a Python package. A real-world
  deploy would split them into separate repos, but for portfolio
  purposes the monorepo is clearer.
- We picked FastMCP (the Python lib) over the official MCP SDK
  because FastMCP's decorator API is closer to FastAPI's, which
  every Python developer already knows. The wire protocol is the
  same.
