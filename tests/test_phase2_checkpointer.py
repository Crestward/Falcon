"""Phase 2 checkpointer + graph-shape tests."""
from __future__ import annotations

from supervisor.graph import build_graph


def test_phase2_graph_compiles_without_checkpointer() -> None:
    """For unit tests we don't have a live DB checkpointer; the graph should
    still compile cleanly (checkpointer is optional at build time)."""
    g = build_graph()
    assert hasattr(g, "invoke")


def test_phase2_graph_compiles_with_inmemory_checkpointer() -> None:
    """Verify the checkpointer slot accepts a saver — uses LangGraph's
    in-memory implementation so the test stays DB-free."""
    from langgraph.checkpoint.memory import MemorySaver

    g = build_graph(checkpointer=MemorySaver())
    assert hasattr(g, "invoke")


def test_graph_nodes_include_phase2_additions() -> None:
    g = build_graph()
    # The compiled graph exposes its underlying nodes via `.nodes`
    names = set(g.nodes)
    for required in (
        "triage", "account_historian", "network_mapper",
        "expand", "pattern_hunter", "checkpoint_4",
        "auto_close", "hitl_pause", "case_writer",
    ):
        assert required in names, f"missing node: {required}"
