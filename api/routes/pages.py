"""
Web pages and HTMX partials routes
"""
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from api.auth import auth_enabled, registration_allowed, require_auth_page

from core.project_naming import build_project_name
from core.task import TaskStore, TaskState
from core.config import Config
from api.routes.tasks import (
    _make_artifact_id,
    list_task_artifacts as api_list_task_artifacts,
    download_task_artifact as api_download_task_artifact,
)

router = APIRouter()

# Setup Jinja2 templates
BASE_DIR = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(BASE_DIR / "web" / "templates"))


def render_template(request: Request, template_name: str, **context):
    """统一新版本 TemplateResponse 调用顺序。"""
    return templates.TemplateResponse(request, template_name, context)


def _service_check_urls(request: Request) -> List[tuple[str, str]]:
    """
    返回服务健康检查地址（动态端口，避免硬编码导致误判离线）。
    """
    cfg = Config()
    api_base = str(request.base_url).rstrip("/")
    whisper_base = cfg.get("services", "whisper_proxy_url", default="http://127.0.0.1:8866").rstrip("/")
    tts_base = cfg.get("services", "tts_proxy_url", default="http://127.0.0.1:8877").rstrip("/")

    return [
        ("API", f"{api_base}/api/health"),
        ("Whisper", f"{whisper_base}/health"),
        ("TTS", f"{tts_base}/health"),
    ]

