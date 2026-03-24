"""
系统管理路由 - 健康检查、存储状态、配置
"""
import logging
import os
import platform
import uuid
from pathlib import Path
from typing import Optional, Any
from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
import yaml

from api.auth import mask_dict_secrets, require_auth
from core.config import Config

# Keys whose empty-string values from the frontend mean "keep existing".
_SECRET_KEYS = frozenset({"api_key", "token", "access_token", "secret", "password"})


def _restore_secrets(new: dict, old: dict) -> dict:
    """Recursively restore secret values that the frontend left blank or masked."""
    merged: dict = {}
    for key, value in new.items():
        if isinstance(value, dict):
            merged[key] = _restore_secrets(value, old.get(key, {}) if isinstance(old.get(key), dict) else {})
        elif key in _SECRET_KEYS and isinstance(value, str) and (
            value == "" or value.startswith("****")
        ):
            # Empty string = frontend didn't touch the masked field.
            # Starts with '****' = user accidentally submitted the masked placeholder.
            # Either way, keep the existing real value.
            merged[key] = old.get(key, "") if isinstance(old.get(key), str) else ""
        else:
            merged[key] = value
    return merged
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


def _read_yaml_config() -> dict[str, Any]:
    path = _config_file_path()
    if not path.exists():
        # 自动从 example 创建
        example = path.parent / "settings.example.yaml"
        try:
            if example.exists():
                import shutil
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(example, path)
                logger.info("配置文件不存在，已从 settings.example.yaml 自动创建: %s", path)
            else:
                # 创建最小空配置
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(yaml.safe_dump({}, allow_unicode=True), encoding="utf-8")
                logger.info("配置文件不存在，已创建空配置: %s", path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"自动创建配置文件失败: {str(e)}") from e
    try:
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


@router.get("/config", dependencies=[Depends(require_auth)])
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


@router.get("/subtitle-style-defaults", dependencies=[Depends(require_auth)])
async def get_subtitle_style_defaults_api():
    """获取默认字幕样式（任务级默认值）。"""
    return {"subtitle_style": get_subtitle_style_defaults()}


@router.post("/subtitle-style-defaults", dependencies=[Depends(require_auth)])
async def set_subtitle_style_defaults_api(request: SubtitleStyleDefaultsRequest):
    """更新默认字幕样式。"""
    normalized = normalize_subtitle_style(
        request.subtitle_style,
        defaults=get_subtitle_style_defaults(),
    )
    saved = set_subtitle_style_defaults(normalized)
    return {"message": "默认字幕样式已更新", "subtitle_style": saved}


@router.get("/settings/asr-tts", dependencies=[Depends(require_auth)])
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
        "translation": mask_dict_secrets(merged_translation),
        "asr": mask_dict_secrets(merged_asr),
        "tts": mask_dict_secrets(merged_tts),
    }


