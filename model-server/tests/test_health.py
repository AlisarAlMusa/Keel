"""Model-server health endpoint (Phase 0: empty-but-healthy)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from model_server.main import app

client = TestClient(app)


def test_healthz_ok() -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "model-server"
    assert body["models"] == []
