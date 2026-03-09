"""
任务状态机 - 管理视频处理任务的全生命周期
"""
import json
import os
import time
import uuid
from enum import Enum
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict
from pathlib import Path
import logging

from core.runtime_settings import get_subtitle_style_defaults
from core.subtitle_style import normalize_subtitle_style

logger = logging.getLogger(__name__)


class TaskState(str, Enum):
    """任务状态枚举"""
    QUEUED = "queued"                      # 排队中
    DOWNLOADING = "downloading"            # 下载中（VPS执行）
    DOWNLOADED = "downloaded"              # 下载完成
    UPLOADING_SOURCE = "uploading_source"  # 上传源文件到R2
    TRANSLATING = "translating"            # 翻译配音中（Mac执行）
    QC_CHECKING = "qc_checking"            # 质检中
    QC_PASSED = "qc_passed"               # 质检通过
    QC_FAILED = "qc_failed"               # 质检失败
    PROCESSING = "processing"              # 二次创作加工中
    UPLOADING_PRODUCTS = "uploading_products"  # 上传成品到R2
    READY_TO_PUBLISH = "ready_to_publish"  # 待发布
    PUBLISHING = "publishing"              # 发布中
    PARTIAL_SUCCESS = "partial_success"    # 部分发布成功
    COMPLETED = "completed"                # 全部完成
    FAILED = "failed"                      # 失败

    @classmethod
    def active_states(cls) -> list:
        """返回活跃状态列表"""
        return [
            cls.QUEUED, cls.DOWNLOADING, cls.DOWNLOADED,
            cls.UPLOADING_SOURCE, cls.TRANSLATING,
            cls.QC_CHECKING, cls.PROCESSING,
            cls.UPLOADING_PRODUCTS, cls.PUBLISHING
        ]


# ========== 任务范围 ==========

VALID_SCOPES = ("subtitle_only", "subtitle_dub", "dub_and_copy", "full")

# scope → 各阶段默认开关值
SCOPE_DEFAULTS = {
    "subtitle_only": {
        "enable_tts": False, "embed_subtitle_type": "none",
        "enable_short_clips": False, "enable_article": False,
    },
    "subtitle_dub": {
        "enable_tts": True, "embed_subtitle_type": "horizontal",
        "enable_short_clips": False, "enable_article": False,
    },
    "dub_and_copy": {
        "enable_tts": True, "embed_subtitle_type": "horizontal",
        "enable_short_clips": True, "enable_article": True,
    },
    "full": {
        "enable_tts": True, "embed_subtitle_type": "horizontal",
        "enable_short_clips": True, "enable_article": True,
    },
}

SCOPE_LABELS = {
    "subtitle_only": "仅字幕",
    "subtitle_dub": "字幕+配音",
    "dub_and_copy": "配音+文案",
    "full": "全流程",
}


# 合法的状态转换
VALID_TRANSITIONS = {
    TaskState.QUEUED: [TaskState.DOWNLOADING, TaskState.TRANSLATING, TaskState.FAILED],
    TaskState.DOWNLOADING: [TaskState.DOWNLOADED, TaskState.FAILED],
    TaskState.DOWNLOADED: [TaskState.UPLOADING_SOURCE, TaskState.TRANSLATING, TaskState.FAILED],
    TaskState.UPLOADING_SOURCE: [TaskState.TRANSLATING, TaskState.FAILED],
    TaskState.TRANSLATING: [TaskState.QC_CHECKING, TaskState.FAILED],
    TaskState.QC_CHECKING: [TaskState.QC_PASSED, TaskState.QC_FAILED, TaskState.FAILED],
    TaskState.QC_PASSED: [TaskState.PROCESSING, TaskState.COMPLETED],  # COMPLETED: subtitle_dub 直接完成
    TaskState.QC_FAILED: [TaskState.TRANSLATING, TaskState.FAILED],
    TaskState.PROCESSING: [TaskState.UPLOADING_PRODUCTS, TaskState.FAILED],
    TaskState.UPLOADING_PRODUCTS: [TaskState.READY_TO_PUBLISH, TaskState.FAILED],
    TaskState.READY_TO_PUBLISH: [TaskState.PUBLISHING, TaskState.COMPLETED],  # COMPLETED: dub_and_copy 跳过发布
    TaskState.PUBLISHING: [TaskState.COMPLETED, TaskState.PARTIAL_SUCCESS, TaskState.FAILED],
    TaskState.PARTIAL_SUCCESS: [TaskState.PUBLISHING, TaskState.QUEUED],
    TaskState.COMPLETED: [],
    TaskState.FAILED: [TaskState.QUEUED, TaskState.PUBLISHING],
}


