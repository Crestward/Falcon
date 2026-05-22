"""initial schema — accounts, transactions, graph, investigations, observability, pgvector

Revision ID: 0001
Revises:
Create Date: 2026-04-28 00:00:00

Mirrors plan section 0.3. Single edge table with `source_type`; fraud_pattern_embeddings
holds the corpus from 0.2.1.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from core.settings import get_settings

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_EMBED_DIM = get_settings().embedding_dim


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.create_table(
        "accounts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("account_type", sa.String(16), nullable=False),
        sa.Column("open_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kyc_tier", sa.Integer(), nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("status", sa.String(16), server_default="active"),
        sa.Column("holder_name", sa.String(128), nullable=False),
        sa.Column("holder_address", sa.Text()),
        sa.Column("device_fingerprint", sa.String(64)),
        sa.Column("primary_ip", sa.String(45)),
        sa.Column("beneficial_owner_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "transactions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.String(32), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("counterparty_account_id", sa.String(32), sa.ForeignKey("accounts.id")),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), server_default="GBP"),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("merchant", sa.String(128)),
        sa.Column("merchant_category", sa.String(64)),
        sa.Column("description", sa.Text()),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), server_default="settled"),
    )
    op.create_index("ix_transactions_account_id", "transactions", ["account_id"])
    op.create_index("ix_transactions_counterparty_account_id", "transactions", ["counterparty_account_id"])
    op.create_index("ix_transactions_timestamp", "transactions", ["timestamp"])
    op.create_index("ix_tx_account_ts", "transactions", ["account_id", "timestamp"])

    op.create_table(
        "watchlist_entities",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("list_type", sa.String(16), nullable=False),
        sa.Column("country", sa.String(2)),
        sa.Column("risk_score", sa.Integer(), server_default="50"),
        sa.Column("metadata_json", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("ix_watchlist_entities_name", "watchlist_entities", ["name"])

    op.create_table(
        "account_network_edges",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_account_id", sa.String(32), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("target_account_id", sa.String(32), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("relationship_type", sa.String(32), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("weight", sa.Numeric(5, 4), server_default="1.0"),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("metadata_json", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("ix_account_network_edges_source_account_id", "account_network_edges", ["source_account_id"])
    op.create_index("ix_account_network_edges_target_account_id", "account_network_edges", ["target_account_id"])
    op.create_index("ix_edge_src_type", "account_network_edges", ["source_account_id", "source_type"])
    op.create_index("ix_edge_tgt_type", "account_network_edges", ["target_account_id", "source_type"])

    op.create_table(
        "fraud_pattern_embeddings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("typology", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(_EMBED_DIM), nullable=False),
        sa.Column("source_scenario_id", sa.String(32), nullable=False),
    )
    op.create_index("ix_fraud_pattern_embeddings_typology", "fraud_pattern_embeddings", ["typology"])
    op.create_index("ix_fraud_pattern_embeddings_source_scenario_id", "fraud_pattern_embeddings", ["source_scenario_id"])
    # IVFFlat index for cosine similarity — created after data is loaded for best
    # quality. We create an empty one here; rebuild in 0.2.1 once vectors exist.
    op.execute(
        "CREATE INDEX ix_fraud_pattern_embeddings_cos "
        "ON fraud_pattern_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10)"
    )

    op.create_table(
        "fraud_alerts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("account_id", sa.String(32), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("alert_type", sa.String(32), nullable=False),
        sa.Column("initial_score", sa.Numeric(4, 3), nullable=False),
        sa.Column("raised_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), server_default="open"),
        sa.Column("metadata_json", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("ix_fraud_alerts_account_id", "fraud_alerts", ["account_id"])
    op.create_index("ix_fraud_alerts_raised_at", "fraud_alerts", ["raised_at"])

    op.create_table(
        "investigations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("alert_id", sa.String(32), sa.ForeignKey("fraud_alerts.id"), nullable=False),
        sa.Column("status", sa.String(24), server_default="running"),
        sa.Column("confidence_score", sa.Numeric(4, 3)),
        sa.Column("expansion_count", sa.Integer(), server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("state_json", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("ix_investigations_alert_id", "investigations", ["alert_id"])

    op.create_table(
        "investigation_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("investigation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("investigations.id"), nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("actor", sa.String(48), nullable=False),
        sa.Column("payload", postgresql.JSONB(), server_default="{}"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_investigation_events_investigation_id", "investigation_events", ["investigation_id"])

    op.create_table(
        "agent_decisions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("investigation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("investigations.id"), nullable=False),
        sa.Column("agent_name", sa.String(48), nullable=False),
        sa.Column("decision_type", sa.String(48), nullable=False),
        sa.Column("decision_payload", postgresql.JSONB(), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3)),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_agent_decisions_investigation_id", "agent_decisions", ["investigation_id"])
    op.create_index("ix_agent_decisions_agent_name", "agent_decisions", ["agent_name"])

    op.create_table(
        "evidence_items",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("investigation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("investigations.id"), nullable=False),
        sa.Column("evidence_type", sa.String(48), nullable=False),
        sa.Column("source_table", sa.String(48)),
        sa.Column("source_id", sa.String(64)),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("payload", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("ix_evidence_items_investigation_id", "evidence_items", ["investigation_id"])

    op.create_table(
        "case_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("investigation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("investigations.id"), unique=True, nullable=False),
        sa.Column("risk_tier", sa.String(16), nullable=False),
        sa.Column("recommended_action", sa.String(24), nullable=False),
        sa.Column("sar_ready", sa.Boolean(), server_default=sa.false()),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("case_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "security_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("investigation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("investigations.id")),
        sa.Column("rail", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), server_default="warning"),
        sa.Column("actor", sa.String(48), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), server_default="{}"),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_security_events_investigation_id", "security_events", ["investigation_id"])

    op.create_table(
        "agent_traces",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("investigation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("investigations.id"), nullable=False),
        sa.Column("agent_name", sa.String(48), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("reasoning_text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_agent_traces_investigation_id", "agent_traces", ["investigation_id"])
    op.create_index("ix_agent_traces_agent_name", "agent_traces", ["agent_name"])

    op.create_table(
        "tool_call_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("investigation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("investigations.id"), nullable=False),
        sa.Column("agent_name", sa.String(48), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=False),
        sa.Column("justification", sa.Text()),
        sa.Column("result_summary", sa.Text()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("called_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_tool_call_logs_investigation_id", "tool_call_logs", ["investigation_id"])

    op.create_table(
        "evaluation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("backend", sa.String(16), nullable=False),
        sa.Column("git_sha", sa.String(40)),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("summary", postgresql.JSONB(), server_default="{}"),
    )

    op.create_table(
        "evaluation_results",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("evaluation_runs.id"), nullable=False),
        sa.Column("alert_id", sa.String(32), nullable=False),
        sa.Column("investigation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("verdict_correct", sa.Boolean()),
        sa.Column("typology_correct", sa.Boolean()),
        sa.Column("network_recall", sa.Numeric(4, 3)),
        sa.Column("faithfulness_score", sa.Numeric(4, 3)),
        sa.Column("hallucination_rate", sa.Numeric(4, 3)),
        sa.Column("metrics_json", postgresql.JSONB(), server_default="{}"),
        sa.UniqueConstraint("run_id", "alert_id", name="uq_run_alert"),
    )
    op.create_index("ix_evaluation_results_run_id", "evaluation_results", ["run_id"])


def downgrade() -> None:
    for tbl in (
        "evaluation_results", "evaluation_runs", "tool_call_logs", "agent_traces",
        "security_events", "case_files", "evidence_items", "agent_decisions",
        "investigation_events", "investigations", "fraud_alerts",
        "fraud_pattern_embeddings", "account_network_edges",
        "watchlist_entities", "transactions", "accounts",
    ):
        op.drop_table(tbl)
    op.execute("DROP EXTENSION IF EXISTS vector")
