"""Supervisor-wide constants. Centralised so behavioural changes are auditable."""
from __future__ import annotations

MAX_EXPANSIONS: int = 3
"""Hard cap on CHECKPOINT_3 → EXPAND loops. Prevents infinite re-expansion."""

NETWORK_MAX_HOPS: int = 2
"""Phase 1 cap — Phase 2 makes this dynamic per cluster density."""

# Confidence tiering — enforced at CHECKPOINT_4. Boundaries are inclusive
# on the HITL band so the threshold values themselves route to review.
#
# Auto-close threshold lowered from 0.40 → 0.25 (2026-05-21) per fix.md
# item 1: the original 0.40 cut-off was too aggressive — most genuine
# STRUCTURING alerts were producing typology scores around 0.30 and being
# auto-closed before a Case Writer could even draft a SAR. Lowering the
# floor moves those cases into the HITL band where a human reviewer (or,
# in the demo, the dashboard's auto-resume) takes them through to a draft.
# The trade-off is a higher FPR on clean alerts — acceptable for portfolio
# demonstration and tracked as the next tuning item.
CONF_AUTO_CLOSE_BELOW: float = 0.25
CONF_SAR_ABOVE: float = 0.75
