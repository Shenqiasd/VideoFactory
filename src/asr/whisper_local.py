"""
本地 Whisper Proxy ASR Provider。
"""
from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import Optional

import httpx

from core.config import Config

from .base import ASRResult, BaseASRProvider

logger = logging.getLogger(__name__)


class WhisperLocalASR(BaseASRProvider):
    """
    调用本地 OpenAI 兼容 Whisper Proxy（默认 8866）。
    """

    name = "whisper"

    def __init__(self, config: Optional[Config] = None):
        cfg = config or Config()
        asr_cfg = cfg.get("asr", "whisper", default={}) or {}
        self.base_url = str(asr_cfg.get("base_url", "http://127.0.0.1:8866/v1")).rstrip("/")
        self.model = str(asr_cfg.get("model", "base"))
        self.timeout = int(asr_cfg.get("timeout", 600))
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
        tmp_dir = Path(tempfile.mkdtemp(prefix="vf_asr_whisper_"))
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

    async def _call_whisper_proxy(self, audio_path: str, source_lang: str) -> str:
        url = f"{self.base_url}/audio/transcriptions"
        timeout = httpx.Timeout(self.timeout)

        with open(audio_path, "rb") as f:
            files = {"file": (Path(audio_path).name, f, "audio/wav")}
            data = {
                "model": self.model,
                "language": source_lang,
                "response_format": "srt",
            }
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, files=files, data=data)

        if resp.status_code != 200:
            raise RuntimeError(f"Whisper proxy 返回异常: HTTP {resp.status_code} {resp.text[:200]}")

        content_type = (resp.headers.get("content-type") or "").lower()
        body = resp.text
        if "-->" in body and "\n1\n" not in body[:20]:
            # 部分实现可能没有编号，补简单编号
            lines = body.strip().splitlines()
            if lines and "-->" in lines[0]:
                return "1\n" + body.strip() + "\n"
        if "-->" in body:
            return body

        # 兼容代理返回 JSON {"text":"..."} 的情况
        if "application/json" in content_type or body.strip().startswith("{"):
            try:
                payload = json.loads(body)
                text = str(payload.get("text", "")).strip()
                if text:
                    return f"1\n00:00:00,000 --> 00:59:59,000\n{text}\n"
            except Exception:
                pass
        raise RuntimeError("Whisper proxy 未返回有效 SRT")

    async def transcribe(
        self,
        *,
        video_url: str,
        video_path: Optional[str],
        source_lang: str,
    ) -> Optional[ASRResult]:
        """
        使用本地 Whisper Proxy 生成 SRT。

        Args:
            video_url: 视频 URL 或本地路径字符串。
            video_path: 已下载的本地视频路径。
            source_lang: 源语言。

        Returns:
            Optional[ASRResult]: 成功返回结果，否则返回 None。
        """
        input_path = self._resolve_input_path(video_url, video_path)
        if not input_path:
            logger.info("WhisperLocalASR 缺少可用本地视频路径")
            return None

        audio_path = ""
        try:
            audio_path = await self._extract_audio(input_path)
            srt_content = await self._call_whisper_proxy(audio_path, source_lang)
            if not srt_content.strip():
                return None
            logger.info("✅ Whisper 本地转写成功: %s", input_path)
            return ASRResult(
                srt_content=srt_content,
                method=self.name,
                source_lang=source_lang,
                metadata={"video_path": input_path},
            )
        except Exception as exc:
            logger.warning("Whisper 本地转写失败: %s", exc)
            return None
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
