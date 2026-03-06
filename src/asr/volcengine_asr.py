"""
火山引擎 ASR Provider（SeedASR 2.0）。

说明：
- 采用“配置驱动 + 失败可降级”策略。
- 优先尝试 HTTP 网关（若配置了 http_url），再尝试 WebSocket。
- 任一路径失败都会返回 None，由 ASRRouter 继续降级。
"""
from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from core.config import Config

from .base import ASRResult, BaseASRProvider

logger = logging.getLogger(__name__)


class VolcengineASR(BaseASRProvider):
    """火山引擎 ASR。"""

    name = "volcengine"

    def __init__(self, config: Optional[Config] = None):
        cfg = config or Config()
        volc_cfg = cfg.get("asr", "volcengine", default={}) or {}
        self.enabled = bool(volc_cfg.get("enabled", False))
        self.app_id = str(volc_cfg.get("app_id", "")).strip()
        self.token = str(volc_cfg.get("token", "")).strip()
        self.http_url = str(volc_cfg.get("http_url", "")).strip()
        self.ws_url = str(
            volc_cfg.get("ws_url", "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel")
        ).strip()
        self.timeout = int(volc_cfg.get("timeout", 120))
        self.ffmpeg = str(cfg.get("ffmpeg", "path", default="ffmpeg"))

    @staticmethod
    def _is_remote_url(path_or_url: str) -> bool:
        return path_or_url.startswith(("http://", "https://"))

    def _resolve_input_path(self, video_url: str, video_path: Optional[str]) -> Optional[str]:
        if video_path and Path(video_path).exists():
            return video_path
        if video_url and (not self._is_remote_url(video_url)) and Path(video_url).exists():
            return video_url
        return None

    async def _extract_audio(self, video_path: str) -> str:
        tmp_dir = Path(tempfile.mkdtemp(prefix="vf_asr_volc_"))
        audio_path = tmp_dir / "audio.wav"
        cmd = [
            self.ffmpeg,
            "-i",
            video_path,
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-y",
            str(audio_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not audio_path.exists() or audio_path.stat().st_size < 128:
            err = stderr.decode(errors="ignore")[-300:]
            raise RuntimeError(f"FFmpeg 音频分离失败: {err}")
        return str(audio_path)

    def _auth_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _transcribe_http(self, audio_path: str, source_lang: str) -> Optional[str]:
        if not self.http_url:
            return None

        with open(audio_path, "rb") as f:
            files = {"audio": (Path(audio_path).name, f, "audio/wav")}
            data = {"app_id": self.app_id, "language": source_lang, "response_format": "srt"}
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self.http_url, headers=self._auth_headers(), files=files, data=data)

        if resp.status_code != 200:
            raise RuntimeError(f"Volcengine ASR HTTP 返回异常: {resp.status_code} {resp.text[:200]}")

        if "-->" in resp.text:
            return resp.text

        try:
            payload = resp.json()
        except Exception as exc:
            raise RuntimeError(f"Volcengine ASR HTTP 解析失败: {exc}") from exc

        srt = str(payload.get("srt", "")).strip()
        if srt:
            return srt

        segments = payload.get("segments") or payload.get("result") or []
        if isinstance(segments, list) and segments:
            return self._segments_to_srt(segments)
        return None

    @staticmethod
    def _segments_to_srt(segments: list[Dict[str, Any]]) -> str:
        blocks = []
        for i, seg in enumerate(segments, start=1):
            start = float(seg.get("start", 0.0) or 0.0)
            end = float(seg.get("end", start + 1.0) or (start + 1.0))
            text = str(seg.get("text", "")).strip()
            if not text:
                continue
            blocks.append(
                f"{i}\n"
                f"{_format_srt_time(start)} --> {_format_srt_time(end)}\n"
                f"{text}\n"
            )
        return "\n".join(blocks).strip() + ("\n" if blocks else "")

    async def _transcribe_websocket(self, audio_path: str, source_lang: str) -> Optional[str]:
        if not self.ws_url:
            return None

        try:
            import websockets
        except Exception as exc:  # pragma: no cover - 依赖未安装环境
            raise RuntimeError("websockets 未安装，无法启用 Volcengine WebSocket ASR") from exc

        # 说明：官方协议较复杂，当前实现为兼容型基础握手，若网关不兼容会自动回退。
        request_id = f"vf_{int(asyncio.get_event_loop().time() * 1000)}"
        start_payload = {
            "type": "start",
            "request_id": request_id,
            "app_id": self.app_id,
            "language": source_lang,
            "format": "wav",
        }
        end_payload = {"type": "end", "request_id": request_id}

        results: list[str] = []
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        async with websockets.connect(
            self.ws_url,
            extra_headers=headers,
            open_timeout=self.timeout,
            close_timeout=5,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            await ws.send(json.dumps(start_payload, ensure_ascii=False))

            with open(audio_path, "rb") as f:
                while True:
                    chunk = f.read(32_000)
                    if not chunk:
                        break
                    await ws.send(chunk)

            await ws.send(json.dumps(end_payload, ensure_ascii=False))

            while True:
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=1.5)
                except asyncio.TimeoutError:
                    break
                except Exception:
                    break

                if isinstance(message, bytes):
                    continue

                try:
                    payload = json.loads(message)
                except Exception:
                    continue
                text = str(
                    payload.get("text")
                    or payload.get("result")
                    or payload.get("transcript")
                    or ""
                ).strip()
                if text:
                    results.append(text)

                if str(payload.get("type", "")).lower() in {"end", "finish", "final"}:
                    break

        if not results:
            return None

        merged = "\n".join(results).strip()
        if "-->" in merged:
            return merged
        return f"1\n00:00:00,000 --> 00:59:59,000\n{merged}\n"

    async def transcribe(
        self,
        *,
        video_url: str,
        video_path: Optional[str],
        source_lang: str,
    ) -> Optional[ASRResult]:
        """
        使用火山引擎 ASR 转写为 SRT。

        Returns:
            Optional[ASRResult]: 成功返回结果，失败返回 None（供上层降级）。
        """
        if not self.enabled:
            return None
        if not self.app_id or not self.token:
            logger.warning("Volcengine ASR 未配置 app_id/token，跳过")
            return None

        input_path = self._resolve_input_path(video_url, video_path)
        if not input_path:
            logger.info("Volcengine ASR 缺少可用本地视频路径")
            return None

        audio_path = ""
        try:
            audio_path = await self._extract_audio(input_path)
            srt_content = None

            if self.http_url:
                try:
                    srt_content = await self._transcribe_http(audio_path, source_lang)
                except Exception as exc:
                    logger.warning("Volcengine ASR HTTP 失败: %s", exc)

            if not srt_content:
                try:
                    srt_content = await self._transcribe_websocket(audio_path, source_lang)
                except Exception as exc:
                    logger.warning("Volcengine ASR WebSocket 失败: %s", exc)

            if not srt_content or not srt_content.strip():
                return None

            logger.info("✅ Volcengine ASR 转写成功: %s", input_path)
            return ASRResult(
                srt_content=srt_content,
                method=self.name,
                source_lang=source_lang,
                metadata={"video_path": input_path},
            )
        finally:
            if audio_path:
                p = Path(audio_path)
                try:
                    if p.exists():
                        p.unlink()
                    if p.parent.exists():
                        p.parent.rmdir()
                except Exception:
                    pass


def _format_srt_time(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    secs = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
