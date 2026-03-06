"""
字幕样式配置与校验。
"""
from __future__ import annotations

from typing import Any, Dict, Optional


DEFAULT_SUBTITLE_STYLE: Dict[str, int] = {
    "cn_font_size": 26,
    "en_font_size": 20,
    "cn_margin_v": 56,
    "en_margin_v": 26,
    "cn_alignment": 2,
    "en_alignment": 2,
}


_STYLE_LIMITS = {
    "cn_font_size": (12, 96),
    "en_font_size": (10, 96),
    "cn_margin_v": (0, 400),
    "en_margin_v": (0, 400),
    "cn_alignment": (1, 9),
    "en_alignment": (1, 9),
}


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_subtitle_style(
    style: Optional[Dict[str, Any]],
    defaults: Optional[Dict[str, Any]] = None,
) -> Dict[str, int]:
    """
    归一化字幕样式：回填默认值 + 限制数值范围。
    """
    base: Dict[str, Any] = dict(DEFAULT_SUBTITLE_STYLE)
    if isinstance(defaults, dict):
        base.update({k: v for k, v in defaults.items() if k in DEFAULT_SUBTITLE_STYLE})
    if isinstance(style, dict):
        base.update({k: v for k, v in style.items() if k in DEFAULT_SUBTITLE_STYLE})

    normalized: Dict[str, int] = {}
    for key, (min_v, max_v) in _STYLE_LIMITS.items():
        raw = _to_int(base.get(key), DEFAULT_SUBTITLE_STYLE[key])
        normalized[key] = max(min_v, min(max_v, raw))

    return normalized
