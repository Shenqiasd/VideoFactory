"""
全局配置管理
"""
import os
import yaml
from pathlib import Path
from typing import Optional


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
        if config_path is None:
            config_path = os.environ.get(
                "VF_CONFIG",
                str(Path(__file__).parent.parent.parent / "config" / "settings.yaml")
            )

        with open(config_path, "r") as f:
            self._data = yaml.safe_load(f)

    def get(self, *keys, default=None):
        """
        嵌套获取配置值

        用法: config.get("storage", "r2", "bucket")
        """
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
