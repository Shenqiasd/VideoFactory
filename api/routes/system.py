"""
系统管理路由 - 健康检查、存储状态、配置
"""
import logging
import os
import platform
import uuid
from pathlib import Path
from typing import Optional, Any
from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
import yaml

from core.config import Config
from core.storage import StorageManager, LocalStorage
from core.task import TaskStore
from core.runtime import read_worker_heartbeat
from core.runtime_settings import (
    get_subtitle_style_defaults,
    set_subtitle_style_defaults,
)
from core.subtitle_style import normalize_subtitle_style
from translation import SUPPORTED_TRANSLATION_PROVIDERS
from translation.llm_translator import LLMTranslator
from translation.local_llm import LocalLLMTranslator
from translation.volcengine_ark import VolcengineArkTranslator
from tts.volcengine_tts import VolcengineTTS, DEFAULT_VOLCENGINE_VOICES

logger = logging.getLogger(__name__)
router = APIRouter()

ALLOWED_ASR_PROVIDERS = {"auto", "youtube", "volcengine", "whisper"}
ALLOWED_ASR_FALLBACK_ITEMS = {"youtube", "volcengine", "whisper"}
ALLOWED_TTS_PROVIDERS = {"volcengine"}
ALLOWED_TTS_FALLBACK_ITEMS = {"volcengine"}
ALLOWED_TRANSLATION_PROVIDERS = set(SUPPORTED_TRANSLATION_PROVIDERS)


# Request models
class YouTubeCookiesRequest(BaseModel):
    cookies: str


class SubtitleStyleDefaultsRequest(BaseModel):
    subtitle_style: dict


class ASRWhisperSettings(BaseModel):
    base_url: str = "http://127.0.0.1:8866/v1"
    model: str = "whisper-1"
    timeout: int = 600

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("whisper.timeout 必须大于 0")
        return value


class ASRVolcengineSettings(BaseModel):
    enabled: bool = False
    app_id: str = ""
    token: str = ""
    http_url: str = ""
    ws_url: str = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
    timeout: int = 120

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("volcengine.timeout 必须大于 0")
        return value


