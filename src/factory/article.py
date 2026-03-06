"""
图文生成模块
- 基于字幕/翻译文本生成图文内容
- 适用于小红书图文笔记、公众号文章等
"""
import asyncio
import json
import logging
import httpx
from typing import Optional, Dict, Any, List

from core.config import Config

logger = logging.getLogger(__name__)


class ArticleGenerator:
    """
    图文内容生成器
    基于翻译后的字幕/文本，利用LLM生成文章
    """

    def __init__(self, api_base: str = None, api_key: str = None, model: str = None):
        config = Config()

        self.api_base = api_base or config.get("llm", "base_url", default="https://claude2.sssaicode.com/api/v1")
        self.api_key = api_key or config.get("llm", "api_key", default="")
        self.model = model or config.get("llm", "model", default="gpt-5.2-codex")
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=120)
        return self._client

    async def _call_llm(self, system_prompt: str, user_prompt: str, max_tokens: int = 4000) -> Optional[str]:
        """调用LLM"""
        client = await self._get_client()

        try:
            response = await client.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.7,
                },
            )

            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"]
            else:
                logger.error(f"LLM调用失败: HTTP {response.status_code}")
                return None

        except Exception as e:
            logger.error(f"LLM调用异常: {e}")
            return None

    async def generate_xiaohongshu_note(
        self,
        title: str,
        transcript: str,
        video_duration: float = 0,
    ) -> Optional[Dict[str, str]]:
        """
        生成小红书图文笔记

        Args:
            title: 视频标题
            transcript: 翻译后的文本
            video_duration: 视频时长（秒）

        Returns:
            Dict: {"title": str, "content": str, "tags": List[str]}
        """
        system_prompt = (
            "你是一个小红书爆款笔记创作者。"
            "你擅长将视频内容改写为小红书风格的图文笔记。"
            "要求：标题吸引人（不超过20字），内容分段清晰，适当使用emoji，"
            "末尾有互动引导（如'你怎么看？评论区告诉我~'）。"
        )

        user_prompt = f"""请将以下视频内容改写为小红书图文笔记。

视频标题: {title}
视频时长: {int(video_duration / 60)} 分钟
视频内容（字幕文本）:
{transcript[:3000]}

输出格式（JSON）:
{{
    "title": "小红书标题（不超过20字）",
    "content": "笔记正文（500-1000字，分段，含emoji）",
    "tags": ["标签1", "标签2", "标签3"]
}}

只输出JSON。
"""

        result = await self._call_llm(system_prompt, user_prompt)
        return self._parse_json_result(result)

    async def generate_wechat_article(
        self,
        title: str,
        transcript: str,
        video_duration: float = 0,
    ) -> Optional[Dict[str, str]]:
        """
        生成公众号/视频号配套文章

        Args:
            title: 视频标题
            transcript: 翻译后的文本
            video_duration: 视频时长（秒）

        Returns:
            Dict: {"title": str, "content": str, "summary": str}
        """
        system_prompt = (
            "你是一个专业的自媒体内容编辑。"
            "你擅长将视频内容改写为高质量的文章。"
            "要求：文章结构清晰，有导语、正文、总结，"
            "用词准确，适合微信公众号传播。"
        )

        user_prompt = f"""请将以下视频内容改写为公众号文章。

视频标题: {title}
视频时长: {int(video_duration / 60)} 分钟
视频内容（字幕文本）:
{transcript[:4000]}

输出格式（JSON）:
{{
    "title": "文章标题",
    "summary": "100字以内的文章摘要",
    "content": "文章正文（1000-3000字，含小标题分段）"
}}

只输出JSON。
"""

        result = await self._call_llm(system_prompt, user_prompt, max_tokens=6000)
        return self._parse_json_result(result)

    async def generate_summary(
        self,
        transcript: str,
        max_length: int = 200,
    ) -> Optional[str]:
        """
        生成内容摘要

        Args:
            transcript: 翻译后的文本
            max_length: 最大长度

        Returns:
            Optional[str]: 摘要文本
        """
        system_prompt = "你是一个内容摘要专家。请简洁地总结以下视频内容。"

        user_prompt = f"""请用不超过{max_length}字总结以下视频内容:

{transcript[:3000]}

要求：
1. 突出核心观点
2. 语言简洁有力
3. 直接输出摘要文本，不要有前缀
"""

        result = await self._call_llm(system_prompt, user_prompt, max_tokens=500)
        if result:
            return result.strip()[:max_length]
        return None

    def _parse_json_result(self, result: Optional[str]) -> Optional[Dict]:
        """解析LLM返回的JSON"""
        if not result:
            return None

        try:
            text = result.strip()
            # 去除markdown代码块
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
            text = text.strip()

            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"JSON解析失败: {result[:200]}")
            return None

    async def process(
        self,
        title: str,
        transcript: str,
        output_dir: str,
        video_duration: float = 0,
        platforms: List[str] = None,
    ) -> Dict[str, Dict]:
        """
        完整的图文生成流程

        Args:
            title: 视频标题
            transcript: 翻译文本
            output_dir: 输出目录
            video_duration: 视频时长
            platforms: 目标平台列表

        Returns:
            Dict[str, Dict]: 各平台的图文内容
        """
        import os
        os.makedirs(output_dir, exist_ok=True)
        results = {}

        if platforms is None:
            platforms = ["xiaohongshu", "wechat"]

        tasks = []
        task_platforms = []

        for platform in platforms:
            if platform == "xiaohongshu":
                tasks.append(self.generate_xiaohongshu_note(title, transcript, video_duration))
                task_platforms.append(platform)
            elif platform in ("wechat", "weixin"):
                tasks.append(self.generate_wechat_article(title, transcript, video_duration))
                task_platforms.append(platform)

        if tasks:
            outputs = await asyncio.gather(*tasks)
            for platform, output in zip(task_platforms, outputs):
                if output:
                    results[platform] = output

                    # 保存到文件
                    import json as json_mod
                    filepath = os.path.join(output_dir, f"article_{platform}.json")
                    with open(filepath, "w", encoding="utf-8") as f:
                        json_mod.dump(output, f, ensure_ascii=False, indent=2)
                    logger.info(f"📝 图文生成: {platform} → {filepath}")

        # 生成通用摘要
        summary = await self.generate_summary(transcript)
        if summary:
            results["summary"] = {"text": summary}
            summary_path = os.path.join(output_dir, "summary.txt")
            with open(summary_path, "w", encoding="utf-8") as f:
                f.write(summary)

        return results

    async def close(self):
        """关闭客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None
