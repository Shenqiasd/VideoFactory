"""
任务管理路由 - CRUD + 状态查询
"""
import logging
import time
import os
import asyncio
import uuid
import re
import mimetypes
from typing import Optional, List, Any, Dict
from fastapi import APIRouter, HTTPException, Form, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from pathlib import Path
import hashlib

from api.rate_limit import limiter
from core.project_naming import is_remote_url, resolve_project_titles
from core.task import Task, TaskState, TaskStore, VALID_SCOPES, SCOPE_DEFAULTS, normalize_creation_config
from core.config import Config
from core.storage import StorageManager
from core.runtime_settings import get_subtitle_style_defaults
from core.subtitle_style import normalize_subtitle_style
from factory.long_video import LongVideoProcessor
from source.ytdlp_runtime import build_ytdlp_base_cmd

logger = logging.getLogger(__name__)
router = APIRouter()

# 全局TaskStore实例
_task_store: Optional[TaskStore] = None


def get_task_store() -> TaskStore:
    global _task_store
    if _task_store is None:
        _task_store = TaskStore()
    return _task_store


def _is_checked(value: Optional[str]) -> bool:
    """兼容HTML checkbox值"""
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _prefers_html_navigation(request: Request) -> bool:
    accept = str(request.headers.get("accept", "")).lower()
    sec_fetch_mode = str(request.headers.get("sec-fetch-mode", "")).lower()
    return "text/html" in accept or sec_fetch_mode == "navigate"


async def _run_production(task_id: str):
    """后台触发生产流程（前端快捷创建场景）"""
    from production.pipeline import ProductionPipeline

    store = get_task_store()
    task = store.get(task_id)
    if not task:
        logger.warning(f"后台启动生产失败，任务不存在: {task_id}")
        return

    pipeline = ProductionPipeline(task_store=store)
    await pipeline.run(task)


# ========== 请求/响应模型 ==========

class TaskCreateRequest(BaseModel):
    """创建任务请求"""
    source_url: str = Field(..., description="视频URL或本地路径")
    source_title: str = Field("", description="视频标题")
    source_lang: str = Field("en", description="源语言")
    target_lang: str = Field("zh_cn", description="目标语言")
    task_scope: str = Field("full", description="任务范围: subtitle_only/subtitle_dub/dub_and_copy/full")
    enable_tts: Optional[bool] = Field(None, description="启用配音（可选，覆盖scope默认值）")
    enable_short_clips: Optional[bool] = Field(None, description="启用短视频切片（可选，覆盖scope默认值）")
    enable_article: Optional[bool] = Field(None, description="启用图文生成（可选，覆盖scope默认值）")
    embed_subtitle_type: Optional[str] = Field(None, description="字幕嵌入类型（可选，覆盖scope默认值）: horizontal/vertical/none")
    subtitle_style: Optional[dict] = Field(None, description="字幕样式（可选）")
    creation_config: Optional[dict] = Field(None, description="创作配置（可选）")
    priority: int = Field(2, description="优先级: 0=紧急 1=高 2=普通 3=低")


class TaskResponse(BaseModel):
    """任务响应"""
    task_id: str
    project_name: str
    source_url: str
    source_title: str
    state: str
    progress: int
    error_message: str
    last_error_code: str = ""
    last_step: str = ""
    task_scope: str = "full"
    subtitle_style: dict = Field(default_factory=dict)
    creation_config: dict = Field(default_factory=dict)
    creation_state: dict = Field(default_factory=dict)
    creation_status: dict = Field(default_factory=dict)
    created_at: float
    updated_at: float
    products_count: int = 0


class TaskDetailResponse(BaseModel):
    """任务详情响应"""
    task_id: str
    project_name: str
    source_url: str
    source_title: str
    source_lang: str
    target_lang: str
    state: str
    progress: int
    error_message: str
    last_error_code: str
    last_step: str
    task_scope: str = "full"
    subtitle_style: dict = Field(default_factory=dict)
    creation_config: dict = Field(default_factory=dict)
    creation_state: dict = Field(default_factory=dict)
    creation_status: dict = Field(default_factory=dict)
    retry_count: int
    created_at: float
    updated_at: float
    completed_at: float
    translated_title: str
    translated_description: str
    qc_score: float
    qc_details: str
    global_review_report: dict = Field(default_factory=dict)
    products: list
    timeline: list
    translation_task_id: str
    translation_progress: int
    duration_seconds: float
    publish_accounts: dict = Field(default_factory=dict)
    publish_account_details: dict = Field(default_factory=dict)


class TaskStatsResponse(BaseModel):
    """任务统计响应"""
    stats: dict
    active_count: int
    total_count: int


class SubtitlePreviewRequest(BaseModel):
    source_url: str = Field(..., description="视频URL或本地路径")
    source_lang: str = Field("en", description="源语言")
    target_lang: str = Field("zh_cn", description="目标语言")
    task_scope: str = Field("subtitle_only", description="任务范围")
    subtitle_style: Optional[dict] = Field(None, description="字幕样式")


def _build_scope_options(
    scope: str,
    *,
    enable_tts: Optional[bool] = None,
    enable_short_clips: Optional[bool] = None,
    enable_article: Optional[bool] = None,
    embed_subtitle_type: Optional[str] = None,
) -> dict:
    """以 scope 默认值为基线，并应用显式覆盖项。"""
    options = dict(SCOPE_DEFAULTS[scope])

    if enable_tts is not None:
        options["enable_tts"] = enable_tts
    if enable_short_clips is not None:
        options["enable_short_clips"] = enable_short_clips
    if enable_article is not None:
        options["enable_article"] = enable_article
    if embed_subtitle_type in {"horizontal", "vertical", "none"}:
        options["embed_subtitle_type"] = embed_subtitle_type

    return options


