"""
发布适配器 - 封装 social-auto-upload 调用逻辑
支持: 抖音、B站、小红书、YouTube、视频号
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
from typing import Optional, Dict, Any

import httpx

logger = logging.getLogger(__name__)


PLATFORM_SCRIPT_MAP = {
    "douyin": "upload_video_to_douyin.py",
    "bilibili": "upload_video_to_bilibili.py",
    "xiaohongshu": "upload_video_to_xhs.py",
    "youtube": "upload_video_to_youtube.py",
    "weixin": "upload_video_to_tencent.py",
}


class SocialAutoUploadAdapter:
    """social-auto-upload 发布适配层基类"""

    async def publish(self, platform: str, payload: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        raise NotImplementedError


class LocalSocialAutoUploadAdapter(SocialAutoUploadAdapter):
    """本地执行 social-auto-upload 脚本"""

    def __init__(self, sau_path: str):
        self.sau_path = sau_path

    async def publish(self, platform: str, payload: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        if not os.path.exists(self.sau_path):
            return {"success": False, "url": "", "error": f"social-auto-upload未安装: {self.sau_path}"}

        script = PLATFORM_SCRIPT_MAP.get(platform)
        if not script:
            return {"success": False, "url": "", "error": f"不支持的平台: {platform}"}

        video_path = payload.get("video_path", "")
        if not os.path.exists(video_path):
            return {"success": False, "url": "", "error": f"视频文件不存在: {video_path}"}

        script_path = os.path.join(self.sau_path, script)
        if not os.path.exists(script_path):
            return {"success": False, "url": "", "error": f"发布脚本不存在: {script_path}"}

        config_file = f"/tmp/vf_publish_{platform}_{os.getpid()}.json"
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

        try:
            cmd = ["python3", script_path, "--config", config_file]
            logger.info("📤 本地发布: platform=%s title=%s", platform, payload.get("title", ""))

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.sau_path,
            )

            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
            stdout_text = stdout.decode(errors="ignore")
            stderr_text = stderr.decode(errors="ignore")

            if process.returncode == 0:
                url = self._extract_url(stdout_text)
                return {"success": True, "url": url, "error": "", "executor": "local"}

            error = stderr_text[:300] or stdout_text[:300]
            logger.error("❌ 本地发布失败: platform=%s error=%s", platform, error)
            return {"success": False, "url": "", "error": error, "executor": "local"}
        except asyncio.TimeoutError:
            return {"success": False, "url": "", "error": "发布超时", "executor": "local"}
        except Exception as e:
            return {"success": False, "url": "", "error": str(e), "executor": "local"}
        finally:
            if os.path.exists(config_file):
                os.remove(config_file)

    @staticmethod
    def _extract_url(output: str) -> str:
        urls = re.findall(r"https?://\S+", output or "")
        return urls[-1] if urls else ""


class RemoteSocialAutoUploadAdapter(SocialAutoUploadAdapter):
    """通过 VPS 分发网关执行发布"""

    def __init__(self, base_url: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def publish(self, platform: str, payload: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        client = await self._get_client()
        body = dict(payload)
        body["platform"] = platform

        try:
            endpoint = f"{self.base_url}/api/publish"
            response = await client.post(endpoint, json=body)
            if response.status_code == 404:
                response = await client.post(f"{self.base_url}/publish", json=body)

            if response.status_code >= 400:
                return {
                    "success": False,
                    "url": "",
                    "error": f"远程发布失败 HTTP {response.status_code}: {response.text[:300]}",
                    "executor": "remote",
                }

            result = response.json() if response.text else {}
            success = bool(result.get("success", True))
            return {
                "success": success,
                "url": result.get("url", ""),
                "error": result.get("error", ""),
                "executor": "remote",
                "remote_job_id": result.get("job_id", ""),
            }
        except Exception as e:
            return {"success": False, "url": "", "error": str(e), "executor": "remote"}

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
