"""Phase 4 — FastAPI surface tests via TestClient.

These hit the same Postgres the supervisor uses (compose stack must be up).
They exercise endpoint wiring, serialisation, status codes, and the
metrics/eval surfaces. Investigation-triggering tests stub the supervisor
call so the test suite stays cheap.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from api.main import app
from core.db import session_scope
from core.models import FraudAlert, Investigation


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def real_alert_id() -> str:
    with session_scope() as s:
        a = s.execute(select(FraudAlert).limit(1)).scalar_one_or_none()
        if a is None:
            pytest.skip("No fraud_alerts seeded — run `python -m data.generate`.")
        return a.id


# ---------- liveness + pages ----------


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_dashboard_serves_html(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "FALCON" in body
    # Vanilla — no React bundle, must be a single self-contained page.
    assert "<script" in body
    assert "/investigations" in body  # the JS hits this endpoint


# ---------- alerts ----------


def test_list_alerts_returns_array(client: TestClient, real_alert_id: str) -> None:
    r = client.get("/alerts")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    ids = {a["id"] for a in data}
    assert real_alert_id in ids
    sample = next(a for a in data if a["id"] == real_alert_id)
    assert "account_id" in sample
    assert "alert_type" in sample
    assert "latest_investigation" in sample  # may be None or dict


# ---------- investigations list/detail ----------


def test_list_investigations(client: TestClient) -> None:
    r = client.get("/investigations?limit=10")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_list_investigations_status_filter(client: TestClient) -> None:
    r = client.get("/investigations?status=completed&limit=5")
    assert r.status_code == 200
    for row in r.json():
        assert row["status"] == "completed"


def test_get_investigation_404(client: TestClient) -> None:
    r = client.get(f"/investigations/{uuid.uuid4()}")
    assert r.status_code == 404


def test_get_investigation_includes_case(client: TestClient) -> None:
    """Pick any completed investigation and confirm shape."""
    with session_scope() as s:
        inv = s.execute(
            select(Investigation).where(Investigation.status == "completed").limit(1)
        ).scalar_one_or_none()
        if inv is None:
            pytest.skip("No completed investigations yet.")
        inv_id = inv.id
    r = client.get(f"/investigations/{inv_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(inv_id)
    assert "case_file" in body  # may be None on auto_closed paths


# ---------- events + traces ----------


def test_list_events_for_real_investigation(client: TestClient) -> None:
    with session_scope() as s:
        inv = s.execute(select(Investigation).limit(1)).scalar_one_or_none()
        if inv is None:
            pytest.skip("No investigations seeded.")
        inv_id = inv.id
    r = client.get(f"/investigations/{inv_id}/events")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_list_traces_filter(client: TestClient) -> None:
    with session_scope() as s:
        inv = s.execute(select(Investigation).limit(1)).scalar_one_or_none()
        if inv is None:
            pytest.skip("No investigations seeded.")
        inv_id = inv.id
    r = client.get(f"/investigations/{inv_id}/traces?agent=triage")
    assert r.status_code == 200
    for row in r.json():
        assert row["agent_name"] == "triage"


# ---------- trigger ----------


def test_trigger_investigation_unknown_alert(client: TestClient) -> None:
    r = client.post("/investigations", json={"alert_id": "DOES_NOT_EXIST"})
    assert r.status_code == 404


def test_trigger_investigation_schedules_background(
    client: TestClient, real_alert_id: str
) -> None:
    """The supervisor graph call is stubbed — we only assert wiring + 202
    and that the new investigation_id is returned to the client."""
    with patch("api.main.run_investigation_graph") as mock_run:
        r = client.post("/investigations", json={"alert_id": real_alert_id})
        assert r.status_code == 202
        body = r.json()
        assert body["alert_id"] == real_alert_id
        assert "investigation_id" in body
        # uuid round-trips
        uuid.UUID(body["investigation_id"])
    # FastAPI TestClient runs background tasks synchronously after the response.
    mock_run.assert_called_once()


# ---------- annotate ----------


def test_annotate_409_when_not_paused(client: TestClient) -> None:
    with session_scope() as s:
        inv = s.execute(
            select(Investigation).where(Investigation.status != "paused_hitl").limit(1)
        ).scalar_one_or_none()
        if inv is None:
            pytest.skip("No non-paused investigation available.")
        inv_id = inv.id
    r = client.post(
        f"/investigations/{inv_id}/annotate",
        json={"reviewer_id": "test", "note": "n/a"},
    )
    assert r.status_code == 409


# ---------- cases ----------


def test_get_case_404(client: TestClient) -> None:
    r = client.get(f"/cases/{uuid.uuid4()}")
    assert r.status_code == 404


# ---------- eval ----------


def test_eval_latest_shape(client: TestClient) -> None:
    r = client.get("/eval/latest")
    assert r.status_code == 200
    body = r.json()
    assert "run" in body
    assert "results" in body
    if body["run"] is not None:
        assert "summary" in body["run"]
        assert "id" in body["run"]


# ---------- metrics ----------


def test_demo_unknown_typology_404(client: TestClient) -> None:
    r = client.get("/demo/NOT_A_REAL_TYPOLOGY")
    assert r.status_code == 404


def test_demo_typology_returns_200_or_404(client: TestClient) -> None:
    """If the cache file exists, expect 200 with the documented shape; if it
    doesn't, expect 404 with a helpful capture hint in the detail. Either is
    a valid state for the test environment."""
    r = client.get("/demo/STRUCTURING")
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        body = r.json()
        for key in ("typology", "captured_at", "alert_id", "investigation", "events", "traces"):
            assert key in body, f"missing key {key!r} in demo cache payload"
        assert body["typology"] == "STRUCTURING"
    else:
        assert "capture_demo_runs" in (r.json().get("detail") or "")


def test_metrics_shape(client: TestClient) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.json()
    for key in (
        "investigations_by_status",
        "duration",
        "slow_investigations",
        "per_agent_latency",
        "tool_call_counts",
        "total_tokens",
    ):
        assert key in body
    assert isinstance(body["per_agent_latency"], dict)
    assert isinstance(body["tool_call_counts"], dict)