@dataclass
class TaskProduct:
    """任务产出物"""
    type: str              # long_video, short_clip, article, cover
    platform: str          # bilibili, douyin, xiaohongshu, youtube, all
    local_path: str = ""
    r2_path: str = ""
    title: str = ""
    description: str = ""
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Task:
    """视频处理任务"""

    # 基本信息
    task_id: str = ""
    source_url: str = ""
    source_title: str = ""
    source_lang: str = "en"
    target_lang: str = "zh_cn"

    # 状态
    state: str = TaskState.QUEUED.value
    progress: int = 0
    error_message: str = ""
    last_error_code: str = ""
    last_step: str = ""
    state_entered_at: float = 0.0
    step_started_at: float = 0.0
    retry_count: int = 0

    # 时间戳
    created_at: float = 0.0
    updated_at: float = 0.0
    completed_at: float = 0.0

    # 文件路径
    source_r2_path: str = ""         # R2上的源视频路径
    source_local_path: str = ""      # 本地源视频路径
    translated_video_path: str = ""  # 翻译后视频路径
    subtitle_path: str = ""          # 字幕文件路径
    tts_audio_path: str = ""         # 配音音频路径（支持 wav/mp3/ogg/pcm）
    transcript_text: str = ""        # 翻译后的文本

    # KlicStudio相关
    klic_task_id: str = ""           # KlicStudio返回的任务ID
    klic_progress: int = 0

    # 翻译后的信息
    translated_title: str = ""
    translated_description: str = ""

    # 质检
    qc_score: float = 0.0
    qc_details: str = ""

    # 产出物
    products: List[Dict] = field(default_factory=list)
    timeline: List[Dict[str, Any]] = field(default_factory=list)

    # 配置选项
    task_scope: str = "full"  # subtitle_only / subtitle_dub / dub_and_copy / full
    enable_tts: bool = True
    enable_short_clips: bool = True
    enable_article: bool = True
    embed_subtitle_type: str = "horizontal"  # horizontal/vertical/none
    subtitle_style: Dict[str, Any] = field(default_factory=dict)
    priority: int = 2  # 0=紧急 1=高 2=普通 3=低
    publish_accounts: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not self.task_id:
            self.task_id = f"vf_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        if not self.created_at:
            self.created_at = time.time()
        if not self.updated_at:
            self.updated_at = time.time()
        if not self.state_entered_at:
            self.state_entered_at = self.updated_at
        if not self.last_step:
            self.last_step = self.state
        if not self.step_started_at:
            self.step_started_at = self.updated_at
        if not self.timeline:
            self._append_timeline(
                "task_created",
                timestamp=self.created_at,
                to_state=self.state,
                to_step=self.last_step,
                duration_seconds=0.0,
                record_time=False,
            )

        self.subtitle_style = normalize_subtitle_style(
            self.subtitle_style,
            defaults=get_subtitle_style_defaults(),
        )

    def _append_timeline(
        self,
        event: str,
        *,
        timestamp: Optional[float] = None,
        duration_seconds: Optional[float] = None,
        record_time: bool = True,
        **payload,
    ):
        """向任务时间线写入事件（最多保留最近200条）。"""
        ts = timestamp if timestamp is not None else time.time()
        entry: Dict[str, Any] = {
            "event": event,
            "timestamp": ts,
        }
        if duration_seconds is not None:
            entry["duration_seconds"] = round(max(0.0, duration_seconds), 3)

        for key, value in payload.items():
            if value is not None and value != "":
                entry[key] = value

        self.timeline.append(entry)
        if len(self.timeline) > 200:
            self.timeline = self.timeline[-200:]

        if record_time:
            self.updated_at = ts

    def mark_step(self, step: str):
        """记录执行步骤，用于排障和前端展示。"""
        if not step:
            return

        previous_step = self.last_step or ""
        if previous_step == step:
            return

        now = time.time()
        duration = (now - self.step_started_at) if (self.step_started_at and previous_step) else None
        self.last_step = step
        self.step_started_at = now
        self._append_timeline(
            "step_transition",
            timestamp=now,
            from_step=previous_step or None,
            to_step=step,
            duration_seconds=duration,
            record_time=False,
        )

    def transition(self, new_state: TaskState) -> bool:
        """
        状态转换

        Args:
            new_state: 新状态

        Returns:
            bool: 是否成功
        """
        current = TaskState(self.state)
        valid_next = VALID_TRANSITIONS.get(current, [])

        if new_state not in valid_next:
            logger.warning(
                f"非法状态转换: {current.value} → {new_state.value} "
                f"(合法: {[s.value for s in valid_next]})"
            )
            return False

        old_state = self.state
        now = time.time()
        duration = (now - self.state_entered_at) if self.state_entered_at else None
        self.state = new_state.value
        self.updated_at = now
        self.state_entered_at = now

        if new_state == TaskState.COMPLETED:
            self.completed_at = now
            self.progress = 100

        self._append_timeline(
            "state_transition",
            timestamp=now,
            from_state=old_state,
            to_state=new_state.value,
            duration_seconds=duration,
            record_time=False,
        )

        logger.info(f"📋 任务 {self.task_id}: {old_state} → {new_state.value}")
        return True

    def fail(self, error_message: str, error_code: str = ""):
        """标记任务失败"""
        self.error_message = error_message
        if error_code:
            self.last_error_code = error_code
        self.mark_step(TaskState.FAILED.value)
        self.transition(TaskState.FAILED)
        self._append_timeline(
            "task_failed",
            error_message=error_message[:300],
            error_code=error_code or self.last_error_code,
            record_time=False,
        )

    def add_product(self, product: TaskProduct):
        """添加产出物"""
        self.products.append(asdict(product))

    def to_dict(self) -> dict:
        """转为字典"""
        return asdict(self)

    def to_json(self) -> str:
        """转为JSON字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        """从字典创建"""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def from_json(cls, json_str: str) -> "Task":
        """从JSON字符串创建"""
        return cls.from_dict(json.loads(json_str))

    @property
    def duration_seconds(self) -> float:
        """任务耗时（秒）"""
        end = self.completed_at or time.time()
        return end - self.created_at

    @property
    def is_active(self) -> bool:
        """任务是否在活跃状态"""
        return TaskState(self.state) in TaskState.active_states()


class TaskStore:
    """
    任务持久化存储
    使用JSON文件存储（简单可靠，适合当前规模）
    """

    def __init__(self, store_path: str = None):
        if store_path is None:
            store_path = str(Path.home() / ".video-factory" / "tasks.json")

        self.store_path = Path(store_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)

        self._tasks: Dict[str, Task] = {}
        self._last_mtime: float = 0.0
        self._load()

    def _load(self, force: bool = False):
        """从文件加载任务"""
        if not self.store_path.exists():
            self._tasks = {}
            self._last_mtime = 0.0
            return

        try:
            mtime = self.store_path.stat().st_mtime
            if not force and self._last_mtime and mtime <= self._last_mtime:
                return

            with open(self.store_path, "r") as f:
                data = json.load(f)

            loaded_tasks: Dict[str, Task] = {}
            for task_id, task_data in data.items():
                loaded_tasks[task_id] = Task.from_dict(task_data)

            self._tasks = loaded_tasks
            self._last_mtime = mtime
            logger.info(f"📂 加载了 {len(self._tasks)} 个任务")
        except Exception as e:
            logger.error(f"加载任务失败: {e}")

    def _refresh(self):
        """按需刷新内存任务（多进程同步）。"""
        self._load(force=False)

    def _save(self):
        """保存到文件"""
        try:
            data = {tid: t.to_dict() for tid, t in self._tasks.items()}
            tmp_path = self.store_path.with_suffix(".json.tmp")
            with open(tmp_path, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.store_path)
            self._last_mtime = self.store_path.stat().st_mtime
        except Exception as e:
            logger.error(f"保存任务失败: {e}")

    def create(self, **kwargs) -> Task:
        """创建新任务"""
        self._refresh()
        task = Task(**kwargs)
        self._tasks[task.task_id] = task
        self._save()
        logger.info(f"📝 创建任务: {task.task_id}")
        return task

    def get(self, task_id: str) -> Optional[Task]:
        """获取任务"""
        self._refresh()
        return self._tasks.get(task_id)

    def update(self, task: Task):
        """更新任务"""
        self._refresh()
        task.updated_at = time.time()
        self._tasks[task.task_id] = task
        self._save()

    def delete(self, task_id: str):
        """删除任务"""
        self._refresh()
        if task_id in self._tasks:
            del self._tasks[task_id]
            self._save()

    def list_by_state(self, state: TaskState) -> List[Task]:
        """按状态列出任务"""
        self._refresh()
        return [t for t in self._tasks.values() if t.state == state.value]

    def list_active(self) -> List[Task]:
        """列出所有活跃任务"""
        self._refresh()
        return [t for t in self._tasks.values() if t.is_active]

    def list_all(self) -> List[Task]:
        """列出所有任务"""
        self._refresh()
        return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)

    def get_stats(self) -> Dict[str, int]:
        """获取任务统计"""
        self._refresh()
        stats = {}
        for task in self._tasks.values():
            state = task.state
            stats[state] = stats.get(state, 0) + 1
        stats["total"] = len(self._tasks)
        return stats