# Helper functions
def format_bytes(bytes_value: int) -> str:
    """Convert bytes to human readable format"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.1f} {unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.1f} PB"


def format_duration(seconds: int) -> str:
    """Convert seconds to human readable duration"""
    if seconds < 60:
        return f"{seconds}秒"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}分钟"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}小时{minutes}分钟"


def parse_timestamp(value) -> Optional[float]:
    """兼容 float 时间戳、数字字符串和 ISO 时间。"""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value) if float(value) > 0 else None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = float(raw)
            return parsed if parsed > 0 else None
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def format_timestamp(value, fmt: str = "%Y-%m-%d %H:%M", fallback: str = "未知") -> str:
    ts = parse_timestamp(value)
    if ts is None:
        return fallback
    return datetime.fromtimestamp(ts).strftime(fmt)


def duration_between(start_value, end_value) -> Optional[int]:
    start_ts = parse_timestamp(start_value)
    end_ts = parse_timestamp(end_value)
    if start_ts is None or end_ts is None:
        return None
    return max(0, int(end_ts - start_ts))


def get_task_products(task: Dict) -> List[Dict]:
    products = task.get("products")
    if isinstance(products, list):
        return products
    legacy_products = task.get("factory_products")
    if isinstance(legacy_products, list):
        return legacy_products
    return []


def get_task_platforms(task: Dict) -> List[str]:
    platforms: List[str] = []

    def _append(platform: Optional[str]):
        if platform and platform != "all" and platform not in platforms:
            platforms.append(platform)

    publish_accounts = task.get("publish_accounts") or {}
    for platform in publish_accounts.keys():
        _append(platform)

    for platform in task.get("target_platforms", []) or []:
        _append(platform)

    for product in get_task_products(task):
        _append(product.get("platform"))

    return platforms


def get_task_title(task: Dict) -> str:
    return build_project_name(
        translated_title=task.get("project_name", "") or task.get("translated_title", ""),
        source_title=task.get("source_title", "") or task.get("original_title", ""),
        source_url=task.get("source_url", ""),
        task_id=task.get("task_id", ""),
    )


def get_task_initial(task: Dict) -> str:
    title = get_task_title(task).strip()
    return title[:1].upper() if title else "V"


def _normalize_summary_text(text: Optional[str], limit: int = 200) -> str:
    normalized = " ".join(str(text or "").split())
    return normalized[:limit]


def get_task_summary(task: Dict[str, Any]) -> str:
    for product in get_task_products(task):
        if product.get("type") != "long_video":
            continue
        summary = _normalize_summary_text(product.get("description"))
        if summary:
            return summary
    return _normalize_summary_text(task.get("translated_description"))


def _build_task_artifact_url(task_id: str, path: str, *, inline: bool = False) -> str:
    artifact_id = _make_artifact_id(path)
    suffix = "?inline=1" if inline else ""
    return f"/api/tasks/{task_id}/artifacts/{artifact_id}/download{suffix}"


def get_task_cover_preview_url(task: Dict[str, Any]) -> str:
    cover_products = [
        product
        for product in get_task_products(task)
        if isinstance(product, dict) and product.get("type") == "cover"
    ]
    if not cover_products:
        return ""

    def _sort_key(product: Dict[str, Any]) -> tuple[int, int]:
        metadata = product.get("metadata") or {}
        cover_type = str(metadata.get("cover_type", "")).lower()
        order = {"horizontal": 0, "vertical": 1}
        return order.get(cover_type, 99), 0 if product.get("local_path") else 1

    primary_cover = sorted(cover_products, key=_sort_key)[0]
    cover_path = primary_cover.get("local_path") or ""
    if not cover_path and primary_cover.get("r2_path"):
        cover_path = f"/__r2__/{primary_cover['r2_path']}"
    if not cover_path:
        return ""
    return _build_task_artifact_url(task.get("task_id", ""), cover_path, inline=True)


def enrich_creation_badges(task: Dict[str, Any]) -> None:
    products = get_task_products(task)
    creation_status = task.get("creation_status") or {}
    clip_products = [p for p in products if isinstance(p, dict) and p.get("type") == "short_clip"]
    cover_products = [p for p in products if isinstance(p, dict) and p.get("type") == "cover"]

    task["has_long_video"] = any(isinstance(p, dict) and p.get("type") == "long_video" for p in products)
    task["has_clips"] = bool(clip_products)
    task["clips_count"] = len(clip_products)
    task["has_article"] = any(isinstance(p, dict) and p.get("type") == "article" for p in products)
    task["has_cover"] = bool(cover_products)
    task["creation_review_pending"] = bool(creation_status.get("review_required")) and creation_status.get("review_status") == "pending"
    task["creation_review_status"] = creation_status.get("review_status", "")


def get_status_display(state: str) -> Dict[str, str]:
    """Get display info for task status (grayscale monochrome style)"""
    status_map = {
        TaskState.QUEUED: {"text": "排队中", "color": "text-fg-sub", "bg": "bg-muted"},
        TaskState.DOWNLOADING: {"text": "下载中", "color": "text-fg", "bg": "bg-muted"},
        TaskState.DOWNLOADED: {"text": "已下载", "color": "text-fg", "bg": "bg-muted"},
        TaskState.UPLOADING_SOURCE: {"text": "上传源文件", "color": "text-fg", "bg": "bg-muted"},
        TaskState.TRANSLATING: {"text": "翻译中", "color": "text-fg", "bg": "bg-muted"},
        TaskState.QC_CHECKING: {"text": "质检中", "color": "text-fg", "bg": "bg-muted"},
        TaskState.QC_PASSED: {"text": "质检通过", "color": "text-green-600", "bg": "bg-green-50"},
        TaskState.PROCESSING: {"text": "加工中", "color": "text-fg", "bg": "bg-muted"},
        TaskState.UPLOADING_PRODUCTS: {"text": "上传产品", "color": "text-fg", "bg": "bg-muted"},
        TaskState.READY_TO_PUBLISH: {"text": "待发布", "color": "text-fg-strong", "bg": "bg-muted"},
        TaskState.PUBLISHING: {"text": "发布中", "color": "text-fg", "bg": "bg-muted"},
        TaskState.PARTIAL_SUCCESS: {"text": "部分成功", "color": "text-amber-700", "bg": "bg-amber-50"},
        TaskState.COMPLETED: {"text": "已完成", "color": "text-green-600", "bg": "bg-green-50"},
        TaskState.FAILED: {"text": "失败", "color": "text-red-600", "bg": "bg-red-50"},
    }
    return status_map.get(state, status_map[TaskState.QUEUED])


def calculate_task_progress(task: Dict) -> int:
    """Calculate task progress percentage based on state and scope"""
    scope = task.get("task_scope", "full")

    # 不同 scope 的进度映射（终点不同，越短的 scope 前期步骤占比越大）
    scope_progress = {
        "subtitle_only": {
            TaskState.QUEUED: 0, TaskState.DOWNLOADING: 10, TaskState.DOWNLOADED: 20,
            TaskState.UPLOADING_SOURCE: 25, TaskState.TRANSLATING: 50,
            TaskState.QC_CHECKING: 65, TaskState.QC_PASSED: 75,
            TaskState.PROCESSING: 85, TaskState.UPLOADING_PRODUCTS: 92,
            TaskState.READY_TO_PUBLISH: 96,
            TaskState.PARTIAL_SUCCESS: 100, TaskState.COMPLETED: 100, TaskState.FAILED: 0,
        },
        "subtitle_dub": {
            TaskState.QUEUED: 0, TaskState.DOWNLOADING: 8, TaskState.DOWNLOADED: 15,
            TaskState.UPLOADING_SOURCE: 18, TaskState.TRANSLATING: 45,
            TaskState.QC_CHECKING: 80, TaskState.QC_PASSED: 90,
            TaskState.PARTIAL_SUCCESS: 100, TaskState.COMPLETED: 100, TaskState.FAILED: 0,
        },
        "dub_and_copy": {
            TaskState.QUEUED: 0, TaskState.DOWNLOADING: 5, TaskState.DOWNLOADED: 10,
            TaskState.UPLOADING_SOURCE: 12, TaskState.TRANSLATING: 35,
            TaskState.QC_CHECKING: 55, TaskState.QC_PASSED: 60,
            TaskState.PROCESSING: 75, TaskState.UPLOADING_PRODUCTS: 85,
            TaskState.READY_TO_PUBLISH: 95,
            TaskState.PARTIAL_SUCCESS: 100, TaskState.COMPLETED: 100, TaskState.FAILED: 0,
        },
        "full": {
            TaskState.QUEUED: 0, TaskState.DOWNLOADING: 5, TaskState.DOWNLOADED: 10,
            TaskState.UPLOADING_SOURCE: 12, TaskState.TRANSLATING: 35,
            TaskState.QC_CHECKING: 55, TaskState.QC_PASSED: 60,
            TaskState.PROCESSING: 70, TaskState.UPLOADING_PRODUCTS: 80,
            TaskState.READY_TO_PUBLISH: 85, TaskState.PUBLISHING: 95,
            TaskState.PARTIAL_SUCCESS: 100, TaskState.COMPLETED: 100, TaskState.FAILED: 0,
        },
    }

    progress_map = scope_progress.get(scope, scope_progress["full"])
    return progress_map.get(task.get("state"), 0)


_UI_ACTIVE_STATES = {
    TaskState.QUEUED,
    TaskState.DOWNLOADING,
    TaskState.DOWNLOADED,
    TaskState.UPLOADING_SOURCE,
    TaskState.TRANSLATING,
    TaskState.QC_CHECKING,
    TaskState.QC_PASSED,
    TaskState.PROCESSING,
    TaskState.UPLOADING_PRODUCTS,
    TaskState.PUBLISHING,
}


def is_task_active_for_ui(task: Dict[str, Any]) -> bool:
    """统一页面层“活跃任务”口径。"""
    return task.get("state") in _UI_ACTIVE_STATES


def get_task_progress(task: Dict[str, Any]) -> int:
    """优先使用任务持久化进度，缺失时再按状态兜底推导。"""
    raw_progress = task.get("progress")
    if isinstance(raw_progress, (int, float)):
        normalized = max(0, min(100, int(raw_progress)))
        if normalized > 0 or task.get("state") in [TaskState.QUEUED, TaskState.FAILED]:
            return normalized
    return calculate_task_progress(task)


# scope → 显示标签（full 不显示标签）
_SCOPE_DISPLAY_LABELS = {
    "subtitle_only": "仅字幕",
    "subtitle_dub": "字幕+配音",
    "dub_and_copy": "配音+文案",
}


def get_scope_label(task: Dict) -> str:
    """返回 scope 显示标签，full 返回空字符串"""
    return _SCOPE_DISPLAY_LABELS.get(task.get("task_scope", "full"), "")


# ============================================================================
# Page Routes
# ============================================================================

@router.get("/", response_class=HTMLResponse, dependencies=[Depends(require_auth_page)])
async def dashboard(request: Request):
    """Dashboard home page"""
    stats_context = _build_stats_cards_context()
    active_context = _build_active_tasks_context()
    service_context = await _build_service_status_detail_context(request)
    storage_context = _build_storage_overview_context()
    recent_context = _build_recent_completed_context()
    return render_template(
        request,
        "dashboard.html",
        stats=stats_context["stats"],
        active_tasks=active_context["tasks"],
        total_active=active_context["total_active"],
        services=service_context["services"],
        last_check_time=service_context["last_check_time"],
        r2=storage_context["r2"],
        local=storage_context["local"],
        recent_tasks=recent_context["tasks"],
    )


@router.get("/tasks/new", response_class=HTMLResponse, dependencies=[Depends(require_auth_page)])
async def new_task_page(request: Request):
    """New task creation page"""
    return render_template(request, "new_task.html")


@router.get("/tasks", response_class=HTMLResponse, dependencies=[Depends(require_auth_page)])
async def tasks_list_page(request: Request):
    """Task list page"""
    status = str(request.query_params.get("status", "all") or "all")
    platform = str(request.query_params.get("platform", "all") or "all")
    context = _build_task_list_context(status=status, platform=platform)
    return render_template(request, "tasks.html", **context)


@router.get("/tasks/{task_id}", response_class=HTMLResponse, dependencies=[Depends(require_auth_page)])
async def task_detail_page(request: Request, task_id: str):
    """Task detail page"""
    return render_template(request, "task_detail.html", task_id=task_id)


@router.get("/tasks/{task_id}/artifacts", dependencies=[Depends(require_auth_page)])
async def task_artifacts_alias(request: Request, task_id: str):
    """任务产物列表别名路由（兼容非 /api 前缀网关）。"""
    return await api_list_task_artifacts(task_id)


@router.get("/tasks/{task_id}/artifacts/{artifact_id}/download", dependencies=[Depends(require_auth_page)])
async def task_artifact_download_alias(request: Request, task_id: str, artifact_id: str, inline: bool = False):
    """任务产物下载别名路由（兼容非 /api 前缀网关）。"""
    return await api_download_task_artifact(task_id, artifact_id, inline=inline)


@router.get("/publish", response_class=HTMLResponse, dependencies=[Depends(require_auth_page)])
async def publish_page(request: Request):
    """Publish management page"""
    return render_template(request, "publish.html")


@router.get("/platform-accounts", response_class=HTMLResponse, dependencies=[Depends(require_auth_page)])
async def platform_accounts_page(request: Request):
    """Platform accounts management page (OAuth-bound accounts)"""
    return render_template(request, "platform_accounts.html")


@router.get("/publish-v2", response_class=HTMLResponse, dependencies=[Depends(require_auth_page)])
async def publish_v2_page(request: Request):
    """Multi-platform publishing management page"""
    return render_template(request, "publish_v2.html")


@router.get("/analytics", response_class=HTMLResponse, dependencies=[Depends(require_auth_page)])
async def analytics_page(request: Request):
    """Data analytics dashboard page"""
    return render_template(request, "analytics.html")


@router.get("/storage", response_class=HTMLResponse, dependencies=[Depends(require_auth_page)])
async def storage_page(request: Request):
    """Storage management page"""
    return render_template(request, "storage.html")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Auth page (login tab) — combined login/register with client-side tab switching."""
    if not auth_enabled():
        # No users yet — redirect to /register so JS picks up register tab
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/register", status_code=302)
    return render_template(request, "auth.html")


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    """Auth page (register tab) — combined login/register with client-side tab switching."""
    if not registration_allowed():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/login", status_code=302)
    return render_template(request, "auth.html")


