import json
import os
import time

from core.runtime import worker_heartbeat_path


def test_health_reports_worker_missing_when_no_heartbeat(client):
    path = worker_heartbeat_path()
    if path.exists():
        path.unlink()

    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()

    assert payload["status"] == "healthy"
    assert payload["worker"]["alive"] is False
    assert payload["worker"]["reason"] in {"heartbeat_missing", "stale", "pid_dead", "not_running"}


def test_health_reports_worker_alive_with_fresh_heartbeat(client):
    path = worker_heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "timestamp": time.time(),
                "interval_seconds": 10,
                "status": "running",
            }
        ),
        encoding="utf-8",
    )

    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()

    assert payload["worker"]["alive"] is True
    assert payload["worker"]["reason"] == "ok"
    assert payload["worker"]["pid"] == os.getpid()
    assert isinstance(payload["worker"]["last_heartbeat"], float)


def test_runtime_endpoint_contains_worker_and_queue(client):
    response = client.get("/api/system/runtime")
    assert response.status_code == 200

    payload = response.json()
    assert "worker" in payload
    assert "queue" in payload
    assert set(payload["queue"].keys()) == {"queued", "active", "failed", "total"}
