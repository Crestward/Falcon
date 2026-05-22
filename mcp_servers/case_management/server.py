"""case-management-mcp — Phase 1."""
from __future__ import annotations

import uuid
from typing import Any

from fastmcp import FastMCP

from core.settings import get_settings
from mcp_servers.case_management import queries

mcp = FastMCP("case-management-mcp")


@mcp.tool()
def health() -> dict[str, str]:
    return {"status": "ok", "service": "case-management-mcp"}


@mcp.tool()
def persist_case_file(
    investigation_id: str,
    risk_tier: str,
    recommended_action: str,
    sar_ready: bool,
    confidence: float,
    case_json: dict[str, Any],
) -> str:
    case_id = queries.persist_case_file(
        uuid.UUID(investigation_id),
        risk_tier,
        recommended_action,
        sar_ready,
        confidence,
        case_json,
    )
    return str(case_id)


@mcp.tool()
def get_case(investigation_id: str) -> dict[str, Any] | None:
    return queries.get_case(uuid.UUID(investigation_id))


def main() -> None:
    settings = get_settings()
    mcp.run(transport="streamable-http", host="0.0.0.0", port=settings.mcp_case_management_port)


if __name__ == "__main__":
    main()
