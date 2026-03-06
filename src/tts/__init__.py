"""
TTS Providers 导出。
"""

from .base import BaseTTSProvider, TTSResult
from .volcengine_tts import VolcengineTTS

__all__ = [
    "BaseTTSProvider",
    "TTSResult",
    "VolcengineTTS",
]