def _resolve_subtitle_style(style: Optional[dict]) -> dict:
    return normalize_subtitle_style(style, defaults=get_subtitle_style_defaults())


async def _resolve_titles_for_task(
    *,
    source_url: str,
    source_title: str,
    source_lang: str,
    target_lang: str,
) -> tuple[str, str]:
    resolved = await resolve_project_titles(
        source_url=source_url,
        source_title=source_title,
        source_lang=source_lang,
        target_lang=target_lang,
    )
    return resolved.source_title, resolved.project_name


def _extract_form_subtitle_style(
    *,
    subtitle_cn_font_size: Optional[str] = None,
    subtitle_en_font_size: Optional[str] = None,
    subtitle_cn_margin_v: Optional[str] = None,
    subtitle_en_margin_v: Optional[str] = None,
    subtitle_cn_alignment: Optional[str] = None,
    subtitle_en_alignment: Optional[str] = None,
) -> dict:
    raw: dict = {}

    def _maybe_int(value: Optional[str]) -> Optional[int]:
        if value is None:
            return None
        v = value.strip()
        if not v:
            return None
        try:
            return int(v)
        except ValueError:
            return None

    mapping = {
        "cn_font_size": _maybe_int(subtitle_cn_font_size),
        "en_font_size": _maybe_int(subtitle_en_font_size),
        "cn_margin_v": _maybe_int(subtitle_cn_margin_v),
        "en_margin_v": _maybe_int(subtitle_en_margin_v),
        "cn_alignment": _maybe_int(subtitle_cn_alignment),
        "en_alignment": _maybe_int(subtitle_en_alignment),
    }
    for key, value in mapping.items():
        if value is not None:
            raw[key] = value

    return _resolve_subtitle_style(raw)


def _extract_form_creation_config(
    *,
    creation_clip_count: Optional[str] = None,
    creation_duration_min: Optional[str] = None,
    creation_duration_max: Optional[str] = None,
    creation_crop_mode: Optional[str] = None,
    creation_review_mode: Optional[str] = None,
    creation_platforms: Optional[List[str]] = None,
    creation_bgm_path: Optional[str] = None,
    creation_bgm_volume: Optional[str] = None,
    creation_intro_path: Optional[str] = None,
    creation_outro_path: Optional[str] = None,
    creation_transition: Optional[str] = None,
    creation_transition_duration: Optional[str] = None,
) -> Dict[str, Any]:
    raw: Dict[str, Any] = {}

    def _maybe_int(value: Optional[str]) -> Optional[int]:
        if value is None:
            return None
        v = value.strip()
        if not v:
            return None
        try:
            return int(v)
        except ValueError:
            return None

    def _maybe_float(value: Optional[str]) -> Optional[float]:
        if value is None:
            return None
        v = value.strip()
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None

    mapping = {
        "clip_count": _maybe_int(creation_clip_count),
        "duration_min": _maybe_int(creation_duration_min),
        "duration_max": _maybe_int(creation_duration_max),
        "bgm_volume": _maybe_float(creation_bgm_volume),
        "transition_duration": _maybe_float(creation_transition_duration),
    }
    for key, value in mapping.items():
        if value is not None:
            raw[key] = value

    for key, value in {
        "crop_mode": creation_crop_mode,
        "review_mode": creation_review_mode,
        "bgm_path": creation_bgm_path,
        "intro_path": creation_intro_path,
        "outro_path": creation_outro_path,
        "transition": creation_transition,
    }.items():
        normalized = str(value or '').strip()
        if normalized:
            raw[key] = normalized

    normalized_platforms: List[str] = []
    raw_platform_items = creation_platforms or []
    if isinstance(raw_platform_items, str):
        raw_platform_items = [part for part in raw_platform_items.split(',')]
    for item in raw_platform_items:
        parts = [part for part in str(item or '').split(',')]
        for part in parts:
            value = str(part or '').strip().lower()
            if value and value not in normalized_platforms:
                normalized_platforms.append(value)
    if normalized_platforms:
        raw["platforms"] = normalized_platforms

    return raw


def _preview_root() -> Path:
    return Path("/tmp/video-factory/previews")


def _preview_entry_path(preview_id: str) -> Path:
    return _preview_root() / preview_id / "preview.mp4"


def _get_cookie_file_path() -> Path:
    cfg = Config()
    working_dir = Path(cfg.get("storage", "local", "mac_working_dir", default="/tmp/video-factory/working"))
    return working_dir.parent / "config" / "youtube_cookies.txt"


