"""
元数据生成模块
- 利用LLM生成平台适配的标题、描述、标签
- 支持多平台差异化输出
"""
import asyncio
import json
import logging
import re
import httpx
from typing import Optional, Dict, Any, List, Tuple

from core.config import Config

logger = logging.getLogger(__name__)


# 各平台的内容规则
PLATFORM_RULES = {
    "bilibili": {
        "title_max_length": 80,
        "description_max_length": 2000,
        "max_tags": 12,
        "tag_max_length": 20,
        "style": "活泼有趣，适度使用emoji，B站风格，吸引年轻用户",
    },
    "douyin": {
        "title_max_length": 55,
        "description_max_length": 300,
        "max_tags": 5,
        "tag_max_length": 20,
        "style": "简短有力，使用热门话题标签，短视频风格",
    },
    "xiaohongshu": {
        "title_max_length": 20,
        "description_max_length": 1000,
        "max_tags": 10,
        "tag_max_length": 15,
        "style": "种草分享风格，使用emoji，小红书调性",
    },
    "youtube": {
        "title_max_length": 100,
        "description_max_length": 5000,
        "max_tags": 30,
        "tag_max_length": 30,
        "style": "SEO友好，包含关键词，国际化视角",
    },
    "weixin": {
        "title_max_length": 64,
        "description_max_length": 500,
        "max_tags": 5,
        "tag_max_length": 15,
        "style": "正式稳重，适合微信视频号调性",
    },
}


