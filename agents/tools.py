"""Agent-side tool wrappers.

Every wrapper:
  1. validates the call as a `JustifiedToolCall` (justification rail)
  2. invokes the underlying query function
  3. writes a row to `tool_call_logs` for audit

In Phase 1 the wrappers call queries.py directly (in-process) — same Python
function the MCP server exposes over HTTP. This keeps the hot path fast on
local Ollama while preserving the architecture: every "tool call" is logged,
justified, and points at a named MCP server.

Phase 2 will add the scope rail (`@enforce_scope`) on top of this layer.
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from typing import Any

from core.db import session_scope
from core.models import ToolCallLog
from core.schemas import JustifiedToolCall
from guardrails import enforce_justification
from mcp_servers.case_management import queries as case_q
from mcp_servers.network_graph import queries as network_q
from mcp_servers.transaction_store import queries as txn_q
from mcp_servers.watchlist import queries as wl_q


def _log(
    investigation_id: uuid.UUID | None,
    agent_name: str,
    tool_name: str,
    arguments: dict[str, Any],
    justification: str,
    result: Any,
    latency_ms: int,
) -> None:
    if investigation_id is None:
        return  # no-op for ad-hoc/test calls outside an investigation
    summary = _summarise(result)
    # JSONB column — round-trip via json.dumps(default=str) to coerce UUIDs etc.
    safe_args = json.loads(json.dumps(arguments, default=str))
    with session_scope() as s:
        s.add(
            ToolCallLog(
                investigation_id=investigation_id,
                agent_name=agent_name,
                tool_name=tool_name,
                arguments=safe_args,
                justification=justification,
                result_summary=summary,
                latency_ms=latency_ms,
            )
        )


def _summarise(result: Any) -> str:
    if isinstance(result, list):
        return f"list[{len(result)}]"
    if isinstance(result, dict):
        return "dict{" + ",".join(sorted(result.keys())[:6]) + "}"
    return str(result)[:200]


def _dispatch(call: JustifiedToolCall) -> Any:
    """Look up the underlying query function for a tool name."""
    table: dict[str, Callable[..., Any]] = {
        "transaction_store.get_quick_signals": txn_q.get_quick_signals,
        "transaction_store.get_history": txn_q.get_history,
        "transaction_store.semantic_pattern_search": txn_q.semantic_pattern_search,
        "network_graph.traverse_network": network_q.traverse_network,
        "watchlist.lookup": wl_q.lookup,
        "case_management.persist_case_file": case_q.persist_case_file,
        "case_management.record_decision": case_q.record_decision,
    }
    fn = table.get(call.tool_name)
    if fn is None:
        raise ValueError(f"Unknown tool: {call.tool_name}")
    return fn(**call.arguments)


def call_tool(
    *,
    investigation_id: uuid.UUID | None,
    agent_name: str,
    tool_name: str,
    arguments: dict[str, Any],
    justification: str,
) -> Any:
    """The one entry point for every agent tool call.

    Justification rail: `enforce_justification` constructs the
    `JustifiedToolCall` and writes a `security_events` row if the
    justification is missing or too short. This is the production path
    of the rail — agents always pass a justification, so the rail rarely
    fires here, but when it does the audit log records it correctly.
    """
    call: JustifiedToolCall = enforce_justification(
        tool_name=tool_name,
        arguments=arguments,
        justification=justification,
        actor=agent_name,
        investigation_id=investigation_id,
    )
    started = time.perf_counter()
    result = _dispatch(call)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    _log(investigation_id, agent_name, tool_name, arguments, justification, result, elapsed_ms)
    return result
