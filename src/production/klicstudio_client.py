"""
KlicStudio API 客户端
封装KlicStudio Server的HTTP API调用
"""
import httpx
import asyncio
import logging
from typing import Optional, Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """任务状态"""
    PROCESSING = 1
    SUCCESS = 2
    FAILED = 3


class KlicStudioClient:
    """KlicStudio API客户端"""

    def __init__(self, base_url: str = "http://127.0.0.1:8888", timeout: int = 3600):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)
        self.last_error: str = ""

    async def submit_task(
        self,
        url: str,
        origin_lang: str = "en",
        target_lang: str = "zh_cn",
        bilingual: bool = True,
        enable_tts: bool = False,
        tts_voice_code: Optional[str] = None,
        embed_subtitle_video_type: str = "none",
        modal_filter: bool = True
    ) -> Optional[str]:
        """
        提交翻译任务

        Args:
            url: YouTube URL或local:路径
            origin_lang: 原始语言（如 en）
            target_lang: 目标语言（如 zh_cn）
            bilingual: 是否双语字幕
            enable_tts: 是否启用配音
            tts_voice_code: TTS语音编码
            embed_subtitle_video_type: 视频类型（none/horizontal/vertical）
            modal_filter: 是否过滤语气词

        Returns:
            Optional[str]: 任务ID，失败返回None
        """
        self.last_error = ""
        try:
            payload = {
                "app_id": 0,
                "url": url,
                "origin_lang": origin_lang,
                "target_lang": target_lang,
                "bilingual": 1 if bilingual else 2,
                "translation_subtitle_pos": 1,
                "modal_filter": 1 if modal_filter else 2,
                "tts": 1 if enable_tts else 2,
                "tts_voice_code": tts_voice_code or "",
                "embed_subtitle_video_type": embed_subtitle_video_type,
                "origin_language_word_one_line": 12
            }

            response = await self.client.post(
                f"{self.base_url}/api/capability/subtitleTask",
                json=payload
            )

            data = response.json()

            if data.get("error") == 0:
                task_id = data.get("data", {}).get("task_id")
                logger.info(f"✅ 任务提交成功: {task_id}")
                return task_id
            else:
                self.last_error = str(data.get("msg") or "unknown error")
                logger.error(f"❌ 任务提交失败: {self.last_error}")
                return None

        except Exception as e:
            self.last_error = str(e)
            logger.error(f"提交任务异常: {e}")
            return None

    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        查询任务状态

        Args:
            task_id: 任务ID

        Returns:
            Optional[Dict]: 任务信息
                - 成功时返回 data 字典 (含 status, process_percent 等)
                - KlicStudio返回失败时返回 {"status": 3, "error_msg": "..."}
                - 网络异常时返回 None
        """
        try:
            response = await self.client.get(
                f"{self.base_url}/api/capability/subtitleTask",
                params={"taskId": task_id}
            )

            data = response.json()

            if data.get("error") == 0:
                return data.get("data", {})
            else:
                # KlicStudio返回错误（任务失败、任务不存在等）
                error_msg = data.get("msg", "未知错误")
                logger.error(f"KlicStudio任务错误: {error_msg}")
                # 返回标记失败的字典，让调用方能区分"网络异常"和"任务失败"
                return {"status": 3, "error_msg": error_msg, "process_percent": 0}

        except Exception as e:
            logger.error(f"查询任务异常: {e}")
            return None

    async def wait_for_completion(
        self,
        task_id: str,
        poll_interval: int = 10,
        max_wait: int = 3600
    ) -> Optional[Dict[str, Any]]:
        """
        等待任务完成

        Args:
            task_id: 任务ID
            poll_interval: 轮询间隔（秒）
            max_wait: 最大等待时间（秒）

        Returns:
            Optional[Dict]: 完成后的任务信息，超时/失败返回None
        """
        elapsed = 0

        while elapsed < max_wait:
            task_info = await self.get_task_status(task_id)

            if not task_info:
                logger.error(f"无法获取任务状态: {task_id}")
                return None

            status = task_info.get("status")
            progress = task_info.get("process_percent", 0)

            logger.info(f"📊 任务 {task_id} 进度: {progress}% (状态: {status})")

            # 状态 2 = 成功
            if status == TaskStatus.SUCCESS.value or progress == 100:
                logger.info(f"✅ 任务完成: {task_id}")
                return task_info

            # 状态 3 = 失败
            if status == TaskStatus.FAILED.value:
                logger.error(f"❌ 任务失败: {task_id}")
                return None

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        logger.error(f"⏰ 任务超时: {task_id} (超过{max_wait}秒)")
        return None

    async def download_file(self, file_path: str, save_to: str) -> bool:
        """
        下载文件

        Args:
            file_path: KlicStudio文件路径（如 tasks/xxx/output/bilingual_srt.srt）
            save_to: 保存到本地的路径

        Returns:
            bool: 是否成功
        """
        try:
            url = f"{self.base_url}/api/file/{file_path}"

            async with self.client.stream("GET", url) as response:
                if response.status_code == 200:
                    with open(save_to, "wb") as f:
                        async for chunk in response.aiter_bytes():
                            f.write(chunk)

                    logger.info(f"✅ 文件下载成功: {save_to}")
                    return True
                else:
                    logger.error(f"❌ 文件下载失败: HTTP {response.status_code}")
                    return False

        except Exception as e:
            logger.error(f"下载文件异常: {e}")
            return False

    async def get_config(self) -> Optional[Dict[str, Any]]:
        """获取KlicStudio配置"""
        try:
            response = await self.client.get(f"{self.base_url}/api/config")
            data = response.json()

            if data.get("error") == 0:
                return data.get("data", {})
            else:
                return None

        except Exception as e:
            logger.error(f"获取配置异常: {e}")
            return None

    async def close(self):
        """关闭客户端"""
        await self.client.aclose()


# 使用示例
if __name__ == "__main__":
    async def test():
        client = KlicStudioClient()

        # 测试连接
        config = await client.get_config()
        print(f"LLM Model: {config.get('llm', {}).get('model')}")

        # 提交任务
        task_id = await client.submit_task(
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            origin_lang="en",
            target_lang="zh_cn",
            enable_tts=False
        )

        if task_id:
            # 等待完成
            result = await client.wait_for_completion(task_id, poll_interval=5)

            if result:
                print(f"翻译标题: {result.get('video_info', {}).get('translated_title')}")

        await client.close()

    asyncio.run(test())
