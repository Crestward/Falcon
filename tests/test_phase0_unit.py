"""Phase 0 unit smoke tests — no external infra required.

Verifies the code loads, schemas validate, and the LLM factory dispatches correctly.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_settings_load() -> None:
    from core.settings import get_settings
    s = get_settings()
    assert s.llm_backend in {"ollama", "bedrock", "vertex", "anthropic"}
    assert s.judge_backend in {"bedrock", "vertex", "anthropic"}
    assert s.embedding_dim == 768


def test_models_metadata_has_all_tables() -> None:
    from core.models import Base
    table_names = set(Base.metadata.tables.keys())
    expected = {
        "accounts", "transactions", "watchlist_entities",
        "account_network_edges", "fraud_pattern_embeddings",
        "fraud_alerts", "investigations", "investigation_events",
        "agent_decisions", "evidence_items", "case_files",
        "security_events", "agent_traces", "tool_call_logs",
        "evaluation_runs", "evaluation_results",
    }
    missing = expected - table_names
    assert not missing, f"Missing tables in models.py: {missing}"


def test_triage_schema_validates() -> None:
    from core.schemas import InvestigationDepth, SeverityTier, TriageAssessment
    ok = TriageAssessment(
        severity=SeverityTier.HIGH,
        recommended_depth=InvestigationDepth.DEEP,
        initial_hypothesis="Possible structuring based on five near-threshold credits.",
        quick_signals={"recent_max_amount": 9800},
        justification="Five credits between £9,300 and £9,900 within 48h.",
    )
    assert ok.severity is SeverityTier.HIGH


def test_triage_schema_rejects_extra_fields() -> None:
    from core.schemas import TriageAssessment
    with pytest.raises(ValidationError):
        TriageAssessment.model_validate({
            "severity": "HIGH",
            "recommended_depth": "DEEP",
            "initial_hypothesis": "x" * 50,
            "justification": "y" * 50,
            "totally_made_up_field": True,
        })


def test_justification_rail_rejects_empty_justification() -> None:
    from core.schemas import JustifiedToolCall
    with pytest.raises(ValidationError):
        JustifiedToolCall(tool_name="x", arguments={}, justification="")


def test_llm_factory_judge_never_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even when LLM_BACKEND=ollama, judge must dispatch to a cloud backend
    (Bedrock by default, or whichever JUDGE_BACKEND is set to). Judge is
    never ollama — an 8B model grading an 8B model is not credible (plan 3.3).
    """
    from core.settings import Settings, get_settings

    # Build a fresh Settings instance bypassing .env so the test isn't
    # coupled to whatever the developer has configured locally.
    test_settings = Settings(
        llm_backend="ollama",
        judge_backend="bedrock",
        _env_file=None,  # type: ignore[call-arg]
    )
    monkeypatch.setattr("core.settings.Settings", lambda **kw: test_settings)
    get_settings.cache_clear()
    monkeypatch.setattr("core.settings.get_settings", lambda: test_settings)

    from core import llm_factory
    monkeypatch.setattr(llm_factory, "get_settings", lambda: test_settings)
    calls: list[str] = []
    monkeypatch.setattr(llm_factory, "_make_bedrock", lambda role: calls.append(f"bedrock:{role}") or "BR")
    monkeypatch.setattr(llm_factory, "_make_ollama", lambda role: calls.append(f"ollama:{role}") or "OL")
    monkeypatch.setattr(llm_factory, "_make_vertex", lambda role: calls.append(f"vertex:{role}") or "VX")
    monkeypatch.setattr(llm_factory, "_make_anthropic", lambda role: calls.append(f"anthropic:{role}") or "AN")

    assert llm_factory.get_llm("judge") == "BR"
    assert llm_factory.get_llm("triage") == "OL"
    assert calls == ["bedrock:judge", "ollama:triage"]


def test_llm_factory_judge_follows_judge_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Judge dispatches per JUDGE_BACKEND independently of LLM_BACKEND."""
    from core.settings import Settings, get_settings

    test_settings = Settings(
        llm_backend="anthropic",
        judge_backend="vertex",
        _env_file=None,  # type: ignore[call-arg]
    )
    get_settings.cache_clear()
    monkeypatch.setattr("core.settings.get_settings", lambda: test_settings)
    from core import llm_factory
    monkeypatch.setattr(llm_factory, "get_settings", lambda: test_settings)
    monkeypatch.setattr(llm_factory, "_make_bedrock", lambda role: "BR")
    monkeypatch.setattr(llm_factory, "_make_vertex", lambda role: "VX")
    monkeypatch.setattr(llm_factory, "_make_anthropic", lambda role: "AN")

    assert llm_factory.get_llm("judge") == "VX"
    assert llm_factory.get_llm("triage") == "AN"


def test_llm_factory_role_registry_complete() -> None:
    """Every non-judge role must have a local model assigned."""
    from core.llm_factory import OLLAMA_MODELS
    expected = {"triage", "account_historian", "network_mapper", "pattern_hunter", "case_writer"}
    assert expected.issubset(OLLAMA_MODELS.keys())


def test_fraud_scenarios_registry() -> None:
    from data.fraud_scenarios import SCENARIO_GENERATORS
    assert set(SCENARIO_GENERATORS.keys()) == {
        "STRUCTURING", "LAYERING", "ACCOUNT_TAKEOVER", "MULE_NETWORK", "PEP_EXPOSURE",
    }


def test_mcp_servers_importable() -> None:
    """All four MCP server modules must import cleanly."""
    import importlib
    for mod in (
        "mcp_servers.transaction_store.server",
        "mcp_servers.network_graph.server",
        "mcp_servers.watchlist.server",
        "mcp_servers.case_management.server",
    ):
        importlib.import_module(mod)
