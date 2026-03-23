"""
Storage management API routes.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth import require_auth
from core.config import Config
from core.storage import StorageManager, LocalStorage

router = APIRouter()


def _config_file_path() -> Path:
    env_path = os.environ.get("VF_CONFIG", "")
    if env_path:
        return Path(env_path)
    return Path(__file__).resolve().parents[2] / "config" / "settings.yaml"


def _ensure_config_file(path: Path) -> None:
    """When settings.yaml is absent, bootstrap it from settings.example.yaml."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    example = path.parent / "settings.example.yaml"
    if example.exists():
        import shutil
        shutil.copy2(example, path)
    else:
        path.write_text("{}\n", encoding="utf-8")


def _read_yaml_config() -> dict[str, Any]:
    path = _config_file_path()
    try:
        _ensure_config_file(path)
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(content, dict):
            raise ValueError("配置文件格式异常")
        return content
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置失败: {str(e)}") from e


def _write_yaml_config(config_data: dict[str, Any]):
    path = _config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(
            yaml.safe_dump(config_data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置失败: {str(e)}") from e


def _get_storage_managers() -> tuple[StorageManager, LocalStorage]:
    cfg = Config()
    storage = StorageManager(
        bucket=cfg.get("storage", "r2", "bucket", default="videoflow"),
        rclone_remote=cfg.get("storage", "r2", "rclone_remote", default="r2"),
    )
    local = LocalStorage(
        working_dir=cfg.get("storage", "local", "mac_working_dir", default="/tmp/video-factory/working"),
        output_dir=cfg.get("storage", "local", "mac_output_dir", default="/tmp/video-factory/output"),
    )
    return storage, local


def _default_cleanup_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "schedule": "0 2 * * *",
        "rules": [
            {"location": "local", "path": "working", "days": 1, "enabled": True},
            {"location": "local", "path": "output", "days": 3, "enabled": True},
            {"location": "r2", "path": "raw", "days": 7, "enabled": True},
            {"location": "r2", "path": "processed", "days": 30, "enabled": True},
            {"location": "r2", "path": "ready", "days": 7, "enabled": True},
            {"location": "r2", "path": "archive", "days": 90, "enabled": True},
        ],
    }


def _cleanup_config_from_settings() -> dict[str, Any]:
    cfg = Config()
    cleanup = cfg.get("storage", "auto_cleanup", default=None)
    if isinstance(cleanup, dict):
        merged = _default_cleanup_config()
        merged.update({k: v for k, v in cleanup.items() if k != "rules"})
        rules = cleanup.get("rules")
        if isinstance(rules, list) and rules:
            merged["rules"] = rules
        return merged
    return _default_cleanup_config()


def _run_cleanup_rule(location: str, path: str, days: int) -> dict[str, Any]:
    storage, local = _get_storage_managers()
    if location == "r2":
        return storage.cleanup_old_files(path, days)
    if location == "local":
        return local.cleanup_old_files(path, days)
    raise HTTPException(status_code=400, detail=f"未知清理位置: {location}")


@router.get("/storage/files")
async def get_storage_files(location: str = "r2", path: str = "raw"):
    """获取存储文件列表"""
    storage, local = _get_storage_managers()

    if location == "r2":
        files = storage.list_files_with_details(path)
    elif location == "local":
        files = local.list_files_with_details(path)
    else:
        raise HTTPException(status_code=400, detail="location 仅支持 r2/local")

    total_size = sum(int(f.get("size", 0) or 0) for f in files)
    return {
        "location": location,
        "path": path,
        "count": len(files),
        "total_size": total_size,
        "total_size_human": StorageManager._format_size(total_size),
        "files": files,
    }


@router.delete("/storage/files", dependencies=[Depends(require_auth)])
async def delete_storage_files(request: Request):
    """删除存储文件"""
    data = await request.json()
    location = (data.get("location") or "r2").strip().lower()
    paths = data.get("paths") or []

    if not isinstance(paths, list) or not paths:
        raise HTTPException(status_code=400, detail="paths 不能为空")

    storage, local = _get_storage_managers()
    if location == "r2":
        deleted = storage.delete_files(paths)
    elif location == "local":
        deleted = local.delete_files(paths)
    else:
        raise HTTPException(status_code=400, detail="location 仅支持 r2/local")

    return {
        "success": True,
        "deleted": deleted,
        "requested": len(paths),
    }


@router.post("/storage/cleanup", dependencies=[Depends(require_auth)])
async def cleanup_storage(request: Request):
    """手动触发清理"""
    data = await request.json()
    location = (data.get("location") or "all").strip().lower()
    path = data.get("path")
    days = data.get("days")

    if location in {"r2", "local"}:
        if not path or days is None:
            raise HTTPException(status_code=400, detail="location 为 r2/local 时需要 path 和 days")
        try:
            days_int = int(days)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="days 必须是整数")
        if days_int <= 0:
            raise HTTPException(status_code=400, detail="days 必须大于 0")
        result = _run_cleanup_rule(location, path, days_int)
        return {"success": True, "location": location, "path": path, **result}

    if location != "all":
        raise HTTPException(status_code=400, detail="location 仅支持 r2/local/all")

    cleanup_config = _cleanup_config_from_settings()
    results: List[Dict[str, Any]] = []
    for rule in cleanup_config.get("rules", []):
        if not rule.get("enabled", True):
            continue
        rule_location = str(rule.get("location", "")).strip().lower()
        rule_path = str(rule.get("path", "")).strip()
        rule_days = rule.get("days")
        if not rule_location or not rule_path or rule_days is None:
            continue
        try:
            rule_days = int(rule_days)
        except (TypeError, ValueError):
            continue
        result = _run_cleanup_rule(rule_location, rule_path, rule_days)
        results.append({
            "location": rule_location,
            "path": rule_path,
            "days": rule_days,
            **result,
        })

    return {"success": True, "results": results}


@router.get("/storage/cleanup-config")
async def get_cleanup_config():
    """获取清理配置"""
    return _cleanup_config_from_settings()


@router.put("/storage/cleanup-config", dependencies=[Depends(require_auth)])
async def update_cleanup_config(request: Request):
    """更新清理配置"""
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="配置格式错误")

    config_data = _read_yaml_config()
    storage_cfg = config_data.get("storage") if isinstance(config_data.get("storage"), dict) else {}
    storage_cfg = dict(storage_cfg)
    storage_cfg["auto_cleanup"] = data
    config_data["storage"] = storage_cfg

    _write_yaml_config(config_data)
    Config.reset()

    return {
        "success": True,
        "config_path": str(_config_file_path()),
        "auto_cleanup": data,
        "message": "存储清理配置已保存（Worker 需重启后生效）",
    }
