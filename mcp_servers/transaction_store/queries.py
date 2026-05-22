"""SQL queries backing the transaction-store-mcp tools.

These are the *real* implementations. `server.py` re-exposes them over MCP
HTTP for cross-process clients; agents in this repo call them directly for
speed (no HTTP per call). Either path executes identical SQL.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text

from core.db import session_scope
from core.models import FraudPatternEmbedding, Transaction

# ----------------------------------------------------------------------------
# Quick signals — Triage uses this. 7-day window only, by design.
# ----------------------------------------------------------------------------


def get_quick_signals(account_id: str, days: int = 7) -> dict[str, Any]:
    """Lightweight aggregate for Triage. Single scan over a small window."""
    since = datetime.now(UTC) - timedelta(days=days)
    with session_scope() as s:
        rows = s.execute(
            select(Transaction).where(
                Transaction.account_id == account_id,
                Transaction.timestamp >= since,
            )
        ).scalars().all()

    if not rows:
        return {
            "window_days": days,
            "transaction_count": 0,
            "max_amount": 0.0,
            "distinct_counterparties": 0,
            "channel_mix": {},
            "total_inflow": 0.0,
            "total_outflow": 0.0,
        }

    channels = Counter(t.channel for t in rows)
    counterparties = {t.counterparty_account_id for t in rows if t.counterparty_account_id}
    inflow = sum((t.amount for t in rows if t.direction == "credit"), Decimal(0))
    outflow = sum((t.amount for t in rows if t.direction == "debit"), Decimal(0))
    return {
        "window_days": days,
        "transaction_count": len(rows),
        "max_amount": float(max(t.amount for t in rows)),
        "distinct_counterparties": len(counterparties),
        "channel_mix": dict(channels),
        "total_inflow": float(inflow),
        "total_outflow": float(outflow),
    }


# ----------------------------------------------------------------------------
# Full history — Account Historian uses this.
# ----------------------------------------------------------------------------


def get_history(account_id: str, days: int = 90) -> list[dict[str, Any]]:
    since = datetime.now(UTC) - timedelta(days=days)
    with session_scope() as s:
        rows = s.execute(
            select(Transaction)
            .where(
                Transaction.account_id == account_id,
                Transaction.timestamp >= since,
            )
            .order_by(Transaction.timestamp.asc())
        ).scalars().all()
        return [
            {
                "id": t.id,
                "amount": float(t.amount),
                "direction": t.direction,
                "channel": t.channel,
                "merchant": t.merchant,
                "merchant_category": t.merchant_category,
                "counterparty_account_id": t.counterparty_account_id,
                "timestamp": t.timestamp.isoformat(),
            }
            for t in rows
        ]


# ----------------------------------------------------------------------------
# Semantic pattern search — Account Historian uses this to flag transactions
# semantically similar to known typologies, even if no rule fires.
# ----------------------------------------------------------------------------


def semantic_pattern_search(query_text: str, limit: int = 5) -> list[dict[str, Any]]:
    """Embed `query_text` with nomic-embed-text and cosine-search the pattern corpus.

    Returns the top-`limit` matching typology rows with their distance.
    """
    from langchain_ollama import OllamaEmbeddings

    from core.settings import get_settings

    embed = OllamaEmbeddings(
        model="nomic-embed-text",
        base_url=get_settings().ollama_host,
    )
    vec = embed.embed_query(query_text)

    with session_scope() as s:
        rows = s.execute(
            select(
                FraudPatternEmbedding.id,
                FraudPatternEmbedding.typology,
                FraudPatternEmbedding.description,
                FraudPatternEmbedding.source_scenario_id,
                FraudPatternEmbedding.embedding.cosine_distance(vec).label("distance"),
            )
            .order_by(text("distance ASC"))
            .limit(limit)
        ).all()
        return [
            {
                "pattern_id": r.id,
                "typology": r.typology,
                "description": r.description,
                "source_scenario_id": r.source_scenario_id,
                "distance": float(r.distance),
            }
            for r in rows
        ]
