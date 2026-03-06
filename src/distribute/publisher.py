"""
发布模块 - 封装 social-auto-upload 进行多平台发布
支持: 抖音、B站、小红书、YouTube、视频号
"""
import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional, Dict, Any, List

import httpx

from core.config import Config

logger = logging.getLogger(__name__)


PLATFORM_SCRIPT_MAP = {
    "douyin": "upload_video_to_douyin.py",
    "bilibili": "upload_video_to_bilibili.py",
    "xiaohongshu": "upload_video_to_xhs.py",
    "youtube": "upload_video_to_youtube.py",
    "weixin": "upload_video_to_tencent.py",
}


class PlatformPublisher:
    """平台发布器基类"""

    platform: str = ""

    async def publish(
        self,
        video_path: str,
        title: str,
        description: str = "",
        tags: List[str] = None,
        cover_path: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        raise NotImplementedError


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
            # 优先使用标准 publish 接口，兼容旧网关 /publish
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


class SocialAutoUploadPublisher(PlatformPublisher):
    """基于适配层的 social-auto-upload 发布器"""

    def __init__(
        self,
        platform: str,
        social_auto_upload_path: str = None,
        adapter: Optional[SocialAutoUploadAdapter] = None,
    ):
        self.platform = platform
        config = Config()

        self.sau_path = social_auto_upload_path or config.get(
            "distribute", "social_auto_upload_path",
            default=str(Path.home() / "Projects" / "social-auto-upload"),
        )
        self.account_dir = config.get(
            "distribute", "account_dir",
            default=str(Path.home() / ".video-factory" / "accounts"),
        )
        distributor_api = config.get("vps", "distributor_api", default="")

        if adapter is not None:
            self.adapter = adapter
        elif distributor_api:
            self.adapter = RemoteSocialAutoUploadAdapter(distributor_api)
        else:
            self.adapter = LocalSocialAutoUploadAdapter(self.sau_path)

    async def publish(
        self,
        video_path: str,
        title: str,
        description: str = "",
        tags: List[str] = None,
        cover_path: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        payload = {
            "video_path": video_path,
            "title": title,
            "description": description,
            "tags": tags or [],
            "cover_path": cover_path,
            "task_id": kwargs.get("task_id", ""),
            "product_type": kwargs.get("product_type", ""),
            "idempotency_key": kwargs.get("idempotency_key", ""),
            "r2_path": kwargs.get("r2_path", ""),
            "r2_cover_path": kwargs.get("r2_cover_path", ""),
        }
        return await self.adapter.publish(self.platform, payload, **kwargs)


class ManualPublisher(PlatformPublisher):
    """手动发布（生成发布清单）"""

    def __init__(self, platform: str):
        self.platform = platform

    async def publish(
        self,
        video_path: str,
        title: str,
        description: str = "",
        tags: List[str] = None,
        cover_path: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        checklist = {
            "platform": self.platform,
            "video_path": video_path,
            "title": title,
            "description": description,
            "tags": tags or [],
            "cover_path": cover_path,
            "status": "待手动发布",
            "task_id": kwargs.get("task_id", ""),
            "idempotency_key": kwargs.get("idempotency_key", ""),
        }
        logger.info("📋 生成手动发布清单: %s - %s", self.platform, title)
        return {"success": True, "url": "", "error": "", "manual_checklist": checklist, "executor": "manual"}


class PublishManager:
    """发布管理器"""

    def __init__(self):
        config = Config()
        self._publishers: Dict[str, PlatformPublisher] = {}

        default_platforms = ["bilibili", "douyin", "xiaohongshu", "youtube"]
        auto_publish_enabled = config.get("distribute", "auto_publish", default=False)
        shared_adapter = self._build_social_auto_upload_adapter(config) if auto_publish_enabled else None

        for platform in default_platforms:
            if auto_publish_enabled:
                self._publishers[platform] = SocialAutoUploadPublisher(platform, adapter=shared_adapter)
            else:
                self._publishers[platform] = ManualPublisher(platform)

    @staticmethod
    def _build_social_auto_upload_adapter(config: Config) -> SocialAutoUploadAdapter:
        distributor_api = config.get("vps", "distributor_api", default="")
        if distributor_api:
            logger.info("分发模式: 远程VPS (%s)", distributor_api)
            return RemoteSocialAutoUploadAdapter(distributor_api)

        sau_path = config.get(
            "distribute", "social_auto_upload_path",
            default=str(Path.home() / "Projects" / "social-auto-upload"),
        )
        logger.info("分发模式: 本地social-auto-upload (%s)", sau_path)
        return LocalSocialAutoUploadAdapter(sau_path)

    def set_publisher(self, platform: str, publisher: PlatformPublisher):
        self._publishers[platform] = publisher

    async def publish_to_platform(
        self,
        platform: str,
        video_path: str,
        title: str,
        description: str = "",
        tags: List[str] = None,
        cover_path: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        publisher = self._publishers.get(platform)
        if not publisher:
            return {"success": False, "url": "", "error": f"未配置平台: {platform}"}

        return await publisher.publish(
            video_path=video_path,
            title=title,
            description=description,
            tags=tags,
            cover_path=cover_path,
            **kwargs,
        )

    async def publish_to_all(
        self,
        products: List[Dict[str, Any]],
        metadata_map: Dict[str, Dict] = None,
        task_id: str = "",
    ) -> Dict[str, Dict[str, Any]]:
        results = {}
        metadata_map = metadata_map or {}

        platform_products: Dict[str, List[Dict]] = {}
        for product in products:
            platform = product.get("platform", "all")
            ptype = product.get("type", "")

            if platform == "all":
                if ptype == "long_video":
                    for p in ["bilibili", "youtube"]:
                        platform_products.setdefault(p, []).append(product)
                elif ptype == "short_clip":
                    for p in ["douyin", "xiaohongshu"]:
                        platform_products.setdefault(p, []).append(product)
            else:
                platform_products.setdefault(platform, []).append(product)

        for platform, prods in platform_products.items():
            meta = metadata_map.get(platform, {})

            for product in prods:
                video_path = product.get("local_path", "")
                title = meta.get("title", product.get("title", ""))
                description = meta.get("description", product.get("description", ""))
                tags = meta.get("tags", product.get("tags", []))
                cover_path = product.get("cover_path", "")

                result = await self.publish_to_platform(
                    platform=platform,
                    video_path=video_path,
                    title=title,
                    description=description,
                    tags=tags,
                    cover_path=cover_path,
                    task_id=task_id,
                    product_type=product.get("type", "unknown"),
                    r2_path=product.get("r2_path", ""),
                )

                results[f"{platform}_{product.get('type', 'unknown')}"] = result
                await asyncio.sleep(5)

        return results
