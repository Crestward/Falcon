"""transaction-store-mcp — Phase 1. Wraps queries.py over MCP HTTP."""
from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from core.settings import get_settings
from mcp_servers.transaction_store import queries

mcp = FastMCP("transaction-store-mcp")


@mcp.tool()
def health() -> dict[str, str]:
    return {"status": "ok", "service": "transaction-store-mcp"}


@mcp.tool()
def get_quick_signals(account_id: str, days: int = 7) -> dict[str, Any]:
    """Triage-grade aggregate over a short window."""
    return queries.get_quick_signals(account_id, days=days)


@mcp.tool()
def get_history(account_id: str, days: int = 90) -> list[dict[str, Any]]:
    """Full transaction history for Account Historian."""
    return queries.get_history(account_id, days=days)


@mcp.tool()
def semantic_pattern_search(query_text: str, limit: int = 5) -> list[dict[str, Any]]:
    """Cosine search over fraud-pattern embeddings."""
    return queries.semantic_pattern_search(query_text, limit=limit)


def main() -> None:
    settings = get_settings()
    mcp.run(transport="streamable-http", host="0.0.0.0", port=settings.mcp_transaction_store_port)


if __name__ == "__main__":
    main()