@router.post("/settings/asr-tts", dependencies=[Depends(require_auth)])
async def set_asr_tts_settings(request: ASRTTSSettingsRequest):
    """
    更新翻译/ASR/TTS 配置到 settings.yaml。
    保存后会重置 API 进程内配置缓存；Worker 进程建议重启以加载新配置。
    """
    config_data = _read_yaml_config()

    # Restore secret values the frontend left blank (masked placeholders).
    new_asr = request.asr.model_dump()
    new_tts = _normalize_tts_config(request.tts.model_dump())
    new_asr = _restore_secrets(new_asr, config_data.get("asr") or {})
    new_tts = _restore_secrets(new_tts, config_data.get("tts") or {})

    config_data["asr"] = new_asr
    config_data["tts"] = new_tts
    config_data.pop("klicstudio", None)
    if request.translation is not None:
        new_translation = request.translation.model_dump()
        new_translation = _restore_secrets(
            new_translation, config_data.get("translation") or {}
        )
        config_data["translation"] = new_translation
    _write_yaml_config(config_data)
    Config.reset()
    return {
        "success": True,
        "message": "翻译/ASR/TTS 配置已保存（Worker 需重启后生效）",
        "config_path": str(_config_file_path()),
        "translation": mask_dict_secrets(config_data.get("translation") or {}),
        "asr": mask_dict_secrets(config_data["asr"]),
        "tts": mask_dict_secrets(config_data["tts"]),
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


@router.post("/test/translation", dependencies=[Depends(require_auth)])
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


@router.post("/test/tts", dependencies=[Depends(require_auth)])
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


@router.post("/settings/youtube-cookies", dependencies=[Depends(require_auth)])
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


@router.get("/settings/youtube-cookies", dependencies=[Depends(require_auth)])
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
            from api.auth import mask_secret as _mask
            return {
                "exists": True,
                "cookies": _mask(cookies_content, visible_tail=20),
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


# ---------------------------------------------------------------------------
# OAuth 平台配置
# ---------------------------------------------------------------------------

# 每个平台在 settings.yaml 中的密钥字段名映射
_OAUTH_PLATFORM_FIELDS: dict[str, tuple[str, str]] = {
    "youtube":      ("client_id", "client_secret"),
    "bilibili":     ("client_id", "client_secret"),
    "tiktok":       ("client_id", "client_secret"),
    "douyin":       ("client_id", "client_secret"),
    "facebook":     ("app_id",    "app_secret"),
    "instagram":    ("app_id",    "app_secret"),
    "twitter":      ("client_id", "client_secret"),
    "pinterest":    ("client_id", "client_secret"),
    "linkedin":     ("client_id", "client_secret"),
    "kwai":         ("client_id", "client_secret"),
    "xiaohongshu":  ("client_id", "client_secret"),
    "weixin_sph":   ("app_id",    "app_secret"),
    "weixin_gzh":   ("app_id",    "app_secret"),
    "threads":      ("app_id",    "app_secret"),
}

# 每个平台的详细配置引导元数据
_OAUTH_PLATFORM_GUIDES: dict[str, dict] = {
    "youtube": {
        "name": "YouTube",
        "icon": "youtube",
        "color": "#FF0000",
        "group": "international",
        "auth_method": "OAuth 2.0",
        "auth_type": "oauth2",
        "scopes": "YouTube Data API v3 (上传、管理视频)",
        "dev_portal_url": "https://console.cloud.google.com/apis/credentials",
        "dev_portal_name": "Google Cloud Console",
        "env_vars": ["OAUTH_YOUTUBE_CLIENT_ID", "OAUTH_YOUTUBE_CLIENT_SECRET"],
        "guide_steps": [
            "前往 Google Cloud Console (console.cloud.google.com)，创建或选择项目",
            "在「API 和服务」→「库」中启用 YouTube Data API v3",
            "进入「API 和服务」→「凭据」→ 创建 OAuth 2.0 客户端 ID",
            "应用类型选择「Web 应用」，添加授权重定向 URI：{callback_url}/api/oauth/callback/youtube",
            "复制 Client ID 和 Client Secret 填入下方或设置环境变量",
        ],
        "notes": "需要 Google 账号，免费配额足够个人使用。首次使用需配置 OAuth 同意屏幕。",
    },
    "bilibili": {
        "name": "Bilibili",
        "icon": "tv",
        "color": "#00A1D6",
        "group": "domestic",
        "auth_method": "OAuth + HMAC-SHA256 签名",
        "auth_type": "oauth2",
        "scopes": "视频稿件投递、用户信息",
        "dev_portal_url": "https://openhome.bilibili.com/doc",
        "dev_portal_name": "哔哩哔哩开放平台",
        "env_vars": ["OAUTH_BILIBILI_CLIENT_ID", "OAUTH_BILIBILI_CLIENT_SECRET"],
        "guide_steps": [
            "前往哔哩哔哩开放平台 (openhome.bilibili.com) 注册并入驻，完成实名认证",
            "在「开平管理中心」创建应用，获取 client_id（Access Key）和 app_secret（Access Key Secret）",
            "在应用设置中配置授权回调域：{callback_url}/api/oauth/callback/bilibili",
            "B站 API 使用 HMAC-SHA256 签名鉴权，请求头需包含 Access-Token、Authorization、X-Bili-Accesskeyid 等",
            "视频投稿接口：POST /arcopen/fn/archive/add-by-utoken（需先完成视频上传）",
        ],
        "notes": "B站开放平台使用签名鉴权（非标准 OAuth2 重定向），需用 client_id 和 app_secret 计算 HMAC-SHA256 签名。授权页新地址：account.bilibili.com/pc/account-pc/auth/oauth。非正式会员单日最多投递 5 个稿件。",
    },
    "tiktok": {
        "name": "TikTok",
        "icon": "music",
        "color": "#000000",
        "group": "international",
        "auth_method": "OAuth 2.0",
        "auth_type": "oauth2",
        "scopes": "user.info.basic, video.publish, video.upload",
        "dev_portal_url": "https://developers.tiktok.com/apps/",
        "dev_portal_name": "TikTok Developer Portal",
        "env_vars": ["OAUTH_TIKTOK_CLIENT_ID", "OAUTH_TIKTOK_CLIENT_SECRET"],
        "guide_steps": [
            "前往 TikTok Developer Portal (developers.tiktok.com) 注册开发者账号",
            "创建应用，在「Manage apps」中点击「Create」",
            "申请所需权限：Login Kit、Content Posting API",
            "在「Configuration」中添加回调 URL：{callback_url}/api/oauth/callback/tiktok",
            "获取 Client Key 和 Client Secret（注意：TikTok 使用 client_key 而非 client_id）",
        ],
        "notes": "TikTok 开发者账号需审核，Content Posting API 需单独申请权限。审核周期约 1-3 个工作日。",
    },
    "douyin": {
        "name": "抖音",
        "icon": "music-2",
        "color": "#000000",
        "group": "domestic",
        "auth_method": "OAuth 2.0",
        "auth_type": "oauth2",
        "scopes": "video.create.bind（代替用户发布内容到抖音）",
        "dev_portal_url": "https://developer.open-douyin.com/",
        "dev_portal_name": "抖音开放平台",
        "env_vars": ["OAUTH_DOUYIN_CLIENT_ID", "OAUTH_DOUYIN_CLIENT_SECRET"],
        "guide_steps": [
            "前往抖音开放平台 (developer.open-douyin.com) 注册开发者账号，需企业资质完成实名认证",
            "在控制台创建「网站应用」，获取 Client Key（即 client_key）和 Client Secret",
            "进入「能力管理」→「能力实验室」申请 scope：video.create.bind（代替用户发布内容到抖音）",
            "在应用详情中配置授权回调地址：{callback_url}/api/oauth/callback/douyin",
            "授权链接：https://open.douyin.com/platform/oauth/connect?client_key=YOUR_KEY&response_type=code&scope=video.create.bind&redirect_uri=YOUR_URI",
            "Token 接口：POST https://open.douyin.com/oauth/access_token/ （需 client_key + client_secret + code）",
        ],
        "notes": "抖音使用 client_key（非 client_id），系统已自动映射。视频发布流程：上传视频获取 video_id → 调用 /api/douyin/v1/video/create_video/ 发布。需企业资质，Content Posting 权限需单独申请审核。视频大小限制 4GB，超过 50MB 建议分片上传。",
    },
    "facebook": {
        "name": "Facebook",
        "icon": "facebook",
        "color": "#1877F2",
        "group": "international",
        "auth_method": "OAuth 2.0 (Meta)",
        "auth_type": "oauth2",
        "scopes": "pages_manage_posts, pages_read_engagement, publish_video",
        "dev_portal_url": "https://developers.facebook.com/apps/",
        "dev_portal_name": "Meta for Developers",
        "env_vars": ["OAUTH_FACEBOOK_APP_ID", "OAUTH_FACEBOOK_APP_SECRET"],
        "guide_steps": [
            "前往 Meta for Developers (developers.facebook.com) 登录",
            "在「My Apps」中创建应用，选择「Business」类型",
            "进入应用设置 →「基本」，获取 App ID 和 App Secret",
            "添加「Facebook Login」产品，设置有效的 OAuth 重定向 URI：{callback_url}/api/oauth/callback/facebook",
            "在「权限和功能」中申请 pages_manage_posts 和 publish_video 权限",
        ],
        "notes": "发布到 Facebook Page（非个人主页）。需要拥有 Facebook Page 并完成 App Review。",
    },
    "instagram": {
        "name": "Instagram",
        "icon": "instagram",
        "color": "#E4405F",
        "group": "international",
        "auth_method": "OAuth 2.0 (Meta)",
        "auth_type": "oauth2",
        "scopes": "instagram_basic, instagram_content_publish",
        "dev_portal_url": "https://developers.facebook.com/apps/",
        "dev_portal_name": "Meta for Developers",
        "env_vars": ["OAUTH_INSTAGRAM_APP_ID", "OAUTH_INSTAGRAM_APP_SECRET"],
        "guide_steps": [
            "使用与 Facebook 相同的 Meta 开发者平台，创建或复用现有应用",
            "添加「Instagram Basic Display」和「Instagram Graph API」产品",
            "确保 Instagram 账号已转换为 Business 或 Creator 账号，并关联到 Facebook Page",
            "在 OAuth 设置中添加回调 URI：{callback_url}/api/oauth/callback/instagram",
            "获取 App ID 和 App Secret（与 Facebook App 相同的凭据）",
        ],
        "notes": "必须是 Instagram Business/Creator 账号，且关联 Facebook Page。使用容器模式发布视频。",
    },
    "twitter": {
        "name": "X (Twitter)",
        "icon": "twitter",
        "color": "#000000",
        "group": "international",
        "auth_method": "OAuth 2.0 with PKCE",
        "auth_type": "oauth2",
        "scopes": "tweet.read, tweet.write, users.read, offline.access",
        "dev_portal_url": "https://developer.twitter.com/en/portal/dashboard",
        "dev_portal_name": "Twitter Developer Portal",
        "env_vars": ["OAUTH_TWITTER_CLIENT_ID", "OAUTH_TWITTER_CLIENT_SECRET"],
        "guide_steps": [
            "前往 Twitter Developer Portal (developer.twitter.com) 申请开发者账号",
            "在 Dashboard 中创建 Project 和 App",
            "进入 App 的「User authentication settings」，启用 OAuth 2.0",
            "设置回调 URL：{callback_url}/api/oauth/callback/twitter",
            "获取 Client ID 和 Client Secret（OAuth 2.0 凭据）",
        ],
        "notes": "Twitter 使用 OAuth 2.0 + PKCE 模式，系统已自动处理 PKCE 流程。Free 计划有发推限制。",
    },
    "pinterest": {
        "name": "Pinterest",
        "icon": "image",
        "color": "#BD081C",
        "group": "international",
        "auth_method": "OAuth 2.0",
        "auth_type": "oauth2",
        "scopes": "boards:read, pins:read, pins:write",
        "dev_portal_url": "https://developers.pinterest.com/apps/",
        "dev_portal_name": "Pinterest Developer",
        "env_vars": ["OAUTH_PINTEREST_CLIENT_ID", "OAUTH_PINTEREST_CLIENT_SECRET"],
        "guide_steps": [
            "前往 Pinterest Developer (developers.pinterest.com) 注册开发者账号",
            "在「My Apps」中创建应用",
            "在应用设置中配置回调 URL：{callback_url}/api/oauth/callback/pinterest",
            "获取 App ID 和 App Secret",
            "如需发布视频 Pin，需申请 pins:write 权限",
        ],
        "notes": "Pinterest 开发者权限需审核，视频 Pin 功能可能需要额外申请。",
    },
    "linkedin": {
        "name": "LinkedIn",
        "icon": "linkedin",
        "color": "#0A66C2",
        "group": "international",
        "auth_method": "OAuth 2.0",
        "auth_type": "oauth2",
        "scopes": "w_member_social, r_liteprofile",
        "dev_portal_url": "https://www.linkedin.com/developers/apps",
        "dev_portal_name": "LinkedIn Developer",
        "env_vars": ["OAUTH_LINKEDIN_CLIENT_ID", "OAUTH_LINKEDIN_CLIENT_SECRET"],
        "guide_steps": [
            "前往 LinkedIn Developer (linkedin.com/developers) 创建应用",
            "在「Auth」标签页中配置回调 URL：{callback_url}/api/oauth/callback/linkedin",
            "申请所需的 OAuth 2.0 权限范围（w_member_social 用于发布）",
            "获取 Client ID 和 Client Secret",
            "如需代表 Company Page 发布，还需申请 Marketing Developer Platform 权限",
        ],
        "notes": "个人发布使用 w_member_social，公司主页发布需额外 API 权限申请。",
    },
    "kwai": {
        "name": "快手",
        "icon": "video",
        "color": "#FF4906",
        "group": "domestic",
        "auth_method": "OAuth 2.0",
        "auth_type": "oauth2",
        "scopes": "user_info, user_video_publish",
        "dev_portal_url": "https://open.kuaishou.com/",
        "dev_portal_name": "快手开放平台",
        "env_vars": ["OAUTH_KWAI_CLIENT_ID", "OAUTH_KWAI_CLIENT_SECRET"],
        "guide_steps": [
            "前往快手开放平台 (open.kuaishou.com) 注册开发者账号，需完成「企业开发者认证」",
            "在「应用管理」中创建网站应用，获取 app_id 和 app_secret",
            "在应用详情页的「接口权限」中申请 scope：user_video_publish（视频发布）",
            "在应用中配置授权回调地址：{callback_url}/api/oauth/callback/kwai",
            "授权链接：https://open.kuaishou.com/oauth2/authorize?app_id=YOUR_ID&scope=user_info,user_video_publish&response_type=code&redirect_uri=YOUR_URI&ua=pc",
            "Token 接口：POST https://open.kuaishou.com/oauth2/access_token（需 app_id + app_secret + code）",
        ],
        "notes": "快手使用 app_id（非 client_id），系统已自动映射。视频发布三步流程：① POST /openapi/photo/start_upload 发起上传 → ② 上传视频文件（<10MB 直传，>10MB 分片） → ③ POST /openapi/photo/publish 发布视频。支持网页登录和手机扫码两种授权方式。",
    },
    "xiaohongshu": {
        "name": "小红书",
        "icon": "book-open",
        "color": "#FE2C55",
        "group": "domestic",
        "auth_method": "Cookie 登录",
        "auth_type": "cookie",
        "scopes": "笔记发布（无公开发布 API，通过 Cookie 操作）",
        "dev_portal_url": "https://open.xiaohongshu.com/",
        "dev_portal_name": "小红书开放平台（仅企业）",
        "env_vars": [],
        "guide_steps": [
            "小红书开放平台 (open.xiaohongshu.com) 仅对企业合作伙伴开放笔记发布 API，个人开发者无法申请",
            "因此采用 Cookie 登录方式：在浏览器中登录小红书网页版 (www.xiaohongshu.com)",
            "使用浏览器开发者工具（F12）→ Application → Cookies，复制所有 Cookie",
            "关键 Cookie 字段：web_session（登录凭证）、a1（设备标识）、xsecappid",
            "在下方「Cookie 登录」中粘贴 Cookie 完成绑定",
        ],
        "notes": "⚠️ Cookie 有效期约 24 小时（a1 字段），需定期更新。注意：使用 Playwright 等自动化工具操作小红书有封号风险，建议手动提取 Cookie 而非自动化模拟。小红书 v8.42+ 增加了滑块+签名双校验。",
    },
    "weixin_sph": {
        "name": "微信视频号",
        "icon": "message-circle",
        "color": "#07C160",
        "group": "domestic",
        "auth_method": "Cookie 登录",
        "auth_type": "cookie",
        "scopes": "视频发布（无公开发布 API，通过 Cookie 操作）",
        "dev_portal_url": "https://channels.weixin.qq.com/",
        "dev_portal_name": "微信视频号创作者中心",
        "env_vars": [],
        "guide_steps": [
            "微信视频号目前没有公开的视频发布 API（仅提供直播、搜索等只读接口）",
            "在浏览器中打开视频号创作者中心 (channels.weixin.qq.com)",
            "使用微信扫码登录后，通过开发者工具（F12）→ Application → Cookies 复制 Cookie",
            "或使用浏览器扩展（如 EditThisCookie）导出完整 Cookie JSON",
            "在下方「Cookie 登录」中粘贴 Cookie 完成绑定",
        ],
        "notes": "⚠️ Cookie 有效期极短（约 24 小时），需频繁更新。Playwright 自动化操作视频号有封号风险，建议手动提取 Cookie。微信生态对自动化检测较严格。",
    },
    "weixin_gzh": {
        "name": "微信公众号",
        "icon": "newspaper",
        "color": "#07C160",
        "group": "domestic",
        "auth_method": "Cookie 登录",
        "auth_type": "cookie",
        "scopes": "图文/视频发布（公众号 API 视频发布权限受限，通过 Cookie 操作）",
        "dev_portal_url": "https://mp.weixin.qq.com/",
        "dev_portal_name": "微信公众平台",
        "env_vars": [],
        "guide_steps": [
            "微信公众号有素材管理 API，但视频发布 API 仅限已认证服务号，且上传视频限制 30 分钟以内",
            "对于一般用户采用 Cookie 登录方式：在浏览器中登录微信公众平台 (mp.weixin.qq.com)",
            "使用开发者工具（F12）→ Application → Cookies 复制登录后的 Cookie",
            "或使用浏览器扩展导出 Cookie JSON",
            "在下方「Cookie 登录」中粘贴 Cookie 完成绑定",
        ],
        "notes": "⚠️ Cookie 有效期较短，需定期更新。Playwright 自动化操作有封号风险。如有已认证服务号且需 API 方式，可联系管理员配置 AppID/AppSecret（支持素材上传+群发 API）。",
    },
    "threads": {
        "name": "Threads",
        "icon": "at-sign",
        "color": "#000000",
        "group": "international",
        "auth_method": "OAuth 2.0 (Meta)",
        "auth_type": "oauth2",
        "scopes": "threads_basic, threads_content_publish",
        "dev_portal_url": "https://developers.facebook.com/apps/",
        "dev_portal_name": "Meta for Developers",
        "env_vars": ["OAUTH_THREADS_APP_ID", "OAUTH_THREADS_APP_SECRET"],
        "guide_steps": [
            "使用 Meta 开发者平台，创建或复用现有 Facebook 应用",
            "在应用中添加「Threads API」产品",
            "配置 OAuth 回调 URI：{callback_url}/api/oauth/callback/threads",
            "获取 App ID 和 App Secret",
            "申请 threads_basic 和 threads_content_publish 权限",
        ],
        "notes": "Threads API 相对较新（2024 年开放），使用 Meta 统一认证体系。需通过 App Review。",
    },
}

_MASK = "****"


class OAuthSettingsRequest(BaseModel):
    callback_base_url: str = ""
    platforms: dict[str, dict[str, str]] = Field(default_factory=dict)


@router.get("/settings/oauth", dependencies=[Depends(require_auth)])
async def get_oauth_settings():
    """读取 OAuth 配置（密钥脱敏），同时返回平台配置引导元数据。"""
    from api.server import _env_oauth

    config_data = _read_yaml_config()
    oauth: dict = config_data.get("oauth") or {}

    env_callback = os.environ.get("OAUTH_CALLBACK_BASE_URL", "")
    yaml_callback = oauth.get("callback_base_url", "http://localhost:9000")
    callback_base_url = env_callback or yaml_callback

    platforms: dict[str, dict] = {}
    for plat, (id_key, sec_key) in _OAUTH_PLATFORM_FIELDS.items():
        # 检查环境变量
        env_id, env_sec = _env_oauth(plat, id_key, sec_key)
        from_env = bool(env_id and env_sec)

        if from_env:
            # 环境变量已配置，显示脱敏值
            display_id = _MASK + env_id[-4:] if len(env_id) > 4 else _MASK
            display_sec = _MASK + env_sec[-4:] if len(env_sec) > 4 else _MASK
        else:
            # 回退到 settings.yaml
            plat_cfg = oauth.get(plat) or {}
            raw_id = plat_cfg.get(id_key, "")
            raw_sec = plat_cfg.get(sec_key, "")
            display_id = (_MASK + raw_id[-4:]) if raw_id else ""
            display_sec = (_MASK + raw_sec[-4:]) if raw_sec else ""

        # 合并字段信息与配置引导
        guide = _OAUTH_PLATFORM_GUIDES.get(plat, {})
        platforms[plat] = {
            "id_field": id_key,
            "secret_field": sec_key,
            "id_value": display_id,
            "secret_value": display_sec,
            "from_env": from_env,
            # 配置引导元数据
            "name": guide.get("name", plat),
            "icon": guide.get("icon", ""),
            "color": guide.get("color", "#888"),
            "group": guide.get("group", "international"),
            "auth_method": guide.get("auth_method", "OAuth 2.0"),
            "auth_type": guide.get("auth_type", "oauth2"),
            "scopes": guide.get("scopes", ""),
            "dev_portal_url": guide.get("dev_portal_url", ""),
            "dev_portal_name": guide.get("dev_portal_name", ""),
            "env_vars": guide.get("env_vars", []),
            "guide_steps": [
                s.replace("{callback_url}", callback_base_url)
                for s in guide.get("guide_steps", [])
            ],
            "notes": guide.get("notes", ""),
        }

    return {
        "success": True,
        "callback_base_url": callback_base_url,
        "callback_from_env": bool(env_callback),
        "platforms": platforms,
    }


@router.post("/settings/oauth", dependencies=[Depends(require_auth)])
async def set_oauth_settings(request: OAuthSettingsRequest):
    """保存 OAuth 配置并动态重新注册平台服务。"""
    config_data = _read_yaml_config()
    oauth: dict = config_data.get("oauth") or {}

    if request.callback_base_url.strip():
        oauth["callback_base_url"] = request.callback_base_url.strip().rstrip("/")

    for plat, new_vals in request.platforms.items():
        if plat not in _OAUTH_PLATFORM_FIELDS:
            continue
        id_key, sec_key = _OAUTH_PLATFORM_FIELDS[plat]
        existing = oauth.get(plat) or {}

        new_id = new_vals.get("id_value", "")
        new_sec = new_vals.get("secret_value", "")

        # 空字符串或以 **** 开头 → 保留原值
        if not new_id or new_id.startswith(_MASK):
            new_id = existing.get(id_key, "")
        if not new_sec or new_sec.startswith(_MASK):
            new_sec = existing.get(sec_key, "")

        oauth[plat] = {id_key: new_id, sec_key: new_sec}

    config_data["oauth"] = oauth
    _write_yaml_config(config_data)
    Config.reset()

    # 动态重新注册平台服务（热加载）
    from api.server import register_platform_services
    registered = register_platform_services()

    return {
        "success": True,
        "message": f"OAuth 配置已保存，{registered} 个平台已激活",
        "registered_count": registered,
    }
