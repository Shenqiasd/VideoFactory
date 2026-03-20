"""
全局配置管理
支持 YAML 配置文件 + 环境变量覆盖
"""
import os
import logging
import yaml
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 可通过环境变量覆盖的密钥字段
_ENV_OVERRIDES = {
    ("llm", "api_key"): "LLM_API_KEY",
    ("llm", "base_url"): "LLM_BASE_URL",
    ("llm", "model"): "LLM_MODEL",
    ("translation", "volcengine_ark", "api_key"): "VOLCENGINE_ARK_API_KEY",
    ("translation", "volcengine_ark", "base_url"): "VOLCENGINE_ARK_BASE_URL",
    ("translation", "volcengine_ark", "model"): "VOLCENGINE_ARK_MODEL",
    ("tts", "volcengine", "appid"): "VOLCENGINE_TTS_APPID",
    ("tts", "volcengine", "access_token"): "VOLCENGINE_TTS_ACCESS_TOKEN",
    ("asr", "volcengine", "app_id"): "VOLCENGINE_ASR_APPID",
    ("asr", "volcengine", "token"): "VOLCENGINE_ASR_TOKEN",
    ("storage", "r2", "bucket"): "R2_BUCKET",
    ("storage", "r2", "rclone_remote"): "R2_RCLONE_REMOTE",
}


class Config:
    """全局配置单例"""

    _instance: Optional["Config"] = None
    _data: dict = {}

    def __new__(cls, config_path: str = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load(config_path)
        return cls._instance

    def _load(self, config_path: str = None):
        # 加载 .env 文件
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        if config_path is None:
            config_path = os.environ.get(
                "VF_CONFIG",
                str(Path(__file__).parent.parent.parent / "config" / "settings.yaml")
            )

        try:
            with open(config_path, "r") as f:
                self._data = yaml.safe_load(f) or {}
        except FileNotFoundError:
            logger.warning("配置文件未找到: %s，使用空配置", config_path)
            self._data = {}

        self._apply_env_overrides()
        self._validate()

    def _apply_env_overrides(self):
        """用环境变量覆盖 YAML 配置中的密钥"""
        for yaml_path, env_var in _ENV_OVERRIDES.items():
            val = os.environ.get(env_var)
            if val:
                self._set_nested(yaml_path, val)

    def _set_nested(self, keys: tuple, value):
        """设置嵌套字典中的值"""
        d = self._data
        for key in keys[:-1]:
            if key not in d or not isinstance(d[key], dict):
                d[key] = {}
            d = d[key]
        d[keys[-1]] = value

    def _validate(self):
        """启动时验证关键配置，缺失时记录警告"""
        placeholders = {"YOUR_API_KEY_HERE", "your_api_key_here", "YOUR_API_KEY", ""}
        critical = [("llm", "api_key")]
        for key_path in critical:
            val = self.get(*key_path)
            if val is None or val in placeholders:
                env_var = _ENV_OVERRIDES.get(key_path, "N/A")
                logger.warning(
                    "配置缺失或为占位符: %s (可设置环境变量: %s)",
                    ".".join(key_path), env_var,
                )

    def get(self, *keys, default=None):
        """嵌套获取配置值"""
        d = self._data
        for key in keys:
            if isinstance(d, dict):
                d = d.get(key)
            else:
                return default
            if d is None:
                return default
        return d

    @classmethod
    def reset(cls):
        """重置单例（测试用）"""
        cls._instance = None
        cls._data = {}
