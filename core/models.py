"""SQLAlchemy ORM models — single source of truth for the Postgres schema.

Mirrors plan section 0.3. Alembic autogenerate uses `Base.metadata`.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from core.settings import get_settings

_EMBED_DIM = get_settings().embedding_dim


class Base(DeclarativeBase):
    pass


# ----------------------------------------------------------------------------
# Core tables
# ----------------------------------------------------------------------------


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_type: Mapped[str] = mapped_column(String(16))  # personal | business
    open_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kyc_tier: Mapped[int] = mapped_column(Integer)  # 1..3
    country: Mapped[str] = mapped_column(String(2))  # ISO-3166 alpha-2
    status: Mapped[str] = mapped_column(String(16), default="active")
    holder_name: Mapped[str] = mapped_column(String(128))
    holder_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    primary_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    beneficial_owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="account", foreign_keys="Transaction.account_id"
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.id"), index=True
    )
    counterparty_account_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("accounts.id"), nullable=True, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    direction: Mapped[str] = mapped_column(String(8))  # debit | credit
    channel: Mapped[str] = mapped_column(String(16))   # card | transfer | cash | wire | atm
    merchant: Mapped[str | None] = mapped_column(String(128), nullable=True)
    merchant_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="settled")

    account: Mapped[Account] = relationship(back_populates="transactions", foreign_keys=[account_id])

    __table_args__ = (
        Index("ix_tx_account_ts", "account_id", "timestamp"),
    )


class WatchlistEntity(Base):
    __tablename__ = "watchlist_entities"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    list_type: Mapped[str] = mapped_column(String(16))  # PEP | SANCTIONS | ADVERSE_MEDIA
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    risk_score: Mapped[int] = mapped_column(Integer, default=50)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)


# ----------------------------------------------------------------------------
# Graph (single unified edge table — see plan 0.3 rationale)
# ----------------------------------------------------------------------------


class AccountNetworkEdge(Base):
    __tablename__ = "account_network_edges"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_account_id: Mapped[str] = mapped_column(String(32), ForeignKey("accounts.id"), index=True)
    target_account_id: Mapped[str] = mapped_column(String(32), ForeignKey("accounts.id"), index=True)
    relationship_type: Mapped[str] = mapped_column(String(32))
    # 'declared' = KYC, beneficial owner, shared registered address at onboarding
    # 'derived'  = observed transactions, shared device, shared IP, runtime-computed
    source_type: Mapped[str] = mapped_column(String(16))
    weight: Mapped[float] = mapped_column(Numeric(5, 4), default=1.0)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        Index("ix_edge_src_type", "source_account_id", "source_type"),
        Index("ix_edge_tgt_type", "target_account_id", "source_type"),
    )


# ----------------------------------------------------------------------------
# Pattern matching
# ----------------------------------------------------------------------------


class FraudPatternEmbedding(Base):
    __tablename__ = "fraud_pattern_embeddings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    typology: Mapped[str] = mapped_column(String(32), index=True)
    description: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(_EMBED_DIM))
    source_scenario_id: Mapped[str] = mapped_column(String(32), index=True)


# ----------------------------------------------------------------------------
# Investigation tables
# ----------------------------------------------------------------------------


class FraudAlert(Base):
    __tablename__ = "fraud_alerts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), ForeignKey("accounts.id"), index=True)
    alert_type: Mapped[str] = mapped_column(String(32))
    initial_score: Mapped[float] = mapped_column(Numeric(4, 3))
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="open")
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Ground-truth label is stored separately in eval/ground_truth.json — never here.


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_id: Mapped[str] = mapped_column(String(32), ForeignKey("fraud_alerts.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="running")
    # running | paused_hitl | completed | auto_closed | failed
    confidence_score: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    expansion_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state_json: Mapped[dict] = mapped_column(JSONB, default=dict)


class InvestigationEvent(Base):
    """Append-only audit log. Routing reads supervisor state, NOT this table — see plan 1.3."""
    __tablename__ = "investigation_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(48))
    actor: Mapped[str] = mapped_column(String(48))  # agent name or 'supervisor'
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentDecision(Base):
    __tablename__ = "agent_decisions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id"), index=True
    )
    agent_name: Mapped[str] = mapped_column(String(48), index=True)
    decision_type: Mapped[str] = mapped_column(String(48))
    decision_payload: Mapped[dict] = mapped_column(JSONB)
    justification: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvidenceItem(Base):
    __tablename__ = "evidence_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id"), index=True
    )
    evidence_type: Mapped[str] = mapped_column(String(48))
    source_table: Mapped[str | None] = mapped_column(String(48), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)


class CaseFile(Base):
    __tablename__ = "case_files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id"), unique=True
    )
    risk_tier: Mapped[str] = mapped_column(String(16))  # LOW | MEDIUM | HIGH | CRITICAL
    recommended_action: Mapped[str] = mapped_column(String(24))
    # AUTO_CLOSE | REVIEW | SAR_FILE
    sar_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3))
    case_json: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ----------------------------------------------------------------------------
# Security & observability
# ----------------------------------------------------------------------------


class SecurityEvent(Base):
    __tablename__ = "security_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id"), nullable=True, index=True
    )
    rail: Mapped[str] = mapped_column(String(32))  # scope | schema | justification | escalation | pii
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    actor: Mapped[str] = mapped_column(String(48))
    detail: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentTrace(Base):
    __tablename__ = "agent_traces"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id"), index=True
    )
    agent_name: Mapped[str] = mapped_column(String(48), index=True)
    step: Mapped[int] = mapped_column(Integer)
    reasoning_text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ToolCallLog(Base):
    __tablename__ = "tool_call_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id"), index=True
    )
    agent_name: Mapped[str] = mapped_column(String(48))
    tool_name: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict] = mapped_column(JSONB)
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    backend: Mapped[str] = mapped_column(String(16))  # ollama | bedrock
    git_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evaluation_runs.id"), index=True
    )
    alert_id: Mapped[str] = mapped_column(String(32))
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    verdict_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    typology_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    network_recall: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    faithfulness_score: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    hallucination_rate: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    metrics_json: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (UniqueConstraint("run_id", "alert_id", name="uq_run_alert"),)
