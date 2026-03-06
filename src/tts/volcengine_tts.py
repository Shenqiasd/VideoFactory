"""
火山引擎 TTS Provider（HTTP API）。

兼容两类配置：
1. 新格式（推荐）: appid/access_token/resource_id/api_url/default_voice
2. 旧格式（向后兼容）: app_id/token/synthesis_url/voice_id
"""
from __future__ import annotations

import base64
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from core.config import Config

from .base import BaseTTSProvider, TTSResult

logger = logging.getLogger(__name__)


DEFAULT_VOLCENGINE_VOICES = [
    {"id": "zh_female_shuangkuaisisi_moon_bigtts", "name": "双快思思(女)", "language": "zh-CN"},
    {"id": "zh_female_cancan_mars_bigtts", "name": "灿灿(女)", "language": "zh-CN"},
    {"id": "zh_male_ahu_conversation_wvae_bigtts", "name": "阿虎(男)", "language": "zh-CN"},
    # 兼容历史配置音色（部分资源ID下可能不可用）
    {"id": "BV001_streaming", "name": "通用女声(BV)", "language": "zh-CN"},
    {"id": "BV002_streaming", "name": "通用男声(BV)", "language": "zh-CN"},
    {"id": "BV700_streaming", "name": "知性女声(BV)", "language": "zh-CN"},
]


