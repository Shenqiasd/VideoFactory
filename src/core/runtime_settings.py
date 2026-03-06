"""
运行时可写设置（不修改仓库配置文件）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from core.config import Config
from core.subtitle_style import DEFAULT_SUBTITLE_STYLE, normalize_subtitle_style


def _settings_path() -> Path:
    return Path.home() / ".video-factory" / "runtime_settings.json"


def _load_settings() -> Dict[str, Any]:
    path = _settings_path()
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_settings(data: Dict[str, Any]):
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_subtitle_style_defaults() -> Dict[str, int]:
    """
    获取任务默认字幕样式：config 默认值 + runtime 覆盖。
    """
    cfg = Config()
    cfg_defaults = cfg.get("subtitle_style", "defaults", default={}) or {}
    runtime = _load_settings()
    runtime_defaults = runtime.get("subtitle_style_defaults", {})
    merged = dict(DEFAULT_SUBTITLE_STYLE)
    if isinstance(cfg_defaults, dict):
        merged.update(cfg_defaults)
    if isinstance(runtime_defaults, dict):
        merged.update(runtime_defaults)
    return normalize_subtitle_style(merged)


def set_subtitle_style_defaults(style: Dict[str, Any]) -> Dict[str, int]:
    """
    保存任务默认字幕样式。
    """
    normalized = normalize_subtitle_style(style, defaults=get_subtitle_style_defaults())
    runtime = _load_settings()
    runtime["subtitle_style_defaults"] = normalized
    _save_settings(runtime)
    return normalized
