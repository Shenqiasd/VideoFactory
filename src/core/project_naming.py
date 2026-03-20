"""
任务项目名称解析与标题翻译辅助。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

from core.config import Config
from source.downloader import VideoDownloader
from translation import get_translator

logger = logging.getLogger(__name__)

_DISABLED_ENV_VALUES = {"1", "true", "yes", "on"}


@dataclass
class ResolvedProjectTitles:
    source_title: str = ""
    project_name: str = ""


def normalize_lang_code(language: str) -> str:
    normalized = str(language or "").strip().replace("_", "-")
    if not normalized:
        return "zh-CN"

    mapping = {
        "zh-cn": "zh-CN",
        "zh-hans": "zh-CN",
        "zh-tw": "zh-TW",
        "en-us": "en-US",
        "en-gb": "en-GB",
    }
    lowered = normalized.lower()
    if lowered in mapping:
        return mapping[lowered]
    if "-" in normalized:
        first, second = normalized.split("-", 1)
        return f"{first.lower()}-{second.upper()}"
    return normalized


def is_remote_url(source_url: str) -> bool:
    return str(source_url or "").strip().startswith(("http://", "https://"))


def derive_local_source_title(source_url: str) -> str:
    text = str(source_url or "").strip()
    if not text or is_remote_url(text):
        return ""

    if text.startswith("file://"):
        parsed = urlparse(text)
        text = unquote(parsed.path or "")

    return Path(text).stem.strip()


def build_project_name(
    *,
    translated_title: str = "",
    source_title: str = "",
    source_url: str = "",
    task_id: str = "",
    fallback: str = "未命名任务",
) -> str:
    candidates = [
        str(translated_title or "").strip(),
        str(source_title or "").strip(),
        derive_local_source_title(source_url),
        str(source_url or "").strip(),
        str(task_id or "").strip(),
    ]
    for candidate in candidates:
        if candidate:
            return candidate
    return fallback


def title_resolution_enabled(config: Optional[Config] = None) -> bool:
    disabled = str(os.environ.get("VF_DISABLE_TITLE_RESOLVE", "")).strip().lower()
    if disabled in _DISABLED_ENV_VALUES:
        return False

    cfg = config or Config()
    return bool(cfg.get("tasks", "resolve_titles_on_create", default=True))


def title_resolution_timeout(config: Optional[Config] = None) -> float:
    cfg = config or Config()
    raw_timeout = cfg.get("tasks", "resolve_titles_timeout_seconds", default=8)
    try:
        return max(1.0, min(30.0, float(raw_timeout)))
    except (TypeError, ValueError):
        return 8.0


async def fetch_remote_source_title(
    source_url: str,
    *,
    timeout_seconds: Optional[float] = None,
    downloader: Optional[VideoDownloader] = None,
) -> str:
    if not is_remote_url(source_url):
        return ""

    timeout = 8.0 if timeout_seconds is None else float(timeout_seconds)
    video_downloader = downloader or VideoDownloader(timeout=max(1, int(timeout)))
    try:
        info = await video_downloader.get_video_info(source_url, timeout=timeout)
    except Exception as exc:
        logger.warning("创建任务时获取视频标题失败: %s", exc)
        return ""

    return str((info or {}).get("title", "") or "").strip()


async def translate_project_name(
    source_title: str,
    *,
    source_lang: str,
    target_lang: str,
    translator=None,
) -> str:
    content = str(source_title or "").strip()
    if not content:
        return ""

    normalized_source = normalize_lang_code(source_lang)
    normalized_target = normalize_lang_code(target_lang)
    if normalized_source.lower() == normalized_target.lower():
        return content

    active_translator = translator or get_translator(Config())
    try:
        translated = await active_translator.translate_text(
            text=content,
            source_lang=normalized_source,
            target_lang=normalized_target,
        )
    except Exception as exc:
        logger.warning("项目名称翻译失败，回退原标题: %s", exc)
        return content

    return str(translated or content).strip() or content


async def resolve_project_titles(
    *,
    source_url: str,
    source_lang: str,
    target_lang: str,
    source_title: str = "",
    config: Optional[Config] = None,
    downloader: Optional[VideoDownloader] = None,
    translator=None,
) -> ResolvedProjectTitles:
    cfg = config or Config()
    explicit_source_title = str(source_title or "").strip()

    if not title_resolution_enabled(cfg):
        fallback_source_title = explicit_source_title or derive_local_source_title(source_url)
        return ResolvedProjectTitles(source_title=fallback_source_title, project_name="")

    resolved_source_title = explicit_source_title
    if not resolved_source_title:
        if is_remote_url(source_url):
            resolved_source_title = await fetch_remote_source_title(
                source_url,
                timeout_seconds=title_resolution_timeout(cfg),
                downloader=downloader,
            )
        else:
            resolved_source_title = derive_local_source_title(source_url)

    project_name = await translate_project_name(
        resolved_source_title,
        source_lang=source_lang,
        target_lang=target_lang,
        translator=translator,
    )
    return ResolvedProjectTitles(
        source_title=resolved_source_title,
        project_name=project_name,
    )