class MetadataGenerator:
    """
    元数据生成器
    利用LLM为不同平台生成优化的标题、描述、标签
    """

    @staticmethod
    def _as_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        if isinstance(value, (int, float)):
            return bool(value)
        return default

    @staticmethod
    def _as_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def __init__(
        self,
        api_base: str = None,
        api_key: str = None,
        model: str = None,
        strict_json: Optional[bool] = None,
        response_format: Optional[str] = None,
        max_retries: Optional[int] = None,
    ):
        config = Config()

        self.api_base = api_base or config.get("llm", "base_url", default="https://claude2.sssaicode.com/api/v1")
        self.api_key = api_key or config.get("llm", "api_key", default="")
        self.model = model or config.get("llm", "model", default="gpt-5.2-codex")
        cfg_strict_json = config.get("llm", "strict_json", default=True)
        cfg_response_format = config.get("llm", "response_format", default="json_object")
        cfg_max_retries = config.get("llm", "max_retries", default=2)

        self.strict_json = self._as_bool(cfg_strict_json, default=True) if strict_json is None else self._as_bool(strict_json, default=True)
        self.response_format = response_format or cfg_response_format
        retries_value = cfg_max_retries if max_retries is None else max_retries
        self.max_retries = max(0, self._as_int(retries_value, default=2))
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60)
        return self._client

    def _build_messages(self, prompt: str) -> List[Dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "你是专业的视频内容运营专家。你必须只输出一个合法JSON对象，"
                    "禁止输出markdown、代码块、解释文本。"
                ),
            },
            {"role": "user", "content": prompt},
        ]

    async def _call_llm(self, prompt: str, max_tokens: int = 2000) -> Optional[str]:
        """调用LLM"""
        client = await self._get_client()
        payload = {
            "model": self.model,
            "messages": self._build_messages(prompt),
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }

        # 优先使用结构化JSON输出约束
        if self.strict_json and self.response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}

        try:
            response = await client.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            # 兼容不支持 response_format 的网关，自动降级重试一次
            if response.status_code >= 400 and payload.get("response_format"):
                logger.warning(
                    "LLM网关可能不支持 response_format，降级为纯Prompt JSON约束: HTTP %s",
                    response.status_code,
                )
                payload.pop("response_format", None)
                response = await client.post(
                    f"{self.api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )

            if response.status_code != 200:
                logger.error(f"LLM调用失败: HTTP {response.status_code}, body={response.text[:300]}")
                return None

            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                logger.error("LLM调用成功但返回空choices")
                return None

            content = choices[0].get("message", {}).get("content", "")
            if isinstance(content, str):
                return content

            # 兼容部分OpenAI兼容网关返回 content=list[dict{text:...}]
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        txt = block.get("text", "")
                        if txt:
                            parts.append(txt)
                return "\n".join(parts).strip()

            return str(content)

        except Exception as e:
            logger.error(f"LLM调用异常: {e}")
            return None

    def _build_prompt(
        self,
        platform: str,
        original_title: str,
        translated_title: str,
        transcript_excerpt: str,
        video_duration: float,
        content_type: str,
        rules: Dict[str, Any],
        previous_error: str = "",
    ) -> str:
        retry_hint = ""
        if previous_error:
            retry_hint = f"\n上一次输出失败原因：{previous_error}。请修正并仅输出合法JSON对象。"

        content_type_label = "长视频" if content_type == "long_video" else "短视频" if content_type == "short_clip" else "图文"
        return f"""请为以下视频生成{platform}平台的标题、描述和标签。

视频信息:
- 原始标题: {original_title}
- 翻译标题: {translated_title}
- 视频时长: {int(video_duration / 60)} 分钟
- 内容类型: {content_type_label}
- 内容摘要: {transcript_excerpt[:500]}

平台规则:
- 标题最大长度: {rules['title_max_length']} 字符
- 描述最大长度: {rules['description_max_length']} 字符
- 最多标签数: {rules['max_tags']}
- 标签最大长度: {rules['tag_max_length']} 字符
- 风格要求: {rules['style']}

输出要求:
1. 只返回一个JSON对象，禁止markdown代码块、禁止额外解释文字。
2. JSON必须包含且只包含以下键: "title", "description", "tags"
3. "tags" 必须是字符串数组。
4. 标题要吸引眼球但不标题党；描述要利于搜索推荐；标签包含关键词与话题。

JSON模板:
{{
  "title": "标题文本",
  "description": "描述文本",
  "tags": ["标签1", "标签2", "标签3"]
}}
{retry_hint}
"""

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        raw = (text or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", raw).strip()
            if raw.endswith("```"):
                raw = raw[:-3].strip()
        return raw

    def _try_parse_json_object(self, text: str) -> Optional[Dict[str, Any]]:
        raw = self._strip_code_fence(text)
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return obj if isinstance(obj, dict) else None

    def _extract_json_object(self, text: str) -> Tuple[Optional[Dict[str, Any]], str]:
        raw = self._strip_code_fence(text)
        decoder = json.JSONDecoder()
        for idx, ch in enumerate(raw):
            if ch != "{":
                continue
            try:
                obj, end = decoder.raw_decode(raw[idx:])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj, raw[idx:idx + end]
        return None, ""

    @staticmethod
    def _repair_json_text(text: str) -> str:
        raw = (text or "").strip()
        raw = raw.replace("\u201c", '"').replace("\u201d", '"')
        raw = raw.replace("\u2018", "'").replace("\u2019", "'")
        raw = re.sub(r",(\s*[}\]])", r"\1", raw)
        return raw

    def _parse_with_layers(self, llm_output: str) -> Tuple[Optional[Dict[str, Any]], str, str]:
        # Layer 1: 直接JSON解析
        strict_obj = self._try_parse_json_object(llm_output)
        if strict_obj is not None:
            return strict_obj, "strict", ""

        # Layer 2: 从文本中提取首个JSON对象
        extracted_obj, extracted_text = self._extract_json_object(llm_output)
        if extracted_obj is not None:
            return extracted_obj, "extract", ""

        # Layer 3: 对候选JSON做轻量修复后再解析
        candidate = extracted_text
        if not candidate:
            left = llm_output.find("{")
            right = llm_output.rfind("}")
            if left != -1 and right != -1 and right > left:
                candidate = llm_output[left:right + 1]
            else:
                candidate = llm_output

        repaired = self._repair_json_text(self._strip_code_fence(candidate))
        repaired_obj = self._try_parse_json_object(repaired)
        if repaired_obj is not None:
            return repaired_obj, "repair", ""

        return None, "fallback", "json_parse_failed"

    @staticmethod
    def _validate_schema(metadata: Dict[str, Any]) -> Tuple[bool, str]:
        required = ("title", "description", "tags")
        missing = [k for k in required if k not in metadata]
        if missing:
            return False, f"missing_fields:{','.join(missing)}"
        if not isinstance(metadata.get("title"), str):
            return False, "title_not_string"
        if not isinstance(metadata.get("description"), str):
            return False, "description_not_string"
        if not isinstance(metadata.get("tags"), list):
            return False, "tags_not_list"
        return True, ""

    @staticmethod
    def _normalize_tags(tags: Any, max_tags: int, tag_max_length: int) -> List[str]:
        if not isinstance(tags, list):
            return []

        cleaned = []
        seen = set()
        for tag in tags:
            if not isinstance(tag, str):
                continue
            t = tag.strip()
            if not t:
                continue
            t = t[:tag_max_length]
            if t not in seen:
                seen.add(t)
                cleaned.append(t)
            if len(cleaned) >= max_tags:
                break
        return cleaned

    def _build_fallback_metadata(
        self,
        platform: str,
        original_title: str,
        translated_title: str,
        transcript_excerpt: str,
        rules: Dict[str, Any],
        parse_error: str,
    ) -> Dict[str, Any]:
        return {
            "title": (translated_title or original_title)[:rules["title_max_length"]],
            "description": transcript_excerpt[:rules["description_max_length"]],
            "tags": [],
            "platform": platform,
            "parse_mode": "fallback",
            "parse_error": parse_error,
            "retry_count": self.max_retries,
        }

    async def generate_for_platform(
        self,
        platform: str,
        original_title: str,
        translated_title: str = "",
        transcript: str = "",
        video_duration: float = 0,
        content_type: str = "long_video",
    ) -> Dict[str, Any]:
        """
        为指定平台生成元数据

        Args:
            platform: 平台名称
            original_title: 原始标题
            translated_title: 翻译后的标题
            transcript: 翻译后的文本/字幕
            video_duration: 视频时长（秒）
            content_type: 内容类型（long_video/short_clip/article）

        Returns:
            Dict: {"title": str, "description": str, "tags": List[str]}
        """
        rules = PLATFORM_RULES.get(platform, PLATFORM_RULES["bilibili"])

        # 截取transcript前2000字符避免超长
        transcript_excerpt = transcript[:2000] if transcript else ""
        attempt_total = self.max_retries + 1
        last_error = ""

        for attempt in range(attempt_total):
            prompt = self._build_prompt(
                platform=platform,
                original_title=original_title,
                translated_title=translated_title,
                transcript_excerpt=transcript_excerpt,
                video_duration=video_duration,
                content_type=content_type,
                rules=rules,
                previous_error=last_error if attempt > 0 else "",
            )
            result = await self._call_llm(prompt)
            if not result:
                last_error = "empty_llm_response"
                logger.warning(
                    "元数据生成失败: platform=%s attempt=%s/%s error=%s",
                    platform,
                    attempt + 1,
                    attempt_total,
                    last_error,
                )
                continue

            parsed, parse_mode, parse_error = self._parse_with_layers(result)
            if parsed is None:
                last_error = parse_error
                logger.warning(
                    "元数据解析失败: platform=%s attempt=%s/%s parse_mode=%s error=%s",
                    platform,
                    attempt + 1,
                    attempt_total,
                    parse_mode,
                    last_error,
                )
                continue

            valid, schema_error = self._validate_schema(parsed)
            if not valid:
                last_error = schema_error
                logger.warning(
                    "元数据Schema不合规: platform=%s attempt=%s/%s parse_mode=%s error=%s",
                    platform,
                    attempt + 1,
                    attempt_total,
                    parse_mode,
                    last_error,
                )
                continue

            title = parsed.get("title", translated_title or original_title).strip()
            description = parsed.get("description", "").strip()
            tags = self._normalize_tags(
                parsed.get("tags", []),
                max_tags=rules["max_tags"],
                tag_max_length=rules["tag_max_length"],
            )

            metadata = {
                "title": (title or (translated_title or original_title))[:rules["title_max_length"]],
                "description": description[:rules["description_max_length"]],
                "tags": tags,
                "platform": platform,
                "parse_mode": parse_mode,
                "parse_error": "",
                "retry_count": attempt,
            }
            logger.info(
                "✅ 元数据生成成功: platform=%s parse_mode=%s retry_count=%s",
                platform,
                parse_mode,
                attempt,
            )
            return metadata

        fallback = self._build_fallback_metadata(
            platform=platform,
            original_title=original_title,
            translated_title=translated_title,
            transcript_excerpt=transcript_excerpt,
            rules=rules,
            parse_error=last_error or "unknown_error",
        )
        logger.warning(
            "元数据降级到fallback: platform=%s retries=%s error=%s",
            platform,
            self.max_retries,
            fallback["parse_error"],
        )
        return fallback

    async def generate_for_all_platforms(
        self,
        original_title: str,
        translated_title: str = "",
        transcript: str = "",
        video_duration: float = 0,
        platforms: List[str] = None,
        content_type: str = "long_video",
    ) -> Dict[str, Dict[str, Any]]:
        """
        为所有目标平台生成元数据

        Args:
            original_title: 原始标题
            translated_title: 翻译标题
            transcript: 翻译文本
            video_duration: 视频时长
            platforms: 平台列表，None则默认全平台
            content_type: 内容类型

        Returns:
            Dict[str, Dict]: {"bilibili": {...}, "douyin": {...}, ...}
        """
        if platforms is None:
            platforms = ["bilibili", "douyin", "xiaohongshu", "youtube"]

        # 并发生成各平台元数据
        tasks = []
        for platform in platforms:
            tasks.append(
                self.generate_for_platform(
                    platform=platform,
                    original_title=original_title,
                    translated_title=translated_title,
                    transcript=transcript,
                    video_duration=video_duration,
                    content_type=content_type,
                )
            )

        results = await asyncio.gather(*tasks)

        metadata_map = {}
        for platform, result in zip(platforms, results):
            metadata_map[platform] = result

        logger.info(f"📝 元数据生成完成: {list(metadata_map.keys())}")
        return metadata_map

    async def close(self):
        """关闭客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None
