"""Phase 1 graph-shape tests — verify the LangGraph wiring without invoking agents."""
from __future__ import annotations

import uuid

from core.schemas import ExpansionRequest, NetworkGraph
from supervisor.graph import build_graph, route_after_network


def _network(expand: bool, accounts: list[str]) -> NetworkGraph:
    return NetworkGraph(
        nodes=[], edges=[], suspicious_clusters=[],
        expansion_request=ExpansionRequest(trigger=expand, new_accounts=accounts),
    )


def test_router_proceeds_when_no_expansion() -> None:
    state = {
        "investigation_id": uuid.uuid4(),
        "network_graph": _network(False, []),
        "expansion_count": 0,
    }
    assert route_after_network(state) == "proceed"


def test_router_expands_under_cap() -> None:
    state = {
        "investigation_id": uuid.uuid4(),
        "network_graph": _network(True, ["AC1"]),
        "expansion_count": 0,
    }
    assert route_after_network(state) == "expand"


def test_router_caps_expansion() -> None:
    from supervisor.config import MAX_EXPANSIONS
    state = {
        "investigation_id": uuid.uuid4(),
        "network_graph": _network(True, ["AC1"]),
        "expansion_count": MAX_EXPANSIONS,
    }
    assert route_after_network(state) == "proceed"


def test_build_graph_compiles() -> None:
    g = build_graph()
    # Compiled graph exposes invoke / get_state — checking presence is enough.
    assert hasattr(g, "invoke")
