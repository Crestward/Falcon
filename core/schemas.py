"""Inter-agent message schemas. Pydantic enforces the schema rail at every boundary.

Phase 0 ships minimal versions; Phase 1/2 will flesh out fields. Keep additions
backwards-compatible — agents may be on different graph runs.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SeverityTier(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class InvestigationDepth(str, Enum):
    SHALLOW = "SHALLOW"
    DEEP = "DEEP"
    FULL = "FULL"


class FraudTypology(str, Enum):
    STRUCTURING = "STRUCTURING"
    LAYERING = "LAYERING"
    ACCOUNT_TAKEOVER = "ACCOUNT_TAKEOVER"
    MULE_NETWORK = "MULE_NETWORK"
    PEP_EXPOSURE = "PEP_EXPOSURE"
    NONE = "NONE"


class RecommendedAction(str, Enum):
    AUTO_CLOSE = "AUTO_CLOSE"
    REVIEW = "REVIEW"
    SAR_FILE = "SAR_FILE"


class _Strict(BaseModel):
    """Base for inter-agent payloads. Extra fields rejected to catch drift fast."""
    model_config = ConfigDict(extra="forbid", frozen=False)


# ----------------------------------------------------------------------------
# Triage
# ----------------------------------------------------------------------------


class TriageAssessment(_Strict):
    severity: SeverityTier
    recommended_depth: InvestigationDepth
    initial_hypothesis: str = Field(..., min_length=10, max_length=2000)
    quick_signals: dict[str, Any] = Field(default_factory=dict)
    justification: str = Field(..., min_length=10, max_length=2000)


# ----------------------------------------------------------------------------
# Account Historian
# ----------------------------------------------------------------------------


class AnomalyWindow(_Strict):
    start: datetime
    end: datetime
    description: str
    severity: SeverityTier


class AccountProfile(_Strict):
    account_id: str
    baseline: dict[str, Any]
    anomalies: list[AnomalyWindow] = Field(default_factory=list)
    flagged_transaction_ids: list[int] = Field(default_factory=list)
    counterparty_account_ids: list[str] = Field(default_factory=list)
    semantic_matches: list[dict[str, Any]] = Field(default_factory=list)


# ----------------------------------------------------------------------------
# Network Mapper
# ----------------------------------------------------------------------------


class NetworkNode(_Strict):
    account_id: str
    risk_score: float
    role: str | None = None  # 'hub' | 'leaf' | 'bridge' | None


class NetworkEdge(_Strict):
    source: str
    target: str
    relationship_type: str
    source_type: str  # 'declared' | 'derived'
    weight: float
    evidence: dict[str, Any] = Field(default_factory=dict)


class ExpansionRequest(_Strict):
    """Returned in NetworkGraph; supervisor branches on this. DB write is audit-only."""
    trigger: bool
    new_accounts: list[str] = Field(default_factory=list)
    rationale: str | None = None


class NetworkGraph(_Strict):
    nodes: list[NetworkNode]
    edges: list[NetworkEdge]
    suspicious_clusters: list[list[str]] = Field(default_factory=list)
    expansion_request: ExpansionRequest


# ----------------------------------------------------------------------------
# Case Writer
# ----------------------------------------------------------------------------


class CaseEvidence(_Strict):
    evidence_type: str
    summary: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    source_ref: str | None = None


class CaseFileSchema(_Strict):
    """Mirrors FCA/JMLSG SAR structure — see plan 1.5."""
    investigation_id: UUID
    risk_tier: SeverityTier
    recommended_action: RecommendedAction
    sar_ready: bool
    confidence: float = Field(..., ge=0.0, le=1.0)
    executive_summary: str = Field(..., min_length=20, max_length=4000)
    suspicion_grounds: str
    subject_details: dict[str, Any]
    financial_exposure_estimate: float
    evidence_chain: list[CaseEvidence]
    network_summary: dict[str, Any]
    contradictions_addressed: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------------------
# Pattern Hunter (Phase 2)
# ----------------------------------------------------------------------------


class TypologyMatch(_Strict):
    """A single typology evaluated by the Pattern Hunter."""
    typology: FraudTypology
    score: float = Field(..., ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    triggered_detectors: list[str] = Field(default_factory=list)


class TypologyAssessment(_Strict):
    """Pattern Hunter's verdict on which fraud typology (if any) best fits."""
    primary_typology: FraudTypology
    primary_score: float = Field(..., ge=0.0, le=1.0)
    matches: list[TypologyMatch] = Field(default_factory=list)
    rationale: str = Field(..., min_length=10, max_length=4000)


# ----------------------------------------------------------------------------
# Contradiction Detection (Phase 2.4)
# ----------------------------------------------------------------------------


class ContradictionReport(_Strict):
    """Conflicts between agents detected at CHECKPOINT_4. The case writer
    must address every entry in `contradictions_addressed`."""
    contradictions: list[str] = Field(default_factory=list)
    confidence_penalty: float = Field(0.0, ge=0.0, le=1.0)


# ----------------------------------------------------------------------------
# HITL (Phase 2.2)
# ----------------------------------------------------------------------------


class Annotation(_Strict):
    """Human reviewer note submitted via POST /investigations/{id}/annotate.
    On resume, this is injected into state and passed to the Case Writer."""
    reviewer_id: str
    note: str = Field(..., min_length=1, max_length=4000)
    override_action: RecommendedAction | None = None
    override_confidence: float | None = Field(None, ge=0.0, le=1.0)


# ----------------------------------------------------------------------------
# Justification rail — every tool call payload MUST extend this.
# ----------------------------------------------------------------------------


class JustifiedToolCall(_Strict):
    tool_name: str
    arguments: dict[str, Any]
    justification: str = Field(..., min_length=10, max_length=500)
