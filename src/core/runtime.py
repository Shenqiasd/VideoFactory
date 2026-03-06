"""
运行时状态辅助模块
用于 Worker 心跳写入和健康探测。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, Any


def worker_heartbeat_path() -> Path:
    """Worker 心跳文件路径"""
    return Path.home() / ".video-factory" / "worker_heartbeat.json"


def write_worker_heartbeat(
    *,
    pid: int,
    interval_seconds: int,
    status: str = "running",
    extra: Dict[str, Any] | None = None,
) -> Path:
    """写入 Worker 心跳"""
    path = worker_heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    payload: Dict[str, Any] = {
        "pid": pid,
        "timestamp": time.time(),
        "interval_seconds": interval_seconds,
        "status": status,
    }
    if extra:
        payload.update(extra)

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_worker_heartbeat(max_age_seconds: int = 90) -> Dict[str, Any]:
    """
    读取 Worker 心跳并返回诊断结果
    """
    path = worker_heartbeat_path()
    if not path.exists():
        return {
            "exists": False,
            "alive": False,
            "path": str(path),
            "reason": "heartbeat_missing",
            "timestamp": None,
            "pid": None,
            "age_seconds": None,
        }

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "exists": True,
            "alive": False,
            "path": str(path),
            "reason": "heartbeat_corrupt",
            "timestamp": None,
            "pid": None,
            "age_seconds": None,
        }

    timestamp = float(payload.get("timestamp", 0) or 0)
    pid = int(payload.get("pid", 0) or 0) or None
    now = time.time()
    age = (now - timestamp) if timestamp else None

    recent_enough = age is not None and age <= max_age_seconds
    pid_alive = False
    if pid:
        try:
            os.kill(pid, 0)
            pid_alive = True
        except OSError:
            pid_alive = False

    alive = bool(recent_enough and pid_alive and payload.get("status") == "running")
    reason = "ok" if alive else (
        "stale" if not recent_enough else (
            "pid_dead" if not pid_alive else "not_running"
        )
    )

    return {
        "exists": True,
        "alive": alive,
        "path": str(path),
        "reason": reason,
        "timestamp": timestamp or None,
        "pid": pid,
        "age_seconds": age,
        "status": payload.get("status"),
        "interval_seconds": payload.get("interval_seconds"),
    }
