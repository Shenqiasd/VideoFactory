import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

from api.server import app
from core.config import Config


def _write_config(path: Path, working_dir: Path, output_dir: Path):
    content = f"""
storage:
  r2:
    bucket: videoflow
    rclone_remote: r2
  local:
    mac_working_dir: {working_dir}
    mac_output_dir: {output_dir}
  auto_cleanup:
    enabled: true
    schedule: "0 2 * * *"
    rules:
    - location: local
      path: working
      days: 1
      enabled: true
"""
    path.write_text(content.strip() + "\n", encoding="utf-8")


def test_storage_list_and_delete_local(tmp_path, monkeypatch):
    config_path = tmp_path / "settings.yaml"
    working_dir = tmp_path / "working"
    output_dir = tmp_path / "output"
    working_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    test_file = working_dir / "demo.txt"
    test_file.write_text("hello", encoding="utf-8")

    _write_config(config_path, working_dir, output_dir)
    monkeypatch.setenv("VF_CONFIG", str(config_path))
    Config.reset()

    with TestClient(app) as client:
        resp = client.get("/api/storage/files?location=local&path=working")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["count"] == 1
        assert payload["files"][0]["name"] == "demo.txt"

        delete_resp = client.request(
            "DELETE",
            "/api/storage/files",
            json={"location": "local", "paths": ["demo.txt"]},
        )
        assert delete_resp.status_code == 200
        assert delete_resp.json()["deleted"] == 1
        assert not test_file.exists()


def test_storage_cleanup_local(tmp_path, monkeypatch):
    config_path = tmp_path / "settings.yaml"
    working_dir = tmp_path / "working"
    output_dir = tmp_path / "output"
    working_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    old_file = working_dir / "old.txt"
    new_file = working_dir / "new.txt"
    old_file.write_text("old", encoding="utf-8")
    new_file.write_text("new", encoding="utf-8")

    # make old file 3 days ago
    old_time = int(time.time()) - 3 * 24 * 3600
    os.utime(old_file, (old_time, old_time))

    _write_config(config_path, working_dir, output_dir)
    monkeypatch.setenv("VF_CONFIG", str(config_path))
    Config.reset()

    with TestClient(app) as client:
        resp = client.post(
            "/api/storage/cleanup",
            json={"location": "local", "path": "working", "days": 1},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["deleted"] == 1
        assert not old_file.exists()
        assert new_file.exists()


def test_storage_cleanup_config_update(tmp_path, monkeypatch):
    config_path = tmp_path / "settings.yaml"
    working_dir = tmp_path / "working"
    output_dir = tmp_path / "output"
    working_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    _write_config(config_path, working_dir, output_dir)
    monkeypatch.setenv("VF_CONFIG", str(config_path))
    Config.reset()

    with TestClient(app) as client:
        get_resp = client.get("/api/storage/cleanup-config")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert "rules" in data

        new_config = {
            "enabled": False,
            "schedule": "0 3 * * *",
            "rules": [
                {"location": "local", "path": "output", "days": 2, "enabled": True},
            ],
        }
        put_resp = client.put("/api/storage/cleanup-config", json=new_config)
        assert put_resp.status_code == 200
        updated = put_resp.json()["auto_cleanup"]
        assert updated["schedule"] == "0 3 * * *"
        assert updated["rules"][0]["path"] == "output"
