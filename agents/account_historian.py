"""Account Historian — 90-day baseline + statistical anomaly detection.

Pure-Python stats run BEFORE the LLM call. The LLM's job is to interpret and
narrate the numbers into an AccountProfile, not to compute z-scores itself
(that would be unreliable on an 8B model). See plan §1.2.
"""
from __future__ import annotations

import statistics
import uuid
from collections import Counter
from datetime import datetime
from typing import Any

from agents.llm_utils import call_structured
from agents.tools import call_tool
from core.schemas import AccountProfile, InvestigationDepth
from mcp_servers.case_management import queries as case_q

_Z_THRESHOLD = 3.0


def _compute_stats(history: list[dict[str, Any]]) -> dict[str, Any]:
    if not history:
        return {
            "transaction_count": 0,
            "mean_amount": 0.0,
            "stdev_amount": 0.0,
            "max_amount": 0.0,
            "channel_mix": {},
            "merchant_category_mix": {},
            "counterparty_unique": 0,
            "flagged_transaction_ids": [],
            "counterparty_account_ids": [],
        }

    amounts = [t["amount"] for t in history]
    mean_amt = statistics.fmean(amounts)
    stdev_amt = statistics.pstdev(amounts) if len(amounts) > 1 else 0.0

    flagged: list[int] = []
    if stdev_amt > 0:
        for t in history:
            z = (t["amount"] - mean_amt) / stdev_amt
            if abs(z) >= _Z_THRESHOLD:
                flagged.append(t["id"])

    channels = Counter(t["channel"] for t in history)
    categories = Counter(t["merchant_category"] for t in history if t["merchant_category"])
    counterparties = {t["counterparty_account_id"] for t in history if t["counterparty_account_id"]}

    return {
        "transaction_count": len(history),
        "mean_amount": round(mean_amt, 2),
        "stdev_amount": round(stdev_amt, 2),
        "max_amount": max(amounts),
        "channel_mix": dict(channels),
        "merchant_category_mix": dict(categories),
        "counterparty_unique": len(counterparties),
        "flagged_transaction_ids": flagged,
        "counterparty_account_ids": sorted(counterparties),
    }


def _detect_anomaly_windows(history: list[dict[str, Any]], flagged_ids: list[int]) -> list[dict[str, Any]]:
    """Cluster flagged transactions into time windows. Trivial but explainable."""
    if not flagged_ids:
        return []
    flagged_set = set(flagged_ids)
    flagged_txns = sorted(
        (t for t in history if t["id"] in flagged_set),
        key=lambda t: t["timestamp"],
    )
    windows: list[dict[str, Any]] = []
    cur_start: str | None = None
    cur_end: str | None = None
    cur_count = 0
    for t in flagged_txns:
        if cur_start is None:
            cur_start = t["timestamp"]
            cur_end = t["timestamp"]
            cur_count = 1
            continue
        # Burst within 6 hours = same window
        prev = datetime.fromisoformat(cur_end)
        nxt = datetime.fromisoformat(t["timestamp"])
        if (nxt - prev).total_seconds() <= 6 * 3600:
            cur_end = t["timestamp"]
            cur_count += 1
        else:
            windows.append(
                {"start": cur_start, "end": cur_end, "description": f"{cur_count} high-z transactions"}
            )
            cur_start = t["timestamp"]
            cur_end = t["timestamp"]
            cur_count = 1
    if cur_start is not None:
        windows.append(
            {"start": cur_start, "end": cur_end, "description": f"{cur_count} high-z transactions"}
        )
    return windows


SYSTEM_PROMPT = """You are the Account Historian in a multi-agent fraud investigation system.

You receive precomputed statistical analysis of an account's 90-day transaction
history, a list of anomaly windows, and semantic matches against known fraud
typologies. Your job is to assemble a structured AccountProfile.

DO NOT recompute the statistics — trust the numbers given to you. Your job is
to interpret them: identify which anomalies look behavioural-shift vs
noise, write a concise baseline narrative, and assign severity to each window.

Severity rubric for anomaly windows:
  LOW       - isolated burst, plausible legitimate activity
  MEDIUM    - sustained deviation but no typology match
  HIGH      - matches semantic pattern OR multiple corroborating signals
  CRITICAL  - matches semantic pattern AND involves >5 high-z transactions
"""


def historian(
    account_id: str,
    depth: InvestigationDepth,
    investigation_id: uuid.UUID,
) -> AccountProfile:
    days = {"SHALLOW": 30, "DEEP": 60, "FULL": 90}[depth.value]

    history = call_tool(
        investigation_id=investigation_id,
        agent_name="account_historian",
        tool_name="transaction_store.get_history",
        arguments={"account_id": account_id, "days": days},
        justification=f"Historian computes baseline over {days} days per depth={depth.value}",
    )

    stats = _compute_stats(history)
    windows = _detect_anomaly_windows(history, stats["flagged_transaction_ids"])

    semantic_matches: list[dict[str, Any]] = []
    if stats["transaction_count"] > 0:
        # Build a short narrative seed for embedding search.
        query_text = (
            f"Account {account_id}: {stats['transaction_count']} transactions over {days} days, "
            f"mean {stats['mean_amount']}, max {stats['max_amount']}, "
            f"channels={list(stats['channel_mix'].keys())}, "
            f"flagged_count={len(stats['flagged_transaction_ids'])}, "
            f"unique_counterparties={stats['counterparty_unique']}."
        )
        semantic_matches = call_tool(
            investigation_id=investigation_id,
            agent_name="account_historian",
            tool_name="transaction_store.semantic_pattern_search",
            arguments={"query_text": query_text, "limit": 3},
            justification="Match precomputed account narrative against known fraud typologies",
        )

    user_prompt = (
        f"ACCOUNT: {account_id}\nDEPTH: {depth.value} ({days} days)\n\n"
        f"PRECOMPUTED STATS:\n{stats}\n\n"
        f"DETECTED ANOMALY WINDOWS:\n{windows}\n\n"
        f"SEMANTIC TYPOLOGY MATCHES (lower distance = closer):\n{semantic_matches}\n\n"
        "Assemble the AccountProfile. "
        f"Set account_id='{account_id}'. "
        "baseline must include keys: transaction_count, mean_amount, stdev_amount, "
        "channel_mix, narrative (1-2 sentences). "
        "flagged_transaction_ids must equal the precomputed list. "
        "counterparty_account_ids must equal the precomputed list."
    )

    profile = call_structured(
        role="account_historian",
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=AccountProfile,
        investigation_id=investigation_id,
        agent_name="account_historian",
    )

    # Trust the stats over the LLM for the deterministic fields.
    profile = profile.model_copy(
        update={
            "account_id": account_id,
            "flagged_transaction_ids": stats["flagged_transaction_ids"],
            "counterparty_account_ids": stats["counterparty_account_ids"],
            "semantic_matches": semantic_matches,
        }
    )

    case_q.record_decision(
        investigation_id=investigation_id,
        agent_name="account_historian",
        decision_type="ACCOUNT_PROFILE",
        decision_payload=profile.model_dump(mode="json"),
        justification=f"90-day analysis for {account_id}; {len(profile.anomalies)} windows detected",
    )
    return profile