@router.get("/settings", response_class=HTMLResponse, dependencies=[Depends(require_auth_page)])
async def settings_page(request: Request):
    """System settings page"""
    return render_template(request, "settings.html")


# ============================================================================
# HTMX Partial Routes
# ============================================================================

def _build_stats_cards_context() -> Dict[str, Any]:
    task_store = TaskStore()
    all_tasks = [t.to_dict() for t in task_store.list_all()]

    total_tasks = len(all_tasks)
    active_tasks = len([t for t in all_tasks if is_task_active_for_ui(t)])
    completed_tasks = len([t for t in all_tasks if t["state"] == TaskState.COMPLETED])
    pending_publish = len([t for t in all_tasks if t["state"] in [
        TaskState.READY_TO_PUBLISH, TaskState.QC_PASSED
    ]])

    success_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0

    return {
        "stats": {
            "total_tasks": total_tasks,
            "active_tasks": active_tasks,
            "completed_tasks": completed_tasks,
            "pending_publish": pending_publish,
            "success_rate": success_rate,
        }
    }


@router.get("/web/partials/stats_cards", response_class=HTMLResponse)
async def stats_cards_partial(request: Request):
    """Stats cards HTMX partial"""
    return render_template(request, "partials/stats_cards.html", **_build_stats_cards_context())


