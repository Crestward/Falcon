"""watchlist-mcp queries.

PEP/SANCTIONS lookup is on **person/entity name**, not account id — accounts
have no PEP status, people do (plan §2.1).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from core.db import session_scope
from core.models import WatchlistEntity


def lookup(name: str, country: str | None = None) -> list[dict[str, Any]]:
    """Case-insensitive name match, optionally filtered by ISO-3166-α2 country."""
    if not name or not name.strip():
        return []
    with session_scope() as s:
        stmt = select(WatchlistEntity).where(
            WatchlistEntity.name.ilike(f"%{name.strip()}%")
        )
        if country:
            stmt = stmt.where(WatchlistEntity.country == country.upper())
        rows = s.execute(stmt.limit(20)).scalars().all()
        return [
            {
                "id": r.id,
                "name": r.name,
                "list_type": r.list_type,
                "country": r.country,
                "risk_score": r.risk_score,
                "metadata": dict(r.metadata_json or {}),
            }
            for r in rows
        ]
