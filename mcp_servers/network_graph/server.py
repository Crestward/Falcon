"""network-graph-mcp — Phase 1."""
from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from core.settings import get_settings
from mcp_servers.network_graph import queries

mcp = FastMCP("network-graph-mcp")


@mcp.tool()
def health() -> dict[str, str]:
    return {"status": "ok", "service": "network-graph-mcp"}


@mcp.tool()
def traverse_network(seed_account_id: str, max_hops: int = 2) -> dict[str, Any]:
    """Recursive-CTE traversal across declared, transactional and shared-address edges."""
    return queries.traverse_network(seed_account_id, max_hops=max_hops)


def main() -> None:
    settings = get_settings()
    mcp.run(transport="streamable-http", host="0.0.0.0", port=settings.mcp_network_graph_port)


if __name__ == "__main__":
    main()