def _build_active_tasks_context() -> Dict[str, Any]:
    task_store = TaskStore()
    all_tasks = [t.to_dict() for t in task_store.list_all()]

    active_tasks = [t for t in all_tasks if is_task_active_for_ui(t)]
    active_tasks.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    display_tasks = active_tasks[:5]

    for task in display_tasks:
        status = get_status_display(task["state"])
        task["status_text"] = status["text"]
        task["status_color"] = status["color"]
        task["status_bg"] = status["bg"]
        task["progress"] = get_task_progress(task)
        task["current_step"] = status["text"]
        task["can_pause"] = task["state"] in [TaskState.TRANSLATING, TaskState.PROCESSING]
        task["can_cancel"] = task["state"] not in [TaskState.COMPLETED, TaskState.FAILED, TaskState.PARTIAL_SUCCESS]
        task["scope_label"] = get_scope_label(task)
        task["project_name"] = get_task_title(task)

    return {
        "tasks": display_tasks,
        "total_active": len(active_tasks),
    }


@router.get("/web/partials/active_tasks", response_class=HTMLResponse)
async def active_tasks_partial(request: Request):
    """Active tasks HTMX partial"""
    return render_template(
        request,
        "partials/active_tasks.html",
        **_build_active_tasks_context(),
    )


