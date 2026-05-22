"""Phase 3 — LLM-as-judge. Test parsing + neutrality flag with a mock LLM."""
from __future__ import annotations

import json
from unittest.mock import patch

from langchain_core.messages import AIMessage

from eval.judge import judge_case, judge_neutrality_flag


class _FakeLLM:
    def __init__(self, payload: str):
        self._payload = payload

    def invoke(self, _msgs):
        return AIMessage(content=self._payload, response_metadata={"usage": {}})


def _case():
    return {
        "risk_tier": "HIGH",
        "recommended_action": "SAR_FILE",
        "executive_summary": "Account shows structuring pattern.",
        "suspicion_grounds": "Multiple sub-threshold deposits.",
        "evidence_chain": [
            {"evidence_type": "structuring", "summary": "7 deposits within 72h", "confidence": 0.9}
        ],
    }


def _profiles():
    return [{"account_id": "AC1", "anomalies": [{}], "flagged_transaction_ids": [1, 2, 3], "counterparty_account_ids": []}]


def test_judge_parses_valid_response():
    payload = json.dumps({
        "claims": [
            {"claim": "7 deposits within 72h", "support": 2, "note": "ok"},
            {"claim": "Account shows structuring pattern.", "support": 1, "note": ""},
        ]
    })
    with patch("eval.judge.get_llm", return_value=_FakeLLM(payload)):
        result = judge_case(
            case_file=_case(),
            typology="STRUCTURING",
            confidence=0.85,
            profiles=_profiles(),
            network=None,
        )
    # (2 + 1) / 2 / 2 = 0.75
    assert result["faithfulness_score"] == 0.75
    assert result["hallucination_rate"] == 0.0
    assert len(result["claims"]) == 2


def test_judge_handles_unsupported_claims():
    payload = json.dumps({
        "claims": [
            {"claim": "X", "support": 0, "note": "no evidence"},
            {"claim": "Y", "support": 0, "note": "contradicted"},
            {"claim": "Z", "support": 2, "note": "fully supported"},
        ]
    })
    with patch("eval.judge.get_llm", return_value=_FakeLLM(payload)):
        result = judge_case(
            case_file=_case(),
            typology="STRUCTURING",
            confidence=0.85,
            profiles=_profiles(),
            network=None,
        )
    # 2/3 unsupported
    assert round(result["hallucination_rate"], 3) == 0.667
    # (0 + 0 + 1) / 3 = 0.333
    assert round(result["faithfulness_score"], 3) == 0.333


def test_judge_fails_soft_on_invalid_json():
    with patch("eval.judge.get_llm", return_value=_FakeLLM("not json at all")):
        result = judge_case(
            case_file=_case(),
            typology="STRUCTURING",
            confidence=0.85,
            profiles=_profiles(),
            network=None,
        )
    assert result["faithfulness_score"] is None
    assert result["hallucination_rate"] is None
    assert "judge_failed" in (result["note"] or "")


def test_judge_neutrality_flag_cross_vendor(monkeypatch):
    from core.settings import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("LLM_BACKEND", "anthropic")
    monkeypatch.setenv("JUDGE_BACKEND", "vertex")
    flag = judge_neutrality_flag()
    assert flag["same_vendor"] is False
    assert "cross-vendor" in flag["disclosure"].lower()
    get_settings.cache_clear()


def test_judge_neutrality_flag_same_vendor(monkeypatch):
    from core.settings import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("LLM_BACKEND", "anthropic")
    monkeypatch.setenv("JUDGE_BACKEND", "anthropic")
    flag = judge_neutrality_flag()
    assert flag["same_vendor"] is True
    assert "capability-gap" in flag["disclosure"].lower()
    get_settings.cache_clear()
