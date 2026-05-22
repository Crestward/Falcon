"""watchlist-mcp — Phase 1."""
from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from core.settings import get_settings
from mcp_servers.watchlist import queries

mcp = FastMCP("watchlist-mcp")


@mcp.tool()
def health() -> dict[str, str]:
    return {"status": "ok", "service": "watchlist-mcp"}


@mcp.tool()
def lookup(name: str, country: str | None = None) -> list[dict[str, Any]]:
    """PEP/sanctions name lookup. Returns up to 20 matches."""
    return queries.lookup(name, country=country)


def main() -> None:
    settings = get_settings()
    mcp.run(transport="streamable-http", host="0.0.0.0", port=settings.mcp_watchlist_port)


if __name__ == "__main__":
    main()