@router.get("/web/partials/service_status", response_class=HTMLResponse)
async def service_status_partial(request: Request):
    """Service status indicator HTMX partial (legacy, redirects to sidebar)"""
    return HTMLResponse('<span class="text-[12px] text-fg-sub">服务运行中</span>')


@router.get("/web/partials/service_status_sidebar", response_class=HTMLResponse)
async def service_status_sidebar_partial(request: Request):
    """Compact service status for sidebar bottom HTMX partial"""
    import httpx

    checks = _service_check_urls(request)

    # 当前接口已可响应，API服务视为健康，避免 localhost 解析差异误判
    results = [("API", True)]
    for name, url in checks:
        if name == "API":
            continue
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(url)
                healthy = resp.status_code < 500
        except Exception:
            healthy = False
        results.append((name, healthy))

    total = len(results)
    healthy_count = sum(1 for _, h in results if h)
    all_healthy = healthy_count == total

    # Build compact HTML for sidebar
    dot_color = "bg-green-500" if all_healthy else ("bg-yellow-500" if healthy_count > 0 else "bg-red-400")
    status_text = f"{healthy_count}/{total} 服务正常" if not all_healthy else "所有服务正常"

    html_parts = [
        f'<div class="flex items-center gap-2 px-3 py-1.5 text-[12px] text-fg-sub">',
        f'  <span class="w-1.5 h-1.5 rounded-full {dot_color} flex-shrink-0"></span>',
        f'  <span>{status_text}</span>',
        f'</div>',
    ]

    # Individual service dots row
    html_parts.append('<div class="flex items-center gap-3 px-3 py-1">')
    for name, healthy in results:
        c = "bg-green-500" if healthy else "bg-red-400"
        html_parts.append(
            f'<span class="flex items-center gap-1 text-[11px] text-fg-faint">'
            f'<span class="w-1 h-1 rounded-full {c}"></span>{name}</span>'
        )
    html_parts.append('</div>')

    return HTMLResponse("\n".join(html_parts))


