"""Phase 3 — dashboard renderer. Pure HTML generation; no LLM, no DB."""
from __future__ import annotations

from pathlib import Path

from eval.dashboard import render


def _payload(label: str, verdict_acc: float, hallu: float) -> dict:
    return {
        "run_id": f"00000000-0000-0000-0000-0000000000{ord(label[0]):02x}",
        "label": label,
        "backend": "anthropic",
        "started_at": "2026-05-19T10:00:00+00:00",
        "summary": {
            "total": 30,
            "verdict_accuracy": verdict_acc,
            "typology_accuracy": 0.8,
            "network_recall_mean": 0.7,
            "network_precision_mean": 0.85,
            "evidence_recall_mean": 0.75,
            "false_positive_rate": 0.1,
            "false_negative_rate": 0.05,
            "faithfulness_mean": 0.82,
            "hallucination_mean": hallu,
            "by_typology": {
                "STRUCTURING": {
                    "n": 8,
                    "verdict_correct": 7,
                    "typology_correct": 8,
                    "verdict_accuracy": 0.875,
                    "typology_accuracy": 1.0,
                    "network_recall_mean": 0.9,
                }
            },
            "confusion_matrix": {
                "SAR_FILE": {"SAR_FILE": 10, "REVIEW": 1},
                "REVIEW": {"REVIEW": 4, "SAR_FILE": 1},
                "AUTO_CLOSE": {"AUTO_CLOSE": 10},
            },
            "label": label,
            "judge": {
                "agent_backend": "anthropic",
                "judge_backend": "vertex",
                "same_vendor": False,
                "disclosure": "Judge runs on a different vendor than the agents.",
            },
        },
        "results": [
            {
                "alert_id": "ALERT0001",
                "expected_verdict": "SAR_FILE",
                "got_verdict": "SAR_FILE",
                "expected_typology": "STRUCTURING",
                "got_typology": "STRUCTURING",
                "verdict_correct": True,
                "faithfulness_score": 0.9,
                "hallucination_rate": 0.05,
            },
            {
                "alert_id": "ALERT0099",
                "expected_verdict": "SAR_FILE",
                "got_verdict": "AUTO_CLOSE",
                "expected_typology": "STRUCTURING",
                "got_typology": "NONE",
                "verdict_correct": False,
                "faithfulness_score": 0.4,
                "hallucination_rate": 0.6,
            },
        ],
    }


def test_render_two_runs(tmp_path: Path) -> None:
    payloads = [_payload("haiku-4.5", 0.83, 0.08), _payload("sonnet-4.6", 0.93, 0.04)]
    out = tmp_path / "index.html"
    render(payloads, out)
    body = out.read_text(encoding="utf-8")
    assert "FALCON Evaluation" in body
    assert "haiku-4.5" in body
    assert "sonnet-4.6" in body
    # Headline table renders both runs.
    assert body.count("<tr>") >= 4
    # Per-run sections include the worst-case alert.
    assert "ALERT0099" in body


def test_render_same_vendor_disclosure(tmp_path: Path) -> None:
    p = _payload("anthropic-judge", 0.8, 0.1)
    p["summary"]["judge"] = {
        "agent_backend": "anthropic",
        "judge_backend": "anthropic",
        "same_vendor": True,
        "disclosure": "Judge same vendor — capability-gap defence applies.",
    }
    out = tmp_path / "index.html"
    render([p], out)
    body = out.read_text(encoding="utf-8")
    assert "capability-gap" in body
    assert 'class="note warning"' in body


def test_render_empty_raises(tmp_path: Path) -> None:
    import pytest
    with pytest.raises(SystemExit):
        render([], tmp_path / "x.html")