class ASRSettingsRequest(BaseModel):
    provider: str = "auto"
    allow_fallback: bool = True
    allow_router_with_tts: bool = True
    fallback_order: list[str] = Field(default_factory=lambda: ["youtube", "volcengine", "whisper"])
    youtube_skip_download: bool = False
    youtube_preferred_langs: list[str] = Field(default_factory=lambda: ["en", "en-US", "en-GB"])
    whisper: ASRWhisperSettings = Field(default_factory=ASRWhisperSettings)
    volcengine: ASRVolcengineSettings = Field(default_factory=ASRVolcengineSettings)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in ALLOWED_ASR_PROVIDERS:
            raise ValueError(f"asr.provider 非法: {value}")
        return normalized

    @field_validator("fallback_order")
    @classmethod
    def validate_fallback_order(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            candidate = (item or "").strip().lower()
            if not candidate:
                continue
            if candidate not in ALLOWED_ASR_FALLBACK_ITEMS:
                raise ValueError(f"asr.fallback_order 包含非法值: {item}")
            if candidate not in cleaned:
                cleaned.append(candidate)
        if not cleaned:
            raise ValueError("asr.fallback_order 不能为空")
        return cleaned

    @field_validator("youtube_preferred_langs")
    @classmethod
    def validate_youtube_preferred_langs(cls, value: list[str]) -> list[str]:
        cleaned = [lang.strip() for lang in value if lang and lang.strip()]
        return cleaned or ["en", "en-US", "en-GB"]


class TTSVolcengineSettings(BaseModel):
    enabled: bool = False
    # 新格式（推荐）
    appid: str = ""
    access_token: str = ""
    resource_id: str = "seed-tts-1.0"
    cluster: str = "volcano_tts"
    api_url: str = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
    default_voice: str = "zh_female_shuangkuaisisi_moon_bigtts"
    encoding: str = "mp3"
    sample_rate: int = 24000
    speed_ratio: float = 1.0
    volume_ratio: float = 1.0
    pitch_ratio: float = 1.0
    available_voices: list[dict[str, str]] = Field(default_factory=lambda: list(DEFAULT_VOLCENGINE_VOICES))
    # 旧格式（向后兼容）
    app_id: str = ""
    token: str = ""
    clone_url: str = ""
    synthesis_url: str = ""
    voice_id: str = ""
    timeout: int = 120

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("tts.volcengine.timeout 必须大于 0")
        return value

    @field_validator("sample_rate")
    @classmethod
    def validate_sample_rate(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("tts.volcengine.sample_rate 必须大于 0")
        return value

    @field_validator("available_voices")
    @classmethod
    def validate_available_voices(cls, value: list[dict[str, str]]) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        seen = set()
        for item in value or []:
            if not isinstance(item, dict):
                continue
            voice_id = str(item.get("id", "")).strip()
            if not voice_id or voice_id in seen:
                continue
            normalized.append(
                {
                    "id": voice_id,
                    "name": str(item.get("name", voice_id)).strip() or voice_id,
                    "language": str(item.get("language", "zh-CN")).strip() or "zh-CN",
                }
            )
            seen.add(voice_id)
        return normalized or list(DEFAULT_VOLCENGINE_VOICES)


class TTSSettingsRequest(BaseModel):
    provider: str = "volcengine"
    fallback_order: list[str] = Field(default_factory=lambda: ["volcengine"])
    volcengine: TTSVolcengineSettings = Field(default_factory=TTSVolcengineSettings)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in ALLOWED_TTS_PROVIDERS:
            raise ValueError(f"tts.provider 非法: {value}")
        return normalized

    @field_validator("fallback_order")
    @classmethod
    def validate_fallback_order(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            candidate = (item or "").strip().lower()
            if not candidate:
                continue
            if candidate not in ALLOWED_TTS_FALLBACK_ITEMS:
                raise ValueError(f"tts.fallback_order 包含非法值: {item}")
            if candidate not in cleaned:
                cleaned.append(candidate)
        if not cleaned:
            raise ValueError("tts.fallback_order 不能为空")
        return cleaned


class TranslationVolcengineArkSettings(BaseModel):
    enabled: bool = False
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    api_key: str = ""
    model: str = "doubao-seed-translation-250915"
    timeout: int = 60

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("translation.volcengine_ark.timeout 必须大于 0")
        return value


class TranslationLocalLLMSettings(BaseModel):
    enabled: bool = False
    base_url: str = "http://127.0.0.1:1234/v1"
    api_key: str = ""
    model: str = ""
    timeout: int = 120

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("translation.local_llm.timeout 必须大于 0")
        return value


class TranslationLLMSettings(BaseModel):
    timeout: int = 60

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("translation.llm.timeout 必须大于 0")
        return value


class TranslationSettingsRequest(BaseModel):
    provider: str = "llm"
    strict_json: bool = True
    local_llm: TranslationLocalLLMSettings = Field(default_factory=TranslationLocalLLMSettings)
    volcengine_ark: TranslationVolcengineArkSettings = Field(default_factory=TranslationVolcengineArkSettings)
    llm: TranslationLLMSettings = Field(default_factory=TranslationLLMSettings)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in ALLOWED_TRANSLATION_PROVIDERS:
            raise ValueError(f"translation.provider 非法: {value}")
        return normalized


class ASRTTSSettingsRequest(BaseModel):
    asr: ASRSettingsRequest
    tts: TTSSettingsRequest
    translation: Optional[TranslationSettingsRequest] = None


class TranslationTestRequest(BaseModel):
    provider: str = "volcengine_ark"
    text: str = "Hello world"
    source_lang: str = "en"
    target_lang: str = "zh-CN"
    enabled: Optional[bool] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    timeout: Optional[int] = None

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in ALLOWED_TRANSLATION_PROVIDERS:
            raise ValueError(f"provider 非法: {value}")
        return normalized

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value <= 0:
            raise ValueError("timeout 必须大于 0")
        return value


class TTSTestRequest(BaseModel):
    provider: str = "volcengine"
    text: str = "你好，这是火山语音合成测试。"
    voice_type: str = "zh_female_shuangkuaisisi_moon_bigtts"
    enabled: Optional[bool] = None
    appid: Optional[str] = None
    access_token: Optional[str] = None
    resource_id: Optional[str] = None
    cluster: Optional[str] = None
    api_url: Optional[str] = None
    timeout: Optional[int] = None
    encoding: Optional[str] = None
    sample_rate: Optional[int] = None
    speed_ratio: Optional[float] = None
    volume_ratio: Optional[float] = None
    pitch_ratio: Optional[float] = None

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in ALLOWED_TTS_PROVIDERS:
            raise ValueError(f"provider 非法: {value}")
        return normalized

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value <= 0:
            raise ValueError("timeout 必须大于 0")
        return value

    @field_validator("sample_rate")
    @classmethod
    def validate_sample_rate(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value <= 0:
            raise ValueError("sample_rate 必须大于 0")
        return value


class _DictConfig:
    """
    轻量级配置适配器，用于测试接口按请求参数临时覆盖配置。
    """

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def get(self, *keys, default=None):
        node: Any = self._data
        for key in keys:
            if isinstance(node, dict):
                node = node.get(key)
            else:
                return default
            if node is None:
                return default
        return node


def _config_file_path() -> Path:
    env_path = os.environ.get("VF_CONFIG", "")
    if env_path:
        return Path(env_path)
    return Path(__file__).resolve().parents[2] / "config" / "settings.yaml"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _build_asr_defaults() -> dict[str, Any]:
    return {
        "provider": "auto",
        "allow_fallback": True,
        "allow_router_with_tts": True,
        "fallback_order": ["youtube", "volcengine", "whisper"],
        "youtube_skip_download": False,
        "youtube_preferred_langs": ["en", "en-US", "en-GB"],
        "whisper": {
            "base_url": "http://127.0.0.1:8866/v1",
            "model": "whisper-1",
            "timeout": 600,
        },
        "volcengine": {
            "enabled": False,
            "app_id": "",
            "token": "",
            "http_url": "",
            "ws_url": "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel",
            "timeout": 120,
        },
    }


def _build_tts_defaults() -> dict[str, Any]:
    return {
        "provider": "volcengine",
        "fallback_order": ["volcengine"],
        "volcengine": {
            "enabled": False,
            "appid": "",
            "access_token": "",
            "resource_id": "seed-tts-1.0",
            "cluster": "volcano_tts",
            "api_url": "https://openspeech.bytedance.com/api/v3/tts/unidirectional",
            "default_voice": "zh_female_shuangkuaisisi_moon_bigtts",
            "encoding": "mp3",
            "sample_rate": 24000,
            "speed_ratio": 1.0,
            "volume_ratio": 1.0,
            "pitch_ratio": 1.0,
            "available_voices": list(DEFAULT_VOLCENGINE_VOICES),
            "app_id": "",
            "token": "",
            "clone_url": "",
            "synthesis_url": "",
            "voice_id": "",
            "timeout": 120,
        },
    }


def _build_translation_defaults(cfg: Optional[Config] = None) -> dict[str, Any]:
    config = cfg or Config()
    return {
        "provider": "llm",
        "strict_json": bool(config.get("llm", "strict_json", default=False)),
        "local_llm": {
            "enabled": False,
            "base_url": "http://127.0.0.1:1234/v1",
            "api_key": "",
            "model": "",
            "timeout": 120,
        },
        "volcengine_ark": {
            "enabled": False,
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "api_key": "",
            "model": "doubao-seed-translation-250915",
            "timeout": 60,
        },
        "llm": {
            "timeout": 60,
        },
    }


def _normalize_tts_volcengine_config(tts_data: dict[str, Any]) -> dict[str, Any]:
    volc = tts_data.get("volcengine", {})
    if not isinstance(volc, dict):
        return tts_data

    appid = str(volc.get("appid") or volc.get("app_id") or "").strip()
    access_token = str(volc.get("access_token") or volc.get("token") or "").strip()
    resource_id = str(volc.get("resource_id") or "").strip() or "seed-tts-1.0"
    api_url = str(volc.get("api_url") or volc.get("synthesis_url") or "").strip()
    default_voice = str(
        volc.get("default_voice")
        or volc.get("voice_type")
        or volc.get("voice_id")
        or "zh_female_shuangkuaisisi_moon_bigtts"
    ).strip() or "zh_female_shuangkuaisisi_moon_bigtts"

    volc["appid"] = appid
    volc["app_id"] = appid
    volc["access_token"] = access_token
    volc["token"] = access_token
    volc["resource_id"] = resource_id
    volc["api_url"] = api_url
    volc["synthesis_url"] = api_url
    volc["default_voice"] = default_voice
    volc["voice_type"] = default_voice
    volc["voice_id"] = default_voice

    voices = volc.get("available_voices")
    if not isinstance(voices, list) or not voices:
        volc["available_voices"] = list(DEFAULT_VOLCENGINE_VOICES)

    try:
        sample_rate = int(volc.get("sample_rate", 24000) or 24000)
    except (TypeError, ValueError):
        sample_rate = 24000
    volc["sample_rate"] = sample_rate if sample_rate > 0 else 24000

    tts_data["volcengine"] = volc
    return tts_data


def _normalize_asr_config(asr_data: Any) -> dict[str, Any]:
    merged = _deep_merge(_build_asr_defaults(), asr_data if isinstance(asr_data, dict) else {})
    provider = str(merged.get("provider", "auto")).strip().lower()
    if provider not in ALLOWED_ASR_PROVIDERS:
        provider = "auto"
    merged["provider"] = provider
    merged.pop("allow_klicstudio_fallback", None)

    fallback_order = []
    for item in merged.get("fallback_order", []):
        candidate = str(item or "").strip().lower()
        if candidate in ALLOWED_ASR_FALLBACK_ITEMS and candidate not in fallback_order:
            fallback_order.append(candidate)
    merged["fallback_order"] = fallback_order or ["youtube", "volcengine", "whisper"]

    preferred_langs = [
        str(lang).strip()
        for lang in merged.get("youtube_preferred_langs", [])
        if str(lang).strip()
    ]
    merged["youtube_preferred_langs"] = preferred_langs or ["en", "en-US", "en-GB"]
    return merged


def _normalize_tts_config(tts_data: Any) -> dict[str, Any]:
    merged = _deep_merge(_build_tts_defaults(), tts_data if isinstance(tts_data, dict) else {})
    provider = str(merged.get("provider", "volcengine")).strip().lower()
    if provider not in ALLOWED_TTS_PROVIDERS:
        provider = "volcengine"
    merged["provider"] = provider
    merged["fallback_order"] = ["volcengine"]
    return _normalize_tts_volcengine_config(merged)


def _test_audio_root() -> Path:
    return Path("/tmp/video-factory/system-tests")


def _ensure_config_file(path: Path) -> None:
    """When settings.yaml is absent, bootstrap it from settings.example.yaml."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    example = path.parent / "settings.example.yaml"
    if example.exists():
        import shutil
        shutil.copy2(example, path)
        logger.info("配置文件不存在，已从 %s 初始化", example)
    else:
        path.write_text("{}\n", encoding="utf-8")
        logger.info("配置文件不存在，已创建空配置: %s", path)


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


@router.get("/info")
async def get_system_info():
    """获取系统信息"""
    config = Config()

    return {
        "service": "video-factory",
        "version": "0.1.0",
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "r2_bucket": config.get("storage", "r2", "bucket", default=""),
    }


@router.get("/storage")
async def get_storage_status():
    """获取存储状态"""
    config = Config()

    local = LocalStorage(
        working_dir=config.get("storage", "local", "mac_working_dir", default="/tmp/video-factory/working"),
        output_dir=config.get("storage", "local", "mac_output_dir", default="/tmp/video-factory/output"),
    )

    disk_usage = local.get_disk_usage()

    # R2文件列表（根目录）
    storage = StorageManager(
        bucket=config.get("storage", "r2", "bucket", default="videoflow"),
    )
    r2_root_files = storage.list_r2_files("")

    return {
        "disk": disk_usage,
        "r2_bucket": config.get("storage", "r2", "bucket", default="videoflow"),
        "r2_root_items": len(r2_root_files),
        "r2_root_files": r2_root_files[:20],
        "working_dir": str(local.working_dir),
        "output_dir": str(local.output_dir),
    }


@router.get("/tasks-stats")
async def get_tasks_overview():
    """获取任务概览"""
    store = TaskStore()
    stats = store.get_stats()
    active = store.list_active()

    return {
        "stats": stats,
        "active_tasks": [
            {
                "task_id": t.task_id,
                "state": t.state,
                "progress": t.progress,
                "source_title": t.source_title,
            }
            for t in active
        ],
    }


@router.get("/runtime")
async def get_runtime_status():
    """获取运行时状态（进程心跳 + 队列）"""
    store = TaskStore()
    stats = store.get_stats()
    heartbeat = read_worker_heartbeat(max_age_seconds=90)

    return {
        "worker": heartbeat,
        "queue": {
            "queued": stats.get("queued", 0),
            "active": len(store.list_active()),
            "failed": stats.get("failed", 0),
            "total": stats.get("total", 0),
        },
    }


@router.get("/config")
async def get_current_config():
    """获取当前配置（脱敏）"""
    config = Config()

    return {
        "storage": {
            "r2_bucket": config.get("storage", "r2", "bucket", default=""),
            "working_dir": config.get("storage", "local", "mac_working_dir", default=""),
            "output_dir": config.get("storage", "local", "mac_output_dir", default=""),
        },
        "tasks": config.get("tasks", default={}),
    }


@router.get("/subtitle-style-defaults")
async def get_subtitle_style_defaults_api():
    """获取默认字幕样式（任务级默认值）。"""
    return {"subtitle_style": get_subtitle_style_defaults()}


@router.post("/subtitle-style-defaults")
async def set_subtitle_style_defaults_api(request: SubtitleStyleDefaultsRequest):
    """更新默认字幕样式。"""
    normalized = normalize_subtitle_style(
        request.subtitle_style,
        defaults=get_subtitle_style_defaults(),
    )
    saved = set_subtitle_style_defaults(normalized)
    return {"message": "默认字幕样式已更新", "subtitle_style": saved}


@router.get("/settings/asr-tts")
async def get_asr_tts_settings():
    """
    获取翻译/ASR/TTS 配置。
    """
    cfg = Config()
    asr = cfg.get("asr", default={}) or {}
    tts = cfg.get("tts", default={}) or {}
    translation = cfg.get("translation", default={}) or {}
    merged_asr = _normalize_asr_config(asr)
    merged_tts = _normalize_tts_config(tts)
    merged_translation = _deep_merge(
        _build_translation_defaults(cfg),
        translation if isinstance(translation, dict) else {},
    )
    return {
        "translation": merged_translation,
        "asr": merged_asr,
        "tts": merged_tts,
    }


@router.post("/settings/asr-tts")
async def set_asr_tts_settings(request: ASRTTSSettingsRequest):
    """
    更新翻译/ASR/TTS 配置到 settings.yaml。
    保存后会重置 API 进程内配置缓存；Worker 进程建议重启以加载新配置。
    """
    config_data = _read_yaml_config()
    config_data["asr"] = request.asr.model_dump()
    config_data["tts"] = _normalize_tts_config(request.tts.model_dump())
    config_data.pop("klicstudio", None)
    if request.translation is not None:
        config_data["translation"] = request.translation.model_dump()
    _write_yaml_config(config_data)
    Config.reset()
    return {
        "success": True,
        "message": "翻译/ASR/TTS 配置已保存（Worker 需重启后生效）",
        "config_path": str(_config_file_path()),
        "translation": config_data.get("translation", {}),
        "asr": config_data["asr"],
        "tts": config_data["tts"],
    }


@router.get("/tts/voices")
async def get_tts_voices():
    """
    获取火山 TTS 可选音色列表。
    """
    tts = VolcengineTTS(config=Config())
    return {
        "provider": tts.name,
        "default_voice": tts.default_voice,
        "voices": tts.get_available_voices(),
    }


@router.post("/test/translation")
async def test_translation(request: TranslationTestRequest):
    """
    快速测试翻译连通性（无需创建任务）。
    """
    provider = request.provider.strip().lower()
    cfg = Config()
    try:
        if provider == "local_llm":
            runtime_cfg = cfg.get("translation", "local_llm", default={}) or {}
            if not isinstance(runtime_cfg, dict):
                runtime_cfg = {}
            runtime_cfg = dict(runtime_cfg)

            if request.base_url is not None:
                runtime_cfg["base_url"] = request.base_url.strip()
            if request.api_key is not None:
                runtime_cfg["api_key"] = request.api_key.strip()
            if request.model is not None:
                runtime_cfg["model"] = request.model.strip()
            if request.timeout is not None:
                runtime_cfg["timeout"] = int(request.timeout)
            if request.enabled is not None:
                runtime_cfg["enabled"] = bool(request.enabled)
            elif str(runtime_cfg.get("base_url", "")).strip() and str(runtime_cfg.get("model", "")).strip():
                runtime_cfg["enabled"] = True

            translator = LocalLLMTranslator(
                config=_DictConfig({"translation": {"local_llm": runtime_cfg}})
            )
            if not translator.is_configured():
                raise HTTPException(status_code=400, detail="本地翻译模型未配置完整或未启用")
        elif provider == "volcengine_ark":
            runtime_cfg = cfg.get("translation", "volcengine_ark", default={}) or {}
            if not isinstance(runtime_cfg, dict):
                runtime_cfg = {}
            runtime_cfg = dict(runtime_cfg)

            if request.base_url is not None:
                runtime_cfg["base_url"] = request.base_url.strip()
            if request.api_key is not None:
                runtime_cfg["api_key"] = request.api_key.strip()
            if request.model is not None:
                runtime_cfg["model"] = request.model.strip()
            if request.timeout is not None:
                runtime_cfg["timeout"] = int(request.timeout)
            if request.enabled is not None:
                runtime_cfg["enabled"] = bool(request.enabled)
            elif str(runtime_cfg.get("api_key", "")).strip():
                # 显式传入 API Key 时，默认启用测试
                runtime_cfg["enabled"] = True

            translator = VolcengineArkTranslator(
                config=_DictConfig({"translation": {"volcengine_ark": runtime_cfg}})
            )
            if not translator.is_configured():
                raise HTTPException(status_code=400, detail="火山方舟翻译未配置或未启用")
        elif provider == "llm":
            translator = LLMTranslator(config=cfg)
        else:
            raise HTTPException(status_code=400, detail=f"不支持的翻译 provider: {provider}")

        result = await translator.translate_text(
            text=request.text,
            source_lang=request.source_lang,
            target_lang=request.target_lang,
        )
        runtime = translator.runtime_config()
        return {
            "success": True,
            "provider": provider,
            "source_lang": request.source_lang,
            "target_lang": request.target_lang,
            "base_url": runtime.base_url,
            "model": runtime.model,
            "result": result,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("翻译测试失败 provider=%s: %s", provider, exc)
        raise HTTPException(status_code=500, detail=f"翻译测试失败: {str(exc)}") from exc


@router.post("/test/tts")
async def test_tts(request: TTSTestRequest):
    """
    快速测试 TTS 连通性（返回可播放音频 URL）。
    """
    provider = request.provider.strip().lower()
    if provider != "volcengine":
        raise HTTPException(status_code=400, detail="当前仅支持 volcengine 测试")

    cfg = Config()
    runtime_cfg = cfg.get("tts", "volcengine", default={}) or {}
    if not isinstance(runtime_cfg, dict):
        runtime_cfg = {}
    runtime_cfg = dict(runtime_cfg)

    if request.appid is not None:
        appid = request.appid.strip()
        if appid:
            runtime_cfg["appid"] = appid
            runtime_cfg["app_id"] = appid
    if request.access_token is not None:
        token = request.access_token.strip()
        if token:
            runtime_cfg["access_token"] = token
            runtime_cfg["token"] = token
    if request.resource_id is not None:
        runtime_cfg["resource_id"] = request.resource_id.strip() or "seed-tts-1.0"
    if request.cluster is not None:
        runtime_cfg["cluster"] = request.cluster.strip()
    if request.api_url is not None:
        api_url = request.api_url.strip()
        if api_url:
            runtime_cfg["api_url"] = api_url
            runtime_cfg["synthesis_url"] = api_url
    if request.timeout is not None:
        runtime_cfg["timeout"] = int(request.timeout)
    if request.encoding is not None:
        runtime_cfg["encoding"] = request.encoding.strip()
    if request.sample_rate is not None:
        runtime_cfg["sample_rate"] = int(request.sample_rate)
    if request.speed_ratio is not None:
        runtime_cfg["speed_ratio"] = float(request.speed_ratio)
    if request.volume_ratio is not None:
        runtime_cfg["volume_ratio"] = float(request.volume_ratio)
    if request.pitch_ratio is not None:
        runtime_cfg["pitch_ratio"] = float(request.pitch_ratio)

    if request.enabled is not None:
        runtime_cfg["enabled"] = bool(request.enabled)
    elif str(runtime_cfg.get("appid") or runtime_cfg.get("app_id") or "").strip() and str(
        runtime_cfg.get("access_token") or runtime_cfg.get("token") or ""
    ).strip():
        # 显式传入 appid/token 时，默认启用测试
        runtime_cfg["enabled"] = True

    tts = VolcengineTTS(config=_DictConfig({"tts": {"volcengine": runtime_cfg}}))
    if not tts.enabled:
        raise HTTPException(status_code=400, detail="火山 TTS 未启用")
    if not tts.appid or not tts.access_token:
        raise HTTPException(status_code=400, detail="火山 TTS 缺少 appid/access_token")

    encoding = str(tts.encoding).strip().lower()
    ext = {
        "mp3": "mp3",
        "ogg_opus": "ogg",
        "pcm": "pcm",
        "wav": "wav",
    }.get(encoding, "wav")
    root = _test_audio_root()
    root.mkdir(parents=True, exist_ok=True)
    out_path = root / f"tts_test_{uuid.uuid4().hex}.{ext}"

    try:
        result = await tts.synthesize(
            text=request.text,
            output_path=str(out_path),
            voice_type=request.voice_type,
        )
    except Exception as exc:
        logger.warning("TTS 测试异常: %s", exc)
        raise HTTPException(status_code=500, detail=f"TTS 测试失败: {str(exc)}") from exc

    if not result or not Path(result.audio_path).exists():
        detail = tts.last_error or "TTS 合成失败：未生成音频"
        raise HTTPException(status_code=500, detail=detail)

    return {
        "success": True,
        "provider": provider,
        "voice_type": request.voice_type,
        "audio_url": f"/api/system/test/tts/audio/{Path(result.audio_path).name}",
        "size_bytes": Path(result.audio_path).stat().st_size,
    }


@router.get("/test/tts/audio/{filename}")
async def get_test_tts_audio(filename: str):
    """
    返回 TTS 测试音频文件。
    """
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="非法文件名")

    file_path = _test_audio_root() / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="音频不存在")

    suffix = file_path.suffix.lower()
    if suffix == ".mp3":
        media_type = "audio/mpeg"
    elif suffix == ".ogg":
        media_type = "audio/ogg"
    elif suffix == ".pcm":
        media_type = "audio/L16"
    else:
        media_type = "audio/wav"
    return FileResponse(path=str(file_path), media_type=media_type, filename=file_path.name)


@router.post("/settings/youtube-cookies")
async def save_youtube_cookies(cookies: str = Form(...)):
    """
    保存 YouTube cookies 到本地文件
    用于解决 yt-dlp 下载时的 bot 验证问题
    """
    try:
        # Cookies 保存路径
        config = Config()
        working_dir = config.get("storage", "local", "mac_working_dir", default="/tmp/video-factory/working")
        cookies_dir = Path(working_dir).parent / "config"
        cookies_dir.mkdir(parents=True, exist_ok=True)
        cookies_file = cookies_dir / "youtube_cookies.txt"

        # 验证 cookies 格式（简单检查）
        cookies_content = cookies.strip()
        if not cookies_content:
            raise HTTPException(status_code=400, detail="Cookies 内容不能为空")

        # 检查是否包含 Netscape Cookie File 标识
        if "# Netscape HTTP Cookie File" not in cookies_content and "# HTTP Cookie File" not in cookies_content:
            # 如果用户忘记添加头部，自动添加
            cookies_content = "# Netscape HTTP Cookie File\n# This file was auto-generated by video-factory\n\n" + cookies_content

        # 保存到文件
        cookies_file.write_text(cookies_content, encoding="utf-8")

        logger.info(f"✅ YouTube cookies 已保存到: {cookies_file}")

        return {
            "success": True,
            "message": f"Cookies 已保存到 {cookies_file}",
            "path": str(cookies_file),
            "size": len(cookies_content),
        }

    except Exception as e:
        logger.error(f"❌ 保存 YouTube cookies 失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存失败: {str(e)}")


@router.get("/settings/youtube-cookies")
async def get_youtube_cookies():
    """
    读取已保存的 YouTube cookies
    """
    try:
        config = Config()
        working_dir = config.get("storage", "local", "mac_working_dir", default="/tmp/video-factory/working")
        cookies_file = Path(working_dir).parent / "config" / "youtube_cookies.txt"

        if cookies_file.exists():
            cookies_content = cookies_file.read_text(encoding="utf-8")
            return {
                "exists": True,
                "cookies": cookies_content,
                "path": str(cookies_file),
                "size": len(cookies_content),
            }
        else:
            return {
                "exists": False,
                "message": "尚未配置 YouTube cookies",
            }

    except Exception as e:
        logger.error(f"❌ 读取 YouTube cookies 失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取失败: {str(e)}")