async def _download_preview_source(source_url: str, output_path: Path) -> tuple[bool, str]:
    cfg = Config()
    ffmpeg = cfg.get("ffmpeg", "path", default="ffmpeg")

    if source_url.startswith(("http://", "https://")):
        cmd = build_ytdlp_base_cmd() + [
            "-f", "best[height<=720]/best",
            "--download-sections", "*0-8",
            "--merge-output-format", "mp4",
            "-o", str(output_path),
            "--no-playlist",
        ]
        cookies = _get_cookie_file_path()
        if cookies.exists():
            cmd.extend(["--cookies", str(cookies)])
        cmd.append(source_url)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            return False, f"预览下载失败: {stderr.decode(errors='ignore')[:300]}"
        if not output_path.exists() or output_path.stat().st_size < 100_000:
            return False, "预览下载失败: 视频片段为空或过小"
        return True, ""

    source_path = Path(source_url)
    if not source_path.exists():
        return False, "本地视频路径不存在"

    cmd = [
        ffmpeg,
        "-i", str(source_path),
        "-t", "8",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "24",
        "-c:a", "aac",
        "-y",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        return False, f"预览截取失败: {stderr.decode(errors='ignore')[-300:]}"
    return True, ""


def _make_artifact_id(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]


def _safe_download_stem(text: str, fallback: str) -> str:
    normalized = re.sub(r"\s+", "_", (text or "").strip())
    normalized = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("._ ")
    return (normalized[:96] or fallback).strip("._ ") or fallback


def _task_download_stem(task: Task) -> str:
    title = str(getattr(task, "project_name", "") or "").strip()
    if not title or is_remote_url(title):
        return f"task_{task.task_id[:8]}"
    return _safe_download_stem(title, f"task_{task.task_id[:8]}")


def _artifact_type_label(artifact_type: str, display_name: str, file_path: Path) -> str:
    label_map = {
        "subtitle": "双语字幕",
        "video": "翻译视频",
        "audio": "配音音频",
        "source_video": "源视频",
        "long_video": "长视频",
        "short_clip": "短视频",
        "article": "图文",
        "cover": "封面",
    }
    if artifact_type in label_map:
        return label_map[artifact_type]
    raw_label = Path(display_name).stem if Path(display_name).suffix else str(display_name or "").strip()
    return raw_label or file_path.stem or "产物"


def _artifact_platform_token(platform: str) -> str:
    token_map = {
        "bilibili": "B站",
        "douyin": "抖音",
        "xiaohongshu": "小红书",
        "youtube": "YouTube",
        "wechat": "微信",
        "weixin": "视频号",
    }
    normalized = str(platform or "").strip().lower()
    if normalized in {"", "all"}:
        return ""
    return token_map.get(normalized, str(platform or "").strip())


def _artifact_variant_token(artifact_type: str, variant_label: str) -> str:
    raw = str(variant_label or "").strip()
    if not raw:
        return ""
    if artifact_type == "cover":
        cover_map = {
            "horizontal": "横版",
            "vertical": "竖版",
        }
        return cover_map.get(raw.lower(), raw)
    return raw


def _build_download_base_stem(
    task: Task,
    file_path: Path,
    *,
    artifact_type: str,
    display_name: str,
    platform: str = "",
    variant_label: str = "",
) -> str:
    task_stem = _task_download_stem(task)
    type_stem = _safe_download_stem(
        _artifact_type_label(artifact_type, display_name, file_path),
        file_path.stem or "artifact",
    )
    tokens = [task_stem, type_stem]
    platform_token = _artifact_platform_token(platform)
    if platform_token:
        tokens.append(_safe_download_stem(platform_token, platform_token))
    variant_token = _artifact_variant_token(artifact_type, variant_label)
    if variant_token:
        tokens.append(_safe_download_stem(variant_token, variant_token))
    return "_".join(token for token in tokens if token)


def _collect_task_artifacts(task: Task) -> List[dict]:
    """汇总任务可下载产物（去重）。"""
    artifacts: List[dict] = []
    seen_paths = set()
    base_totals: dict[tuple[str, str], int] = {}
    base_indexes: dict[tuple[str, str], int] = {}

    def add_artifact(
        path: str,
        *,
        display_name: str,
        artifact_type: str,
        source: str,
        r2_path: str = "",
        platform: str = "",
        variant_label: str = "",
    ):
        if not path:
            return
        normalized_path = str(Path(path))
        if normalized_path in seen_paths:
            return
        seen_paths.add(normalized_path)

        file_path = Path(normalized_path)
        exists = file_path.exists() and file_path.is_file()
        size_bytes = file_path.stat().st_size if exists else 0
        # 过滤明显损坏的旧翻译链路占位文件（例如 48B 的 video_with_tts.mp4）
        if (
            exists
            and file_path.name == "video_with_tts.mp4"
            and size_bytes < 100_000
            and not r2_path
        ):
            return
        artifact_id = _make_artifact_id(normalized_path)
        base_stem = _build_download_base_stem(
            task,
            file_path,
            artifact_type=artifact_type,
            display_name=display_name,
            platform=platform,
            variant_label=variant_label,
        )
        suffix = file_path.suffix or ""
        base_key = (base_stem.lower(), suffix.lower())
        base_totals[base_key] = base_totals.get(base_key, 0) + 1

        artifacts.append(
            {
                "artifact_id": artifact_id,
                "name": display_name,
                "type": artifact_type,
                "source": source,
                "local_path": normalized_path,
                "filename": file_path.name or f"{artifact_id}.bin",
                "exists": exists,
                "size_bytes": size_bytes,
                "r2_path": r2_path,
                "downloadable": bool(exists or r2_path),
                "download_url": f"/api/tasks/{task.task_id}/artifacts/{artifact_id}/download",
                "_base_stem": base_stem,
                "_base_key": base_key,
                "_variant_label": variant_label,
            }
        )

    # 核心翻译产物
    add_artifact(
        task.subtitle_path,
        display_name="双语字幕",
        artifact_type="subtitle",
        source="task.subtitle_path",
    )
    add_artifact(
        task.translated_video_path,
        display_name="翻译后视频",
        artifact_type="video",
        source="task.translated_video_path",
    )
    add_artifact(
        getattr(task, "tts_audio_path", ""),
        display_name="配音音频",
        artifact_type="audio",
        source="task.tts_audio_path",
    )
    if task.source_local_path:
        add_artifact(
            task.source_local_path,
            display_name="源视频",
            artifact_type="source_video",
            source="task.source_local_path",
            r2_path=task.source_r2_path,
        )
    elif task.source_r2_path:
        add_artifact(
            f"/__r2__/{task.source_r2_path}",
            display_name=Path(task.source_r2_path).name or "source_video.mp4",
            artifact_type="source_video",
            source="task.source_r2_path",
            r2_path=task.source_r2_path,
        )

    # products 列表中的成品
    products = task.products if isinstance(task.products, list) else []
    for idx, product in enumerate(products):
        if not isinstance(product, dict):
            continue
        local_path = product.get("local_path", "")
        r2_path = product.get("r2_path", "")
        if not local_path and not r2_path:
            continue
        product_type = product.get("type", "product")
        filename = Path(local_path).name if local_path else (Path(r2_path).name if r2_path else "")
        display_name = product.get("title") or filename or f"product-{idx + 1}"
        path_or_placeholder = local_path or f"/__r2__/{r2_path}"
        add_artifact(
            path_or_placeholder,
            display_name=display_name,
            artifact_type=product_type,
            source=f"task.products[{idx}]",
            r2_path=r2_path,
            platform=product.get("platform", ""),
            variant_label=((product.get("metadata", {}) or {}).get("cover_type", "") if product_type == "cover" else ""),
        )

    # 补充扫描工作目录/输出目录，避免遗漏未登记文件
    cfg = Config()
    working_root = Path(cfg.get("storage", "local", "mac_working_dir", default="/tmp/video-factory/working"))
    output_root = Path(cfg.get("storage", "local", "mac_output_dir", default="/tmp/video-factory/output"))
    candidates = [
        working_root / task.task_id / "bilingual_srt.srt",
        working_root / task.task_id / "target_language_srt.srt",
        working_root / task.task_id / "origin_language_srt.srt",
        working_root / task.task_id / "global_review_report.json",
        working_root / task.task_id / "output" / "video_with_tts.mp4",
        working_root / task.task_id / "output" / "horizontal_embed.mp4",
        working_root / task.task_id / "output" / "vertical_embed.mp4",
    ]
    candidates.extend(sorted((working_root / task.task_id).glob("tts_final_audio.*")))
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            add_artifact(
                str(candidate),
                display_name=candidate.name,
                artifact_type="file",
                source="working_dir_scan",
            )

    output_dir = output_root / task.task_id
    if output_dir.exists():
        for file_path in output_dir.rglob("*"):
            if file_path.is_file():
                add_artifact(
                    str(file_path),
                    display_name=file_path.name,
                    artifact_type="file",
                    source="output_dir_scan",
                )

    for artifact in artifacts:
        base_key = artifact["_base_key"]
        next_index = base_indexes.get(base_key, 0) + 1
        base_indexes[base_key] = next_index
        needs_sequence = (
            artifact["type"] in {"short_clip", "article"}
            or base_totals.get(base_key, 0) > 1
            or (artifact["type"] == "cover" and not artifact["_variant_label"])
        )
        suffix = Path(artifact["filename"]).suffix or Path(artifact["local_path"]).suffix or ""
        if needs_sequence:
            artifact["download_filename"] = f"{artifact['_base_stem']}_{next_index:02d}{suffix}"
        else:
            artifact["download_filename"] = f"{artifact['_base_stem']}{suffix}"
        artifact.pop("_base_stem", None)
        artifact.pop("_base_key", None)
        artifact.pop("_variant_label", None)

    return artifacts


# ========== 路由 ==========

@router.post("/", response_model=dict)
@limiter.limit("10/minute")
async def create_task(request: Request, body: TaskCreateRequest):
    """创建新任务"""
    store = get_task_store()
    resolved_source_title, resolved_project_name = await _resolve_titles_for_task(
        source_url=body.source_url,
        source_title=body.source_title,
        source_lang=body.source_lang,
        target_lang=body.target_lang,
    )

    # 验证 scope，无效则回退 full
    scope = body.task_scope if body.task_scope in VALID_SCOPES else "full"
    options = _build_scope_options(
        scope,
        enable_tts=body.enable_tts,
        enable_short_clips=body.enable_short_clips,
        enable_article=body.enable_article,
        embed_subtitle_type=body.embed_subtitle_type,
    )

    task = store.create(
        source_url=body.source_url,
        source_title=resolved_source_title,
        translated_title=resolved_project_name,
        source_lang=body.source_lang,
        target_lang=body.target_lang,
        task_scope=scope,
        enable_tts=options["enable_tts"],
        enable_short_clips=options["enable_short_clips"],
        enable_article=options["enable_article"],
        embed_subtitle_type=options["embed_subtitle_type"],
        subtitle_style=_resolve_subtitle_style(body.subtitle_style),
        creation_config=normalize_creation_config(
            body.creation_config,
            enable_short_clips=options["enable_short_clips"],
        ),
        priority=body.priority,
    )

    logger.info(f"📝 API创建任务: {task.task_id} (scope={scope})")

    return {
        "task_id": task.task_id,
        "message": "任务创建成功",
        "state": task.state,
    }


@router.post("/create", response_model=dict)
@limiter.limit("10/minute")
async def create_task_compat(
    request: Request,
    youtube_url: str = Form(...),
    source_lang: str = Form("en"),
    target_lang: str = Form("zh_cn"),
    task_scope: str = Form("full"),
    subtitle_cn_font_size: Optional[str] = Form(None),
    subtitle_en_font_size: Optional[str] = Form(None),
    subtitle_cn_margin_v: Optional[str] = Form(None),
    subtitle_en_margin_v: Optional[str] = Form(None),
    subtitle_cn_alignment: Optional[str] = Form(None),
    subtitle_en_alignment: Optional[str] = Form(None),
    creation_clip_count: Optional[str] = Form(None),
    creation_duration_min: Optional[str] = Form(None),
    creation_duration_max: Optional[str] = Form(None),
    creation_crop_mode: Optional[str] = Form(None),
    creation_review_mode: Optional[str] = Form(None),
    creation_platforms: Optional[str] = Form(None),
    creation_bgm_path: Optional[str] = Form(None),
    creation_bgm_volume: Optional[str] = Form(None),
    creation_intro_path: Optional[str] = Form(None),
    creation_outro_path: Optional[str] = Form(None),
    creation_transition: Optional[str] = Form(None),
    creation_transition_duration: Optional[str] = Form(None),
    create_clips: Optional[str] = Form(None),
    create_article: Optional[str] = Form(None),
    auto_run: Optional[str] = Form(None),
    background_tasks: BackgroundTasks = None,
):
    """兼容Web表单：创建单个任务"""
    store = get_task_store()
    resolved_source_title, resolved_project_name = await _resolve_titles_for_task(
        source_url=youtube_url,
        source_title="",
        source_lang=source_lang,
        target_lang=target_lang,
    )

    # 验证 scope，根据 scope 推导默认开关
    scope = task_scope if task_scope in VALID_SCOPES else "full"
    options = _build_scope_options(scope)
    # 仅在表单显式传值时覆盖默认值（兼容旧客户端不传字段）
    if create_clips is not None:
        options["enable_short_clips"] = _is_checked(create_clips)
    if create_article is not None:
        options["enable_article"] = _is_checked(create_article)
    subtitle_style = _extract_form_subtitle_style(
        subtitle_cn_font_size=subtitle_cn_font_size,
        subtitle_en_font_size=subtitle_en_font_size,
        subtitle_cn_margin_v=subtitle_cn_margin_v,
        subtitle_en_margin_v=subtitle_en_margin_v,
        subtitle_cn_alignment=subtitle_cn_alignment,
        subtitle_en_alignment=subtitle_en_alignment,
    )

    form = await request.form()
    creation_config = _extract_form_creation_config(
        creation_clip_count=creation_clip_count,
        creation_duration_min=creation_duration_min,
        creation_duration_max=creation_duration_max,
        creation_crop_mode=creation_crop_mode,
        creation_review_mode=creation_review_mode,
        creation_platforms=form.getlist("creation_platforms"),
        creation_bgm_path=creation_bgm_path,
        creation_bgm_volume=creation_bgm_volume,
        creation_intro_path=creation_intro_path,
        creation_outro_path=creation_outro_path,
        creation_transition=creation_transition,
        creation_transition_duration=creation_transition_duration,
    )

    task = store.create(
        source_url=youtube_url,
        source_title=resolved_source_title,
        translated_title=resolved_project_name,
        source_lang=source_lang,
        target_lang=target_lang,
        task_scope=scope,
        enable_tts=options["enable_tts"],
        enable_short_clips=options["enable_short_clips"],
        enable_article=options["enable_article"],
        embed_subtitle_type=options["embed_subtitle_type"],
        subtitle_style=subtitle_style,
        creation_config=normalize_creation_config(creation_config, enable_short_clips=options["enable_short_clips"]),
        priority=2,
    )

    logger.info(f"📝 Web表单创建任务: {task.task_id} (scope={scope})")
    should_auto_run = _is_checked(auto_run)
    if should_auto_run and background_tasks is not None:
        background_tasks.add_task(_run_production, task.task_id)
        logger.info(f"🚀 Web表单自动启动生产管线: {task.task_id}")

    payload = {
        "task_id": task.task_id,
        "message": "任务创建成功",
        "state": task.state,
        "auto_run": should_auto_run,
    }
    if request.headers.get("HX-Request", "").lower() == "true":
        return JSONResponse(payload, headers={"HX-Redirect": "/tasks"})
    if _prefers_html_navigation(request):
        return RedirectResponse(url="/tasks", status_code=303)
    return payload


@router.post("/batch-create", response_model=dict)
@limiter.limit("3/minute")
async def batch_create_tasks(
    request: Request,
    urls: str = Form(...),
    source_lang: str = Form("en"),
    target_lang: str = Form("zh_cn"),
    task_scope: str = Form("full"),
    subtitle_cn_font_size: Optional[str] = Form(None),
    subtitle_en_font_size: Optional[str] = Form(None),
    subtitle_cn_margin_v: Optional[str] = Form(None),
    subtitle_en_margin_v: Optional[str] = Form(None),
    subtitle_cn_alignment: Optional[str] = Form(None),
    subtitle_en_alignment: Optional[str] = Form(None),
    creation_clip_count: Optional[str] = Form(None),
    creation_duration_min: Optional[str] = Form(None),
    creation_duration_max: Optional[str] = Form(None),
    creation_crop_mode: Optional[str] = Form(None),
    creation_review_mode: Optional[str] = Form(None),
    creation_platforms: Optional[str] = Form(None),
    creation_bgm_path: Optional[str] = Form(None),
    creation_bgm_volume: Optional[str] = Form(None),
    creation_intro_path: Optional[str] = Form(None),
    creation_outro_path: Optional[str] = Form(None),
    creation_transition: Optional[str] = Form(None),
    creation_transition_duration: Optional[str] = Form(None),
    auto_run: Optional[str] = Form(None),
    background_tasks: BackgroundTasks = None,
):
    """兼容Web表单：批量创建任务"""
    raw_urls = [line.strip() for line in urls.splitlines() if line.strip()]
    deduped_urls = []
    seen = set()
    for url in raw_urls:
        if url in seen:
            continue
        seen.add(url)
        deduped_urls.append(url)

    if not deduped_urls:
        raise HTTPException(status_code=400, detail="未提供有效URL")

    # 验证 scope，根据 scope 推导默认开关
    scope = task_scope if task_scope in VALID_SCOPES else "full"
    options = _build_scope_options(scope)
    subtitle_style = _extract_form_subtitle_style(
        subtitle_cn_font_size=subtitle_cn_font_size,
        subtitle_en_font_size=subtitle_en_font_size,
        subtitle_cn_margin_v=subtitle_cn_margin_v,
        subtitle_en_margin_v=subtitle_en_margin_v,
        subtitle_cn_alignment=subtitle_cn_alignment,
        subtitle_en_alignment=subtitle_en_alignment,
    )

    form = await request.form()
    creation_config = _extract_form_creation_config(
        creation_clip_count=creation_clip_count,
        creation_duration_min=creation_duration_min,
        creation_duration_max=creation_duration_max,
        creation_crop_mode=creation_crop_mode,
        creation_review_mode=creation_review_mode,
        creation_platforms=form.getlist("creation_platforms"),
        creation_bgm_path=creation_bgm_path,
        creation_bgm_volume=creation_bgm_volume,
        creation_intro_path=creation_intro_path,
        creation_outro_path=creation_outro_path,
        creation_transition=creation_transition,
        creation_transition_duration=creation_transition_duration,
    )

    store = get_task_store()
    created_tasks = []

    for url in deduped_urls:
        resolved_source_title, resolved_project_name = await _resolve_titles_for_task(
            source_url=url,
            source_title="",
            source_lang=source_lang,
            target_lang=target_lang,
        )
        task = store.create(
            source_url=url,
            source_title=resolved_source_title,
            translated_title=resolved_project_name,
            source_lang=source_lang,
            target_lang=target_lang,
            task_scope=scope,
            enable_tts=options["enable_tts"],
            enable_short_clips=options["enable_short_clips"],
            enable_article=options["enable_article"],
            embed_subtitle_type=options["embed_subtitle_type"],
            subtitle_style=subtitle_style,
            creation_config=normalize_creation_config(creation_config, enable_short_clips=options["enable_short_clips"]),
            priority=2,
        )
        created_tasks.append(
            {
                "task_id": task.task_id,
                "source_url": task.source_url,
                "state": task.state,
            }
        )

    logger.info(f"📝 Web批量创建任务: {len(created_tasks)} 个")
    should_auto_run = _is_checked(auto_run)
    if should_auto_run and background_tasks is not None:
        for created in created_tasks:
            background_tasks.add_task(_run_production, created["task_id"])
        logger.info(f"🚀 Web批量自动启动生产管线: {len(created_tasks)} 个")

    payload = {
        "count": len(created_tasks),
        "tasks": created_tasks,
        "message": (
            f"批量任务创建成功并已启动生产管线: {len(created_tasks)} 个"
            if should_auto_run
            else f"批量任务创建成功: {len(created_tasks)} 个"
        ),
        "auto_run": should_auto_run,
    }
    if request.headers.get("HX-Request", "").lower() == "true":
        return JSONResponse(payload, headers={"HX-Redirect": "/tasks"})
    if _prefers_html_navigation(request):
        return RedirectResponse(url="/tasks", status_code=303)
    return payload


@router.get("/", response_model=List[TaskResponse])
async def list_tasks(state: Optional[str] = None, limit: int = 50):
    """列出任务"""
    store = get_task_store()

    if state:
        try:
            task_state = TaskState(state)
            tasks = store.list_by_state(task_state)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效状态: {state}")
    else:
        tasks = store.list_all()

    tasks = tasks[:limit]

    return [
        TaskResponse(
            task_id=t.task_id,
            project_name=t.project_name,
            source_url=t.source_url,
            source_title=t.source_title,
            state=t.state,
            progress=t.progress,
            error_message=t.error_message,
            last_error_code=t.last_error_code,
            last_step=t.last_step,
            task_scope=getattr(t, "task_scope", "full"),
            subtitle_style=getattr(t, "subtitle_style", {}) or {},
            creation_config=getattr(t, "creation_config", {}) or {},
            creation_state=getattr(t, "creation_state", {}) or {},
            creation_status=getattr(t, "creation_status", {}) or {},
            created_at=t.created_at,
            updated_at=t.updated_at,
            products_count=len(t.products),
        )
        for t in tasks
    ]


@router.get("/stats", response_model=TaskStatsResponse)
async def get_stats():
    """获取任务统计"""
    store = get_task_store()
    stats = store.get_stats()
    active = store.list_active()

    return TaskStatsResponse(
        stats=stats,
        active_count=len(active),
        total_count=stats.get("total", 0),
    )


@router.get("/completed", response_model=List[TaskResponse])
async def list_completed_tasks():
    """列出已完成的任务"""
    store = get_task_store()
    tasks = store.list_by_state(TaskState.COMPLETED)
    tasks = sorted(tasks, key=lambda t: t.created_at, reverse=True)

    return [
        TaskResponse(
            task_id=t.task_id,
            project_name=t.project_name,
            source_url=t.source_url,
            source_title=t.source_title,
            state=t.state,
            progress=t.progress,
            error_message=t.error_message,
            last_error_code=t.last_error_code,
            last_step=t.last_step,
            task_scope=getattr(t, "task_scope", "full"),
            subtitle_style=getattr(t, "subtitle_style", {}) or {},
            creation_config=getattr(t, "creation_config", {}) or {},
            creation_state=getattr(t, "creation_state", {}) or {},
            creation_status=getattr(t, "creation_status", {}) or {},
            created_at=t.created_at,
            updated_at=t.updated_at,
            products_count=len(t.products),
        )
        for t in tasks
    ]


@router.post("/subtitle-style/preview")
async def create_subtitle_style_preview(request: SubtitlePreviewRequest):
    """
    生成字幕样式预览（真实视频帧）。
    """
    preview_id = f"pv_{uuid.uuid4().hex[:12]}"
    preview_dir = _preview_root() / preview_id
    preview_dir.mkdir(parents=True, exist_ok=True)

    source_clip = preview_dir / "source_preview.mp4"
    ok, err = await _download_preview_source(request.source_url, source_clip)
    if not ok:
        raise HTTPException(status_code=400, detail=err)

    style = _resolve_subtitle_style(request.subtitle_style)
    sample_srt = preview_dir / "sample_bilingual.srt"
    sample_srt.write_text(
        (
            "1\n"
            "00:00:00,000 --> 00:00:04,000\n"
            "这是字幕样式预览（中文）\n"
            "This is subtitle style preview (English)\n\n"
            "2\n"
            "00:00:04,000 --> 00:00:07,900\n"
            "你可以调整中英文字号和位置\n"
            "You can tune size and position independently\n"
        ),
        encoding="utf-8",
    )

    preview_video = _preview_entry_path(preview_id)
    processor = LongVideoProcessor()
    burned, render_debug = await processor.burn_subtitles_with_debug(
        video_path=str(source_clip),
        subtitle_path=str(sample_srt),
        output_path=str(preview_video),
        subtitle_style=style,
        allow_soft_fallback=False,
        probe_font_candidates=True,
        visibility_check=True,
    )
    if not burned or not preview_video.exists():
        detail = (render_debug or {}).get("error") or "字幕样式预览生成失败"
        raise HTTPException(status_code=500, detail=detail)

    expires_at = int(time.time()) + 24 * 3600
    return {
        "preview_id": preview_id,
        "preview_url": f"/api/tasks/subtitle-style/preview/{preview_id}",
        "expires_at": expires_at,
        "subtitle_style": style,
        "render_debug": render_debug,
    }


@router.get("/subtitle-style/preview/{preview_id}")
async def get_subtitle_style_preview(preview_id: str):
    """
    获取字幕样式预览视频。
    """
    preview_path = _preview_entry_path(preview_id)
    if not preview_path.exists() or not preview_path.is_file():
        raise HTTPException(status_code=404, detail="预览不存在或已过期")

    # 超过24小时视为过期
    age = time.time() - preview_path.stat().st_mtime
    if age > 24 * 3600:
        raise HTTPException(status_code=404, detail="预览已过期")

    return FileResponse(
        path=str(preview_path),
        filename=f"{preview_id}.mp4",
        media_type="video/mp4",
    )


def _product_public_view(product: Dict[str, Any]) -> Dict[str, Any]:
    metadata = product.get("metadata") if isinstance(product.get("metadata"), dict) else {}
    return {
        "type": product.get("type", ""),
        "platform": product.get("platform", ""),
        "title": product.get("title", ""),
        "description": product.get("description", ""),
        "local_path": product.get("local_path", ""),
        "r2_path": product.get("r2_path", ""),
        "metadata": metadata,
    }


def _build_creation_summary(task: Task) -> Dict[str, Any]:
    config = normalize_creation_config(
        getattr(task, "creation_config", {}) or {},
        enable_short_clips=getattr(task, "enable_short_clips", None),
    )
    creation_state = getattr(task, "creation_state", {}) or {}
    creation_status = getattr(task, "creation_status", {}) or {}
    segments = list(creation_state.get("selected_segments", []) or [])

    variants_by_segment: Dict[str, List[Dict[str, Any]]] = {}
    covers: List[Dict[str, Any]] = []
    for product in getattr(task, "products", []) or []:
        if not isinstance(product, dict):
            continue
        product_type = str(product.get("type", "")).strip().lower()
        if product_type == "short_clip":
            metadata = product.get("metadata") if isinstance(product.get("metadata"), dict) else {}
            segment_id = str(metadata.get("segment_id") or product.get("title") or "unknown")
            variants_by_segment.setdefault(segment_id, []).append(_product_public_view(product))
        elif product_type == "cover":
            covers.append(_product_public_view(product))

    segment_lookup = {str(item.get("segment_id", "")): item for item in segments if isinstance(item, dict)}
    grouped_segments: List[Dict[str, Any]] = []
    seen = set()
    for segment_id, variants in variants_by_segment.items():
        grouped_segments.append({
            "segment_id": segment_id,
            "segment": segment_lookup.get(segment_id, {"segment_id": segment_id}),
            "variants": variants,
        })
        seen.add(segment_id)
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        segment_id = str(segment.get("segment_id", ""))
        if segment_id and segment_id not in seen:
            grouped_segments.append({
                "segment_id": segment_id,
                "segment": segment,
                "variants": [],
            })

    return {
        "task_id": task.task_id,
        "config": config,
        "status": {
            "state": task.state,
            "progress": task.progress,
            **creation_status,
            **{k: v for k, v in creation_state.items() if k in {"stage", "status", "segments_total", "segments_completed", "variants_total", "variants_completed", "warnings", "used_fallback"}},
        },
        "segments": segments,
        "variants_by_segment": grouped_segments,
        "covers": covers,
        "actions": {
            "can_review": bool(creation_status.get("review_required")),
            "can_approve": creation_status.get("review_status") == "pending",
            "can_reject": creation_status.get("review_status") == "pending",
        },
    }


@router.get("/{task_id}/creation-summary")
async def get_task_creation_summary(task_id: str):
    store = get_task_store()
    task = store.get(task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    return _build_creation_summary(task)


@router.get("/{task_id}", response_model=TaskDetailResponse)
async def get_task(task_id: str):
    """获取任务详情"""
    store = get_task_store()
    task = store.get(task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    from core.database import Database

    db = Database()
    publish_accounts = getattr(task, "publish_accounts", {}) or {}
    publish_account_details = {}
    for platform, account_id in publish_accounts.items():
        account = db.get_account(account_id)
        if account:
            publish_account_details[platform] = {
                "id": account["id"],
                "name": account["name"],
                "status": account["status"],
                "is_default": account.get("is_default", False),
                "last_error": account.get("last_error", ""),
                "capabilities": account.get("capabilities", {}),
            }

    return TaskDetailResponse(
        task_id=task.task_id,
        project_name=task.project_name,
        source_url=task.source_url,
        source_title=task.source_title,
        source_lang=task.source_lang,
        target_lang=task.target_lang,
        state=task.state,
        progress=task.progress,
        error_message=task.error_message,
        last_error_code=task.last_error_code,
        last_step=task.last_step,
        task_scope=getattr(task, "task_scope", "full"),
        subtitle_style=getattr(task, "subtitle_style", {}) or {},
        creation_config=getattr(task, "creation_config", {}) or {},
        creation_state=getattr(task, "creation_state", {}) or {},
        creation_status=getattr(task, "creation_status", {}) or {},
        retry_count=task.retry_count,
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed_at=task.completed_at,
        translated_title=task.translated_title,
        translated_description=task.translated_description,
        qc_score=task.qc_score,
        qc_details=task.qc_details,
        global_review_report=getattr(task, "global_review_report", {}) or {},
        products=task.products,
        timeline=task.timeline,
        translation_task_id=task.translation_task_id,
        translation_progress=task.translation_progress,
        duration_seconds=task.duration_seconds,
        publish_accounts=publish_accounts,
        publish_account_details=publish_account_details,
    )


@router.get("/{task_id}/artifacts")
async def list_task_artifacts(task_id: str):
    """列出任务可下载产物。"""
    store = get_task_store()
    task = store.get(task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    artifacts = _collect_task_artifacts(task)
    return {
        "task_id": task_id,
        "count": len(artifacts),
        "artifacts": artifacts,
    }


@router.get("/{task_id}/artifacts/{artifact_id}/download")
async def download_task_artifact(task_id: str, artifact_id: str, inline: bool = False):
    """下载指定任务产物文件。"""
    store = get_task_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    artifacts = _collect_task_artifacts(task)
    target = next((a for a in artifacts if a["artifact_id"] == artifact_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="产物不存在")

    file_path = Path(target["local_path"])
    if not file_path.exists() or not file_path.is_file():
        r2_path = target.get("r2_path", "")
        if not r2_path:
            raise HTTPException(status_code=404, detail="文件不存在，可能已被清理")

        # 本地文件缺失时，回退从 R2 拉取后再下载
        config = Config()
        cache_dir = Path.home() / ".video-factory" / "download-cache" / task_id
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{artifact_id}_{target.get('download_filename', target.get('filename', file_path.name))}"

        storage = StorageManager(
            bucket=config.get("storage", "r2", "bucket", default="videoflow"),
            rclone_remote=config.get("storage", "r2", "rclone_remote", default="r2"),
        )
        ok = storage.download_from_r2(r2_path, str(cache_file))
        if not ok or not cache_file.exists():
            raise HTTPException(status_code=404, detail="本地文件缺失且R2回源失败")
        file_path = cache_file

    download_name = target.get("download_filename", target.get("filename", file_path.name))
    media_type, _ = mimetypes.guess_type(download_name)

    return FileResponse(
        path=str(file_path),
        filename=download_name,
        media_type=media_type or "application/octet-stream",
        content_disposition_type="inline" if inline else "attachment",
    )


@router.delete("/{task_id}")
async def delete_task(task_id: str):
    """删除任务"""
    store = get_task_store()
    task = store.get(task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    store.delete(task_id)
    return {"message": f"任务 {task_id} 已删除"}


@router.post("/{task_id}/retry")
async def retry_task(task_id: str):
    """重试失败的任务"""
    store = get_task_store()
    task = store.get(task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    if task.state != TaskState.FAILED.value:
        raise HTTPException(status_code=400, detail=f"只能重试失败的任务，当前状态: {task.state}")

    task.transition(TaskState.QUEUED)
    task.retry_count += 1
    task.error_message = ""
    task.last_error_code = ""
    task.mark_step(TaskState.QUEUED.value)
    task.progress = 0
    store.update(task)

    return {"message": f"任务 {task_id} 已重新排队", "state": task.state}


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str):
    """取消任务（前端兼容接口）"""
    store = get_task_store()
    task = store.get(task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    if task.state in [TaskState.COMPLETED.value, TaskState.FAILED.value, TaskState.PARTIAL_SUCCESS.value]:
        raise HTTPException(status_code=400, detail=f"任务不可取消，当前状态: {task.state}")

    cancel_reason = "任务已被用户取消"
    forced_transition = False

    # 大部分状态机支持直接转FAILED；少数状态不支持时强制终止。
    transitioned = task.transition(TaskState.FAILED)
    if not transitioned:
        forced_transition = True
        task.state = TaskState.FAILED.value
        task.updated_at = time.time()

    task.error_message = cancel_reason
    task.last_error_code = "TASK_CANCELLED"
    task.mark_step(TaskState.FAILED.value)
    store.update(task)

    return {
        "message": f"任务 {task_id} 已取消",
        "state": task.state,
        "forced_transition": forced_transition,
    }