async def _build_service_status_detail_context(request: Request) -> Dict[str, Any]:
    import httpx
    from datetime import datetime

    services = []
    checks = _service_check_urls(request)
    name_map = {
        "API": "video-factory API",
        "Whisper": "Groq Whisper 代理",
        "TTS": "Edge-TTS 代理",
    }
    details_map = {name: url for name, url in checks}

    api_health_url = f"{str(request.base_url).rstrip('/')}/api/health"
    services.append(
        {
            "name": "video-factory API",
            "healthy": True,
            "details": api_health_url,
        }
    )

    for short_name, url in checks:
        if short_name == "API":
            continue
        healthy = False
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(url)
                healthy = response.status_code < 500
        except Exception:
            healthy = False

        services.append(
            {
                "name": name_map.get(short_name, short_name),
                "healthy": healthy,
                "details": details_map.get(short_name, url),
            }
        )

    return {
        "services": services,
        "last_check_time": datetime.now().strftime("%H:%M:%S"),
    }


@router.get("/web/partials/service_status_detail", response_class=HTMLResponse)
async def service_status_detail_partial(request: Request):
    """Service status detail HTMX partial"""
    return render_template(
        request,
        "partials/service_status_detail.html",
        **(await _build_service_status_detail_context(request)),
    )


def _build_storage_overview_context() -> Dict[str, Any]:
    import subprocess
    import shutil

    r2_used = 0
    r2_files = 0
    r2_total = 10 * 1024 * 1024 * 1024

    try:
        result = subprocess.run(
            ["rclone", "size", "r2:videoflow", "--json"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            r2_used = data.get("bytes", 0)
            r2_files = data.get("count", 0)
    except Exception:
        pass

    local_path = Path.home() / ".video-factory" / "storage"
    local_used = 0
    local_files = 0
    local_total = 200 * 1024 * 1024 * 1024
    try:
        usage = shutil.disk_usage(str(local_path.parent))
        local_total = usage.total
        local_used = usage.used
        if local_path.exists():
            local_files = sum(1 for _ in local_path.rglob("*") if _.is_file())
    except Exception:
        pass

    return {
        "r2": {
            "used_formatted": format_bytes(r2_used),
            "total_formatted": format_bytes(r2_total),
            "usage_percent": min(100, int(r2_used / r2_total * 100)) if r2_total > 0 else 0,
            "file_count": r2_files,
        },
        "local": {
            "used_formatted": format_bytes(local_used),
            "total_formatted": format_bytes(local_total),
            "usage_percent": min(100, int(local_used / local_total * 100)) if local_total > 0 else 0,
            "file_count": local_files,
        },
    }


@router.get("/web/partials/storage_overview", response_class=HTMLResponse)
async def storage_overview_partial(request: Request):
    """Storage overview HTMX partial"""
    return render_template(
        request,
        "partials/storage_overview.html",
        **_build_storage_overview_context(),
    )


@router.get("/web/partials/task_list", response_class=HTMLResponse)
async def task_list_partial(request: Request, status: str = "all", platform: str = "all"):
    """Task list HTMX partial"""
    context = _build_task_list_context(status=status, platform=platform)

    return render_template(
        request,
        "partials/task_list.html",
        **context,
    )


def _build_task_list_context(*, status: str = "all", platform: str = "all") -> Dict[str, Any]:
    resolved_status = status if status in {"all", "active", "completed", "failed"} else "all"
    resolved_platform = platform or "all"
    task_store = TaskStore()
    all_tasks = [t.to_dict() for t in task_store.list_all()]
    now_ts = datetime.now().timestamp()

    # Filter by status
    if resolved_status != "all":
        if resolved_status == "active":
            all_tasks = [t for t in all_tasks if is_task_active_for_ui(t)]
        else:
            all_tasks = [t for t in all_tasks if t["state"] == resolved_status]

    # Filter by platform
    if resolved_platform != "all":
        all_tasks = [t for t in all_tasks if resolved_platform in get_task_platforms(t)]

    # Enrich task data
    for task in all_tasks:
        status_info = get_status_display(task["state"])
        task["status_text"] = status_info["text"]
        task["status_color"] = status_info["color"]
        task["status_bg"] = status_info["bg"]
        task["progress"] = get_task_progress(task)
        task["current_step"] = status_info["text"]
        task["is_active"] = is_task_active_for_ui(task)
        task["can_retry"] = task["state"] == TaskState.FAILED
        task["can_cancel"] = task["state"] not in [TaskState.COMPLETED, TaskState.FAILED, TaskState.PARTIAL_SUCCESS]
        task["project_name"] = get_task_title(task)
        task["initial"] = get_task_initial(task)
        task["title"] = task["project_name"]
        task["platforms"] = get_task_platforms(task)
        task["created_at_display"] = format_timestamp(task.get("created_at"))
        duration = duration_between(task.get("created_at"), now_ts)
        task["elapsed_time"] = format_duration(duration) if duration is not None else "-"
        task["scope_label"] = get_scope_label(task)
        task["summary"] = get_task_summary(task)
        task["cover_preview_url"] = get_task_cover_preview_url(task)
        enrich_creation_badges(task)

    return {
        "tasks": all_tasks,
        "total_pages": 1,
        "current_page": 1,
        "status": resolved_status,
        "platform": resolved_platform,
    }


def _build_recent_completed_context() -> Dict[str, Any]:
    task_store = TaskStore()
    all_tasks = [t.to_dict() for t in task_store.list_all()]

    completed_tasks = [t for t in all_tasks if t["state"] == TaskState.COMPLETED]
    completed_tasks.sort(key=lambda x: parse_timestamp(x.get("updated_at")) or 0, reverse=True)
    recent_tasks = completed_tasks[:10]

    for task in recent_tasks:
        task["completed_at"] = format_timestamp(task.get("updated_at"))
        duration = duration_between(task.get("created_at"), task.get("updated_at"))
        task["duration"] = format_duration(duration) if duration is not None else "未知"
        task["platforms"] = get_task_platforms(task) or ["未设置"]
        task["project_name"] = get_task_title(task)
        task["display_title"] = get_task_title(task)
        enrich_creation_badges(task)
        task["published"] = task.get("state") == TaskState.COMPLETED and task.get("published", False)

    return {"tasks": recent_tasks}


@router.get("/web/partials/recent_completed", response_class=HTMLResponse)
async def recent_completed_partial(request: Request):
    """Recent completed tasks HTMX partial"""
    return render_template(request, "partials/recent_completed.html", **_build_recent_completed_context())


@router.get("/web/partials/publish_stats", response_class=HTMLResponse)
async def publish_stats_partial(request: Request):
    """Publish stats HTMX partial"""
    from api.routes.distribute import get_scheduler

    scheduler = get_scheduler()
    status_count = scheduler.get_queue_status()

    stats = {
        "pending": status_count.get("pending", 0) + status_count.get("manual_pending", 0),
        "publishing": status_count.get("publishing", 0),
        "done": status_count.get("done", 0),
        "failed": status_count.get("failed", 0) + status_count.get("cancelled", 0),
    }

    return render_template(request, "partials/publish_stats.html", stats=stats)


@router.get("/web/partials/publish_queue", response_class=HTMLResponse)
async def publish_queue_partial(request: Request, platform: str = "all"):
    """Publish queue HTMX partial"""
    from api.routes.distribute import get_scheduler
    from core.database import Database

    scheduler = get_scheduler()
    db = Database()
    task_store = TaskStore()
    task_map = {task.task_id: task.to_dict() for task in task_store.list_all()}
    all_jobs = [
        j.to_dict()
        for j in scheduler._queue
        if j.status in ("pending", "publishing", "manual_pending", "failed", "cancelled")
    ]

    # Filter by platform
    if platform != "all":
        all_jobs = [j for j in all_jobs if j.get("platform") == platform]

    # Platform labels
    platform_labels = {
        "bilibili": "B站",
        "douyin": "抖音",
        "xiaohongshu": "小红书",
        "youtube": "YouTube",
        "weixin": "视频号"
    }

    # Status display
    status_map = {
        "pending": {"text": "待发布", "class": "bg-muted text-fg"},
        "publishing": {"text": "发布中", "class": "bg-blue-50 text-blue-600"},
        "manual_pending": {"text": "待人工确认", "class": "bg-amber-50 text-amber-700"},
        "done": {"text": "已完成", "class": "bg-green-50 text-green-600"},
        "failed": {"text": "失败", "class": "bg-red-50 text-red-600"},
        "cancelled": {"text": "已取消", "class": "bg-zinc-100 text-zinc-600"},
    }

    # Enrich job data
    for job in all_jobs:
        job["platform_label"] = platform_labels.get(job.get("platform", ""), job.get("platform", ""))
        status_info = status_map.get(job.get("status", "pending"), status_map["pending"])
        job["status_text"] = status_info["text"]
        job["status_class"] = status_info["class"]
        task_info = task_map.get(job.get("task_id", ""), {})
        project_name = get_task_title(task_info) if task_info else ""
        job["task_title"] = (
            project_name
            or job.get("metadata", {}).get("title")
            or job.get("product", {}).get("title")
            or job.get("task_id", "")[:8]
        )
        account_id = job.get("metadata", {}).get("account_id", "")
        account = db.get_account(account_id) if account_id else None
        job["account_name"] = ""
        job["account_status"] = ""
        job["account_error"] = ""
        if account:
            job["account_name"] = account.get("name", "")
            job["account_status"] = account.get("status", "")
            job["account_error"] = account.get("last_error", "")
        job["error_text"] = job.get("result", {}).get("error", "")
        job["manual_checklist"] = job.get("result", {}).get("manual_checklist", {})
        events = db.get_publish_job_events(job_id=job.get("job_id", ""), limit=1)
        job["latest_event"] = events[0] if events else {}

        # Format scheduled time
        if "scheduled_time" in job:
            try:
                scheduled = datetime.fromtimestamp(job["scheduled_time"])
                job["scheduled_time"] = scheduled.strftime("%m-%d %H:%M")
            except Exception:
                job["scheduled_time"] = "立即"
        else:
            job["scheduled_time"] = "立即"

    return render_template(request, "partials/publish_queue.html", jobs=all_jobs)


@router.get("/web/partials/publish_events", response_class=HTMLResponse)
async def publish_events_partial(request: Request, task_id: str = "", limit: int = 20):
    from core.database import Database

    db = Database()
    events = db.get_publish_job_events(task_id=task_id, limit=max(1, min(limit, 50)))

    for event in events:
        if event.get("created_at"):
            event["created_at_display"] = format_timestamp(
                event["created_at"],
                fmt="%m-%d %H:%M:%S",
                fallback=str(event["created_at"]),
            )
        else:
            event["created_at_display"] = "-"

    return render_template(request, "partials/publish_events.html", events=events, task_id=task_id)