class VolcengineTTS(BaseTTSProvider):
    """火山引擎 TTS。"""

    name = "volcengine"

    def __init__(self, config: Optional[Config] = None):
        cfg = config or Config()
        tts_cfg = cfg.get("tts", "volcengine", default={}) or {}

        self.enabled = bool(tts_cfg.get("enabled", False))
        self.appid = str(tts_cfg.get("appid") or tts_cfg.get("app_id") or "").strip()
        self.access_token = str(tts_cfg.get("access_token") or tts_cfg.get("token") or "").strip()
        self.cluster = str(tts_cfg.get("cluster", "volcano_tts")).strip() or "volcano_tts"
        self.resource_id = str(tts_cfg.get("resource_id", "seed-tts-1.0")).strip() or "seed-tts-1.0"
        self.api_url = str(
            tts_cfg.get("api_url")
            or tts_cfg.get("synthesis_url")
            or "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
        ).strip()
        self.default_voice = str(
            tts_cfg.get("default_voice")
            or tts_cfg.get("voice_type")
            or tts_cfg.get("voice_id")
            or "zh_female_shuangkuaisisi_moon_bigtts"
        ).strip() or "zh_female_shuangkuaisisi_moon_bigtts"
        self.encoding = str(tts_cfg.get("encoding", "mp3")).strip() or "mp3"
        self.timeout = int(tts_cfg.get("timeout", 120))
        self.user_uid = str(tts_cfg.get("user_uid", "video-factory")).strip() or "video-factory"
        self.speed_ratio = float(tts_cfg.get("speed_ratio", 1.0))
        self.volume_ratio = float(tts_cfg.get("volume_ratio", 1.0))
        self.pitch_ratio = float(tts_cfg.get("pitch_ratio", 1.0))
        self.sample_rate = int(tts_cfg.get("sample_rate", 24000))
        self.available_voices = self._normalize_voices(tts_cfg.get("available_voices"))
        self.last_error = ""

        if self.sample_rate <= 0:
            self.sample_rate = 24000

    @staticmethod
    def _normalize_voices(raw: Any) -> List[Dict[str, str]]:
        if not isinstance(raw, list) or not raw:
            return list(DEFAULT_VOLCENGINE_VOICES)

        normalized: List[Dict[str, str]] = []
        seen = set()
        for item in raw:
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

    def get_available_voices(self) -> List[Dict[str, str]]:
        """返回可用音色列表。"""
        return list(self.available_voices)

    def _set_error(self, message: str):
        self.last_error = message

    def _make_headers(self, *, semicolon_bearer: bool = True) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.access_token:
            bearer_value = f"Bearer;{self.access_token}" if semicolon_bearer else f"Bearer {self.access_token}"
            headers["Authorization"] = bearer_value
        return headers

    def _build_payload_v1(
        self,
        *,
        text: str,
        voice_type: str,
    ) -> Dict[str, Any]:
        return {
            "app": {
                "appid": self.appid,
                "token": self.access_token,
                "cluster": self.cluster,
            },
            "user": {"uid": self.user_uid},
            "audio": {
                "voice_type": voice_type,
                "encoding": self.encoding,
                "speed_ratio": self.speed_ratio,
                "volume_ratio": self.volume_ratio,
                "pitch_ratio": self.pitch_ratio,
            },
            "request": {
                "reqid": str(uuid.uuid4()),
                "text": text,
                "text_type": "plain",
                "operation": "query",
            },
        }

    @staticmethod
    def _ratio_to_rate(ratio: float) -> int:
        """
        将旧版 ratio(0.5~2.0) 映射到 v3 的 rate(-50~100)。
        1.0 -> 0, 0.5 -> -50, 2.0 -> 100。
        """
        try:
            value = int(round((float(ratio) - 1.0) * 100))
        except Exception:
            value = 0
        return max(-50, min(100, value))

    def _build_payload_v3(
        self,
        *,
        text: str,
        voice_type: str,
    ) -> Dict[str, Any]:
        encoding = str(self.encoding or "mp3").strip().lower()
        if encoding not in {"mp3", "ogg_opus", "pcm"}:
            encoding = "mp3"

        return {
            "user": {"uid": self.user_uid},
            "req_params": {
                "text": text,
                "speaker": voice_type,
                "audio_params": {
                    "format": encoding,
                    "sample_rate": int(self.sample_rate),
                    "speech_rate": self._ratio_to_rate(self.speed_ratio),
                    "loudness_rate": self._ratio_to_rate(self.volume_ratio),
                },
            },
        }

    @staticmethod
    def _extract_json_error(payload: Dict[str, Any]) -> str:
        code = payload.get("code", payload.get("status_code"))
        message = payload.get("message") or payload.get("msg") or payload.get("detail") or ""
        if code is None and not message:
            return ""
        return f"code={code}, message={message}".strip(", ")

    @staticmethod
    def _decode_audio_from_json(payload: Dict[str, Any]) -> bytes:
        data = payload.get("data")
        if isinstance(data, str) and data:
            return base64.b64decode(data)
        if isinstance(data, dict):
            for key in ("audio", "audio_base64", "data"):
                val = data.get(key)
                if isinstance(val, str) and val:
                    return base64.b64decode(val)
        for key in ("audio", "audio_base64"):
            val = payload.get(key)
            if isinstance(val, str) and val:
                return base64.b64decode(val)
        return b""

    @staticmethod
    def _is_v3_url(url: str) -> bool:
        u = (url or "").lower()
        return "/api/v3/tts/" in u

    @staticmethod
    def _is_v1_url(url: str) -> bool:
        u = (url or "").lower()
        return u.endswith("/api/v1/tts") or "/api/v1/tts?" in u

    @staticmethod
    def _should_fallback_to_v3(status_code: int, error_text: str) -> bool:
        if status_code in (401, 403):
            return True
        normalized = (error_text or "").lower()
        return "invalid auth token" in normalized or "authentication signature" in normalized

    @staticmethod
    def _humanize_error(error_text: str) -> str:
        normalized = (error_text or "").lower()
        if "resource id is mismatched with speaker related resource" in normalized:
            return (
                f"{error_text}。请在设置页将 `resource_id` 与音色匹配，"
                "可尝试 `seed-tts-1.0` / `seed-tts-1.0-concurr` / `seed-tts-2.0`，"
                "或切换到与当前 resource_id 对应的音色。"
            )
        if "quota exceeded" in normalized:
            return f"{error_text}。火山侧并发/配额不足，请检查资源包或降低并发。"
        return error_text

    async def _request_v1(
        self,
        *,
        text: str,
        voice_type: str,
        api_url: str,
    ) -> Tuple[bytes, str, int]:
        payload = self._build_payload_v1(text=text, voice_type=voice_type)
        resp: Optional[httpx.Response] = None

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    api_url,
                    headers=self._make_headers(semicolon_bearer=True),
                    json=payload,
                )
                # 某些网关仅接受标准 Bearer 空格格式
                if resp.status_code in (401, 403):
                    resp = await client.post(
                        api_url,
                        headers=self._make_headers(semicolon_bearer=False),
                        json=payload,
                    )
        except Exception as exc:
            return b"", f"v1 请求异常: {exc}", -1

        if resp.status_code != 200:
            return b"", f"v1 HTTP {resp.status_code}: {resp.text[:240]}", resp.status_code

        content_type = (resp.headers.get("content-type") or "").lower()
        if "text/plain" in content_type:
            raw = (resp.text or "").strip()
            if raw.startswith("{"):
                try:
                    body = json.loads(raw)
                    if isinstance(body, dict):
                        err = self._extract_json_error(body)
                        if err:
                            return b"", f"v1 业务错误: {err}", resp.status_code
                except Exception:
                    pass
        if "application/json" in content_type:
            try:
                body = resp.json()
            except Exception:
                return b"", "v1 JSON 解析失败", resp.status_code

            code = body.get("code")
            if isinstance(code, int) and code not in (0, 200, 3000):
                return b"", f"v1 业务错误: {self._extract_json_error(body)}", resp.status_code

            audio_bytes = self._decode_audio_from_json(body)
            if not audio_bytes:
                return b"", "v1 返回 JSON 但未包含音频", resp.status_code
            return audio_bytes, "", resp.status_code

        audio_bytes = resp.content or b""
        if not audio_bytes:
            return b"", "v1 未返回有效音频", resp.status_code
        return audio_bytes, "", resp.status_code

    async def _request_v3_http(
        self,
        *,
        text: str,
        voice_type: str,
        api_url: str,
    ) -> Tuple[bytes, str, int]:
        payload = self._build_payload_v3(text=text, voice_type=voice_type)
        headers = {
            "Content-Type": "application/json",
            "X-Api-App-Id": self.appid,
            "X-Api-Access-Key": self.access_token,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(api_url, headers=headers, json=payload)
        except Exception as exc:
            return b"", f"v3 请求异常: {exc}", -1

        if resp.status_code != 200:
            return b"", f"v3 HTTP {resp.status_code}: {resp.text[:240]}", resp.status_code

        content_type = (resp.headers.get("content-type") or "").lower()
        if "text/plain" in content_type:
            raw = (resp.text or "").strip()
            if raw.startswith("{"):
                try:
                    body = json.loads(raw)
                    if isinstance(body, dict):
                        status_code = body.get("status_code", body.get("code"))
                        if status_code not in (None, 0, 200, 20000000):
                            return b"", f"v3 业务错误: {self._extract_json_error(body)}", resp.status_code
                        audio_bytes = self._decode_audio_from_json(body)
                        if audio_bytes:
                            return audio_bytes, "", resp.status_code
                        err = self._extract_json_error(body)
                        if err:
                            return b"", f"v3 业务错误: {err}", resp.status_code
                except Exception:
                    pass

        if "application/json" in content_type:
            try:
                body = resp.json()
            except Exception:
                return b"", "v3 JSON 解析失败", resp.status_code

            status_code = body.get("status_code", body.get("code"))
            if status_code not in (None, 0, 200, 20000000):
                return b"", f"v3 业务错误: {self._extract_json_error(body)}", resp.status_code

            audio_bytes = self._decode_audio_from_json(body)
            if not audio_bytes:
                return b"", "v3 返回 JSON 但未包含音频", resp.status_code
            return audio_bytes, "", resp.status_code

        audio_bytes = resp.content or b""
        if not audio_bytes:
            return b"", "v3 未返回有效音频", resp.status_code
        return audio_bytes, "", resp.status_code

    async def synthesize(
        self,
        *,
        text: str,
        output_path: str,
        source_audio_path: Optional[str] = None,
        language: str = "",
        voice_type: Optional[str] = None,
    ) -> Optional[TTSResult]:
        """
        调用火山引擎 TTS 合成音频。

        Args:
            text: 合成文本。
            output_path: 输出音频路径。
            source_audio_path: 保留兼容参数，当前未使用。
            language: 保留兼容参数，当前未使用。
            voice_type: 音色 ID，未传则用配置默认值。

        Returns:
            Optional[TTSResult]: 成功返回结果，失败返回 None。
        """
        _ = source_audio_path
        _ = language

        self._set_error("")
        if not self.enabled:
            self._set_error("火山 TTS 未启用")
            return None
        if not self.appid or not self.access_token or not self.api_url:
            msg = "Volcengine TTS 未配置 appid/access_token/api_url"
            logger.warning(msg)
            self._set_error(msg)
            return None
        content = (text or "").strip()
        if not content:
            self._set_error("合成文本为空")
            return None

        selected_voice = str(
            voice_type or self.default_voice or "zh_female_shuangkuaisisi_moon_bigtts"
        ).strip()
        audio_bytes = b""
        error_text = ""
        status_code = -1
        used_url = self.api_url

        if self._is_v3_url(self.api_url):
            audio_bytes, error_text, status_code = await self._request_v3_http(
                text=content,
                voice_type=selected_voice,
                api_url=self.api_url,
            )
        else:
            audio_bytes, error_text, status_code = await self._request_v1(
                text=content,
                voice_type=selected_voice,
                api_url=self.api_url,
            )
            # 对 v1 鉴权失败自动切换 v3，兼容新版 Access Token
            if not audio_bytes and self._is_v1_url(self.api_url) and self._should_fallback_to_v3(status_code, error_text):
                fallback_url = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
                audio_bytes, error_text_v3, _ = await self._request_v3_http(
                    text=content,
                    voice_type=selected_voice,
                    api_url=fallback_url,
                )
                if audio_bytes:
                    used_url = fallback_url
                elif error_text_v3:
                    error_text = f"{error_text}; v3 fallback failed: {error_text_v3}"

        if not audio_bytes:
            if not error_text:
                error_text = "Volcengine TTS 未返回有效音频"
            error_text = self._humanize_error(error_text)
            logger.warning("Volcengine TTS 合成失败: %s", error_text)
            self._set_error(error_text)
            return None

        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(audio_bytes)
        if not out_path.exists() or out_path.stat().st_size < 128:
            self._set_error("音频文件生成失败或体积过小")
            return None

        return TTSResult(
            audio_path=str(out_path),
            provider=self.name,
            metadata={
                "voice_type": selected_voice,
                "encoding": self.encoding,
                "cluster": self.cluster,
                "resource_id": self.resource_id,
                "api_url": used_url,
            },
        )
