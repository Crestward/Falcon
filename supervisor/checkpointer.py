"""LangGraph Postgres checkpointer for HITL pause/resume.

The checkpointer serialises full graph state at every node boundary into
Postgres-managed `checkpoints`, `checkpoint_writes`, and `checkpoint_blobs`
tables (created on first `setup()`). When an investigation is interrupted
(HITL pause, process crash, etc.) it can resume from the last checkpoint.

Usage:
    with get_checkpointer() as cp:
        graph = build_graph(checkpointer=cp)
        graph.invoke(initial, config={"configurable": {"thread_id": str(investigation_id)}})

`thread_id` is the LangGraph identifier that ties checkpoints together.
We use the investigation UUID so resume is trivially keyed.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver

from core.settings import get_settings


def _checkpointer_conn_string() -> str:
    """Convert the SQLAlchemy URL (postgresql+psycopg://...) to the bare
    psycopg form (postgresql://...) that PostgresSaver expects."""
    url = get_settings().database_url
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@contextmanager
def get_checkpointer() -> Iterator[PostgresSaver]:
    """Context-managed Postgres checkpointer. Calls setup() on first use
    (idempotent — safe to call repeatedly)."""
    with PostgresSaver.from_conn_string(_checkpointer_conn_string()) as cp:
        cp.setup()
        yield cp
