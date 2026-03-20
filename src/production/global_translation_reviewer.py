"""
全局翻译审阅器。

职责：
- 识别视频领域（v1: music/general）
- 为音乐类视频抽取需要统一保留原文的专有名词词表
- 以句子组为单位重写字幕与元数据，保证全局一致
- 输出结构化审阅报告，并在问题无法修复时阻断任务
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import httpx

from core.config import Config
from core.task import Task
from production.sentence_regrouper import SentenceGroup, SentenceRegrouper
from production.subtitle_repair import SubtitleRepairer
from translation import get_translator
from translation.base import mask_secret

logger = logging.getLogger(__name__)

_SRT_BLOCK_PATTERN = re.compile(
    r"(\d+)\s*\n"
    r"([0-9:,]+)\s*-->\s*([0-9:,]+)\s*\n"
    r"(.*?)(?=\n\s*\n\d+\s*\n|\Z)",
    re.S,
)


@dataclass
class GlobalReviewResult:
    passed: bool
    skipped: bool
    fixed: bool
    domain: str
    confidence: float
    message: str
    report: Dict[str, Any] = field(default_factory=dict)
    translated_title: str = ""
    translated_description: str = ""
    target_text: str = ""
    blocking_reason: str = ""


class GlobalTranslationReviewer:
    """基于通用 LLM 的整稿语义审阅层。"""

    REPORT_FILENAME = "global_review_report.json"
    PLACEHOLDER_SECRET_MARKERS = (
        "your_api_key",
        "replace_me",
        "changeme",
        "your-key",
        "your_key",
        "sk-xxxx",
    )

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
    def _as_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _as_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def __init__(self, config: Optional[Config] = None):
        cfg = config or Config()
        self.config = cfg
        self.enabled = self._as_bool(
            cfg.get("quality", "global_review", "enabled", default=True),
            default=True,
        )
        self.provider = str(
            cfg.get("quality", "global_review", "provider", default="llm") or "llm"
        ).strip().lower()
        self.fail_open = self._as_bool(
            cfg.get("quality", "global_review", "fail_open", default=True),
            default=True,
        )
        self.domain_confidence_threshold = self._as_float(
            cfg.get("quality", "global_review", "domain_confidence_threshold", default=0.6),
            default=0.6,
        )
        self.chunk_size = max(
            4,
            self._as_int(cfg.get("quality", "global_review", "chunk_size", default=18), default=18),
        )
        self.max_excerpt_chars = max(
            2000,
            self._as_int(
                cfg.get("quality", "global_review", "max_excerpt_chars", default=12000),
                default=12000,
            ),
        )
        self.music_enabled = self._as_bool(
            cfg.get("quality", "global_review", "music", "enabled", default=True),
            default=True,
        )
        self.max_glossary_terms = max(
            4,
            self._as_int(
                cfg.get("quality", "global_review", "music", "max_glossary_terms", default=24),
                default=24,
            ),
        )

        runtime = get_translator(config=cfg, provider="llm").runtime_config()
        self.api_base = runtime.base_url
        self.api_key = runtime.api_key
        self.model = runtime.model
        self.timeout = max(
            10,
            self._as_int(
                cfg.get(
                    "quality",
                    "global_review",
                    "timeout",
                    default=cfg.get("translation", "llm", "timeout", default=runtime.timeout),
                ),
                default=runtime.timeout or 60,
            ),
        )
        self.strict_json = self._as_bool(cfg.get("llm", "strict_json", default=True), default=True)
        self.response_format = str(
            cfg.get("llm", "response_format", default="json_object") or "json_object"
        ).strip().lower()
        self.max_retries = max(
            0,
            self._as_int(cfg.get("llm", "max_retries", default=2), default=2),
        )
        self._client: Optional[httpx.AsyncClient] = None

        logger.info(
            "全局审阅器已加载: enabled=%s provider=%s base_url=%s model=%s api_key=%s",
            self.enabled,
            self.provider,
            self.api_base,
            self.model,
            mask_secret(self.api_key),
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @classmethod
    def _looks_like_placeholder_secret(cls, value: str) -> bool:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return False
        if normalized.endswith("_here"):
            return True
        return any(marker in normalized for marker in cls.PLACEHOLDER_SECRET_MARKERS)

    def _runtime_missing_reason(self) -> str:
        if not self.enabled:
            return ""
        if self.provider != "llm":
            return f"global_review.provider 暂不支持: {self.provider}"
        if not self.api_base:
            return "llm 缺少 base_url"
        if not self.api_key:
            return "llm 缺少 api_key"
        if self._looks_like_placeholder_secret(self.api_key):
            return "llm api_key 仍是占位符，请配置真实可用的密钥"
        if not self.model:
            return "llm 缺少 model"
        return ""

    @staticmethod
    def _parse_srt(path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []

        content = path.read_text(encoding="utf-8", errors="ignore").replace("\r\n", "\n")
        entries: List[Dict[str, Any]] = []
        for match in _SRT_BLOCK_PATTERN.finditer(content):
            text_lines = [line.strip() for line in match.group(4).strip().split("\n") if line.strip()]
            entries.append(
                {
                    "index": int(match.group(1)),
                    "start": match.group(2).strip(),
                    "end": match.group(3).strip(),
                    "lines": text_lines or [""],
                }
            )
        return entries

    @staticmethod
    def _write_srt(entries: Sequence[Dict[str, Any]], path: Path):
        blocks: List[str] = []
        for idx, entry in enumerate(entries, start=1):
            lines = [str(line).strip() for line in (entry.get("lines") or []) if str(line).strip()]
            text = "\n".join(lines) if lines else " "
            blocks.append(
                f"{idx}\n{entry.get('start', '00:00:00,000')} --> {entry.get('end', '00:00:01,000')}\n{text}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")

    @staticmethod
    def _entry_text(entry: Dict[str, Any]) -> str:
        return " ".join(
            str(line).strip() for line in (entry.get("lines") or []) if str(line).strip()
        ).strip()

    @staticmethod
    def _normalize_spaces(text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "").strip())

    @classmethod
    def _normalize_lookup(cls, text: str) -> str:
        return cls._normalize_spaces(text).lower()

    @classmethod
    def _resolve_groups(
        cls,
        groups: Sequence[SentenceGroup],
        entries: Sequence[Dict[str, Any]],
    ) -> List[SentenceGroup]:
        total = len(entries)
        if total == 0:
            return []

        normalized: List[SentenceGroup] = []
        seen_indexes = set()
        valid = True

        for raw_group in groups or []:
            cue_indexes = []
            for idx in raw_group.cue_indexes:
                if 0 <= idx < total and idx not in seen_indexes:
                    cue_indexes.append(idx)
                    seen_indexes.add(idx)
            if not cue_indexes:
                continue
            cue_indexes = sorted(cue_indexes)
            source_lines = [cls._entry_text(entries[idx]) for idx in cue_indexes]
            normalized.append(
                SentenceGroup(
                    cue_indexes=cue_indexes,
                    source_lines=source_lines,
                    source_text=" ".join(line for line in source_lines if line).strip(),
                )
            )

        if len(seen_indexes) != total:
            valid = False
        elif [idx for group in normalized for idx in group.cue_indexes] != list(range(total)):
            valid = False

        if valid and normalized:
            return normalized

        return [
            SentenceGroup(
                cue_indexes=[idx],
                source_lines=[cls._entry_text(entries[idx])],
                source_text=cls._entry_text(entries[idx]),
            )
            for idx in range(total)
        ]

    @classmethod
    def _group_texts(
        cls,
        cue_lines: Sequence[str],
        groups: Sequence[SentenceGroup],
    ) -> List[str]:
        texts: List[str] = []
        for group in groups:
            merged = " ".join(
                cls._normalize_spaces(cue_lines[idx])
                for idx in group.cue_indexes
                if 0 <= idx < len(cue_lines) and cls._normalize_spaces(cue_lines[idx])
            ).strip()
            texts.append(merged)
        return texts

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        raw = str(text or "").strip()
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

    def _extract_json_object(self, text: str) -> Optional[Dict[str, Any]]:
        raw = self._strip_code_fence(text)
        decoder = json.JSONDecoder()
        for idx, ch in enumerate(raw):
            if ch != "{":
                continue
            try:
                obj, _ = decoder.raw_decode(raw[idx:])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
        return None

    async def _call_llm_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 3000,
    ) -> Dict[str, Any]:
        missing_reason = self._runtime_missing_reason()
        if missing_reason:
            raise RuntimeError(missing_reason)

        client = await self._get_client()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        if self.strict_json and self.response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}

        last_error = "unknown error"
        for _ in range(self.max_retries + 1):
            response = await client.post(
                f"{self.api_base.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if response.status_code >= 400 and payload.get("response_format"):
                payload.pop("response_format", None)
                response = await client.post(
                    f"{self.api_base.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )

            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                continue

            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if isinstance(content, list):
                content = "\n".join(
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("text")
                ).strip()

            parsed = self._try_parse_json_object(str(content))
            if parsed is None:
                parsed = self._extract_json_object(str(content))
            if parsed is not None:
                return parsed
            last_error = f"响应不是合法JSON对象: {str(content)[:200]}"

        raise RuntimeError(last_error)

    def _build_report_path(self, working_dir: Path) -> Path:
        return working_dir / self.REPORT_FILENAME

    def _persist_report(self, working_dir: Path, report: Dict[str, Any]) -> Path:
        path = self._build_report_path(working_dir)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _finalize_report(self, working_dir: Path, report: Dict[str, Any]) -> Dict[str, Any]:
        report_path = self._build_report_path(working_dir)
        report.setdefault("artifacts", {})
        report["artifacts"]["report_path"] = str(report_path)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    def _base_report(
        self,
        *,
        task: Task,
        domain: str = "general",
        confidence: float = 0.0,
        reason: str = "",
    ) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "task_id": task.task_id,
            "status": "pending",
            "passed": False,
            "skipped": False,
            "fixed": False,
            "domain": {
                "name": domain,
                "confidence": confidence,
                "reason": reason,
            },
            "policy": {
                "music_proper_nouns": "keep_original_language_only",
            },
            "glossary": [],
            "issues_before": [],
            "issues_after": [],
            "fixes_applied": {
                "subtitle_groups_changed": 0,
                "metadata_changed": False,
            },
            "artifacts": {
                "reviewed_files": [
                    "target_language_srt.srt",
                    "bilingual_srt.srt",
                    "translated_title",
                    "translated_description",
                ],
            },
            "summary": "",
            "blocking_reason": "",
        }

    def _summarize(self, *, domain: str, fixed: bool, issues_after: Sequence[Dict[str, Any]]) -> str:
        if domain != "music":
            return "全局审阅跳过：当前仅对 music 域启用强规则。"
        if issues_after:
            return f"全局审阅失败：music 域仍存在 {len(issues_after)} 个未修复问题。"
        if fixed:
            return "全局审阅通过：已按 music 专名规则统一字幕与元数据。"
        return "全局审阅通过：music 域内容已满足专名一致性要求。"

    def _normalize_glossary(self, raw_items: Any) -> List[Dict[str, Any]]:
        glossary: List[Dict[str, Any]] = []
        seen = set()
        for item in raw_items or []:
            if isinstance(item, dict):
                term = str(item.get("term") or item.get("name") or "").strip()
                category = str(item.get("category") or "other").strip().lower()
            else:
                term = str(item or "").strip()
                category = "other"
            if not term:
                continue
            normalized_key = term.lower()
            if normalized_key in seen:
                continue
            seen.add(normalized_key)
            glossary.append(
                {
                    "term": term,
                    "category": category or "other",
                    "must_keep_original": True,
                }
            )
            if len(glossary) >= self.max_glossary_terms:
                break
        return glossary

    def _music_term_issues(
        self,
        *,
        source_groups: Sequence[str],
        target_groups: Sequence[str],
        glossary: Sequence[Dict[str, Any]],
        translated_title: str,
        translated_description: str,
        source_title: str,
    ) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []
        artifacts: List[str] = []

        title_text = self._normalize_spaces(translated_title)
        desc_text = self._normalize_spaces(translated_description)
        if SubtitleRepairer.contains_translation_meta(title_text):
            artifacts.append("translated_title")
        if SubtitleRepairer.contains_translation_meta(desc_text):
            artifacts.append("translated_description")
        for idx, text in enumerate(target_groups):
            if SubtitleRepairer.contains_translation_meta(text):
                artifacts.append(f"group[{idx}]")
        if artifacts:
            issues.append(
                {
                    "code": "TRANSLATION_META_ARTIFACT",
                    "severity": "high",
                    "message": "翻译结果中仍包含模型注解或说明文字",
                    "examples": artifacts[:8],
                }
            )

        normalized_title_source = self._normalize_lookup(source_title)
        combined_targets = [self._normalize_lookup(text) for text in target_groups]
        title_lookup = self._normalize_lookup(title_text)

        for item in glossary:
            term = str(item.get("term") or "").strip()
            if not term:
                continue
            term_lookup = self._normalize_lookup(term)
            missing_groups: List[Dict[str, Any]] = []
            for idx, source in enumerate(source_groups):
                if term_lookup in self._normalize_lookup(source):
                    if idx >= len(combined_targets) or term_lookup not in combined_targets[idx]:
                        missing_groups.append(
                            {
                                "group_index": idx,
                                "source": source[:180],
                                "target": (target_groups[idx] if idx < len(target_groups) else "")[:180],
                            }
                        )
            if missing_groups:
                issues.append(
                    {
                        "code": "MUSIC_TERM_NOT_PRESERVED",
                        "severity": "high",
                        "term": term,
                        "message": f"专有名词未按原文保留: {term}",
                        "examples": missing_groups[:3],
                    }
                )

            title_examples: List[str] = []
            if term_lookup in normalized_title_source and term_lookup not in title_lookup:
                title_examples.append("translated_title")
            if title_examples:
                issues.append(
                    {
                        "code": "MUSIC_TERM_TITLE_NOT_PRESERVED",
                        "severity": "high",
                        "term": term,
                        "message": f"标题未保留专有名词原文: {term}",
                        "examples": title_examples,
                    }
                )

            mixed_patterns = [
                re.compile(rf"[\u4e00-\u9fff·]{2,}\s*[（(]\s*{re.escape(term)}\s*[）)]", re.IGNORECASE),
                re.compile(rf"{re.escape(term)}\s*[（(][^）)]*[\u4e00-\u9fff]", re.IGNORECASE),
            ]
            mixed_hits: List[str] = []
            for label, text in (
                ("translated_title", title_text),
                ("translated_description", desc_text),
            ):
                if any(pattern.search(text) for pattern in mixed_patterns):
                    mixed_hits.append(label)
            for idx, text in enumerate(target_groups):
                if any(pattern.search(text) for pattern in mixed_patterns):
                    mixed_hits.append(f"group[{idx}]")
                    if len(mixed_hits) >= 4:
                        break
            if mixed_hits:
                issues.append(
                    {
                        "code": "MUSIC_TERM_MIXED_FORM",
                        "severity": "high",
                        "term": term,
                        "message": f"专有名词出现中英混写或中文释义包裹: {term}",
                        "examples": mixed_hits[:4],
                    }
                )

        return issues

    async def _detect_domain_and_glossary(
        self,
        *,
        task: Task,
        source_groups: Sequence[str],
    ) -> Dict[str, Any]:
        excerpt_chunks: List[str] = []
        total_chars = 0
        for idx, text in enumerate(source_groups):
            line = f"[{idx}] {self._normalize_spaces(text)}"
            if not line.strip():
                continue
            if total_chars + len(line) > self.max_excerpt_chars:
                break
            excerpt_chunks.append(line)
            total_chars += len(line) + 1

        system_prompt = (
            "你是字幕全局审阅器。"
            "请识别视频领域，只允许输出一个 JSON 对象。"
            "domain 只能是 music 或 general。"
            "如果是 music，请提取需要在译文中统一保留原文的专有名词。"
        )
        user_prompt = f"""请基于以下视频内容进行判断。

要求：
1. 只有在内容明显讨论歌曲、乐队、歌手、专辑、音乐榜单、音乐媒体或音乐史评论时，domain 才能输出 music。
2. 若 domain=music，glossary 中只保留真正的音乐专有名词，使用原语言规范写法。
3. glossary 每项包含 term 和 category；category 仅可为 song、band、artist、album、publication、other。
4. 只输出 JSON，不要解释。

输出 JSON 模板：
{{
  "domain": "music",
  "confidence": 0.95,
  "reason": "一句简短原因",
  "glossary": [
    {{"term": "Bohemian Rhapsody", "category": "song"}}
  ]
}}

source_title:
{task.source_title or ""}

source_excerpt:
{chr(10).join(excerpt_chunks)}
"""
        result = await self._call_llm_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=1800,
        )
        domain = str(result.get("domain") or "general").strip().lower()
        if domain not in {"music", "general"}:
            domain = "general"
        confidence = self._as_float(result.get("confidence"), default=0.0)
        reason = str(result.get("reason") or "").strip()
        glossary = self._normalize_glossary(result.get("glossary"))
        return {
            "domain": domain,
            "confidence": confidence,
            "reason": reason,
            "glossary": glossary,
        }

    @staticmethod
    def _glossary_block(glossary: Sequence[Dict[str, Any]]) -> str:
        if not glossary:
            return "- 无"
        return "\n".join(
            f"- {item.get('category', 'other')}: {item.get('term', '')}"
            for item in glossary
            if item.get("term")
        )

    @staticmethod
    def _extract_translation_list(obj: Dict[str, Any], expected: int) -> List[str]:
        candidates = [
            obj.get("translations"),
            obj.get("items"),
            obj.get("results"),
            obj.get("group_translations"),
        ]
        for candidate in candidates:
            if isinstance(candidate, list):
                return [str(item or "").strip() for item in candidate[:expected]]
        if all(str(key).isdigit() for key in obj.keys()):
            items = [str(obj[key] or "").strip() for key in sorted(obj.keys(), key=lambda x: int(x))]
            return items[:expected]
        return []

    async def _rewrite_group_chunk(
        self,
        *,
        task: Task,
        chunk_sources: Sequence[str],
        chunk_targets: Sequence[str],
        glossary: Sequence[Dict[str, Any]],
    ) -> List[str]:
        system_prompt = (
            "你是字幕全局审稿器。你只输出一个 JSON 对象。"
            "请修订字幕翻译，不要输出任何注解、解释、markdown 或思考过程。"
        )
        payload_groups = [
            {
                "index": idx,
                "source": self._normalize_spaces(source),
                "current_translation": self._normalize_spaces(chunk_targets[idx]) if idx < len(chunk_targets) else "",
            }
            for idx, source in enumerate(chunk_sources)
        ]
        user_prompt = f"""请修订下面这一批字幕句子组的翻译，目标语言是 {task.target_lang}。

硬性规则：
1. 如果 source 中出现 glossary 里的音乐专有名词，译文中必须保留 glossary 的原文写法。
2. 不要把 glossary 中的词翻译成中文，不要做中英混写，不要加括号注释。
3. 只输出 JSON 对象，键名必须是 translations，值必须是与输入同长度的字符串数组。
4. 不要添加省略、解释、注解、品牌说明、格式说明。
5. 保持语义完整自然，但只修订翻译，不改 source。

glossary:
{self._glossary_block(glossary)}

groups:
{json.dumps(payload_groups, ensure_ascii=False)}

输出 JSON 模板：
{{
  "translations": ["修订后译文1", "修订后译文2"]
}}
"""
        result = await self._call_llm_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=min(3600, 220 * max(1, len(chunk_sources)) + 300),
        )
        translations = self._extract_translation_list(result, len(chunk_sources))
        if len(translations) != len(chunk_sources):
            raise RuntimeError(
                f"字幕全局重写返回数量不匹配: expected={len(chunk_sources)} got={len(translations)}"
            )
        cleaned: List[str] = []
        for idx, value in enumerate(translations):
            sanitized = SubtitleRepairer.sanitize_translation_text(value)
            cleaned.append(sanitized or self._normalize_spaces(chunk_targets[idx]))
        return cleaned

    async def _rewrite_metadata(
        self,
        *,
        task: Task,
        glossary: Sequence[Dict[str, Any]],
        translated_title: str,
        translated_description: str,
    ) -> Dict[str, str]:
        system_prompt = (
            "你是字幕全局审稿器。你只输出一个 JSON 对象。"
            "请修订标题和描述，不要输出任何解释。"
        )
        user_prompt = f"""请修订以下翻译元数据，目标语言是 {task.target_lang}。

硬性规则：
1. 所有 glossary 中的音乐专有名词必须保留原文。
2. 不要使用中文译名、音译或中英混写。
3. 不要输出注解、括号说明、markdown、品牌解释。
4. 只输出 JSON 对象，且只能包含 title 和 description。

glossary:
{self._glossary_block(glossary)}

source_title:
{task.source_title or ""}

current_translated_title:
{translated_title or ""}

current_translated_description:
{translated_description or ""}

输出 JSON 模板：
{{
  "title": "修订后的标题",
  "description": "修订后的描述"
}}
"""
        result = await self._call_llm_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=1200,
        )
        title = SubtitleRepairer.sanitize_translation_text(str(result.get("title") or "").strip())
        description = SubtitleRepairer.sanitize_translation_text(
            str(result.get("description") or "").strip()
        )
        return {
            "title": title or translated_title,
            "description": (description or translated_description)[:2000],
        }

    async def review(
        self,
        task: Task,
        working_dir: Path,
        *,
        groups: Sequence[SentenceGroup],
        origin_text: str,
        target_text: str,
    ) -> GlobalReviewResult:
        translated_title = str(task.translated_title or "").strip()
        translated_description = str(task.translated_description or "").strip()

        if not self.enabled:
            report = self._base_report(task=task)
            report.update(
                {
                    "status": "skipped",
                    "passed": True,
                    "skipped": True,
                    "summary": "全局审阅已禁用。",
                }
            )
            report = self._finalize_report(working_dir, report)
            return GlobalReviewResult(
                passed=True,
                skipped=True,
                fixed=False,
                domain="general",
                confidence=0.0,
                message="全局审阅已禁用",
                report=report,
                translated_title=translated_title,
                translated_description=translated_description,
                target_text=target_text,
            )

        missing_reason = self._runtime_missing_reason()
        if missing_reason:
            report = self._base_report(task=task)
            report.update(
                {
                    "status": "failed" if not self.fail_open else "skipped",
                    "passed": self.fail_open,
                    "skipped": self.fail_open,
                    "summary": missing_reason,
                    "blocking_reason": "" if self.fail_open else missing_reason,
                }
            )
            report = self._finalize_report(working_dir, report)
            return GlobalReviewResult(
                passed=self.fail_open,
                skipped=self.fail_open,
                fixed=False,
                domain="general",
                confidence=0.0,
                message=missing_reason,
                report=report,
                translated_title=translated_title,
                translated_description=translated_description,
                target_text=target_text,
                blocking_reason="" if self.fail_open else missing_reason,
            )

        origin_path = working_dir / "origin_language_srt.srt"
        target_path = working_dir / "target_language_srt.srt"
        bilingual_path = working_dir / "bilingual_srt.srt"
        target_text_path = working_dir / "target_language.txt"

        origin_entries = self._parse_srt(origin_path)
        target_entries = self._parse_srt(target_path)
        if not origin_entries or not target_entries:
            message = "缺少 origin/target 字幕，无法执行全局审阅"
            report = self._base_report(task=task)
            report.update(
                {
                    "status": "failed" if not self.fail_open else "skipped",
                    "passed": self.fail_open,
                    "skipped": self.fail_open,
                    "summary": message,
                    "blocking_reason": "" if self.fail_open else message,
                }
            )
            report = self._finalize_report(working_dir, report)
            return GlobalReviewResult(
                passed=self.fail_open,
                skipped=self.fail_open,
                fixed=False,
                domain="general",
                confidence=0.0,
                message=message,
                report=report,
                translated_title=translated_title,
                translated_description=translated_description,
                target_text=target_text,
                blocking_reason="" if self.fail_open else message,
            )

        resolved_groups = self._resolve_groups(groups, origin_entries)
        source_lines = [self._entry_text(entry) for entry in origin_entries]
        target_lines = [self._entry_text(entry) for entry in target_entries]
        source_groups = self._group_texts(source_lines, resolved_groups)
        current_target_groups = self._group_texts(target_lines, resolved_groups)
        if origin_text and not source_groups:
            source_groups = [self._normalize_spaces(origin_text)]

        try:
            detector = await self._detect_domain_and_glossary(task=task, source_groups=source_groups)
        except Exception as exc:
            message = f"全局审阅域识别失败: {exc}"
            report = self._base_report(task=task)
            report.update(
                {
                    "status": "failed" if not self.fail_open else "skipped",
                    "passed": self.fail_open,
                    "skipped": self.fail_open,
                    "summary": message,
                    "blocking_reason": "" if self.fail_open else message,
                }
            )
            report = self._finalize_report(working_dir, report)
            return GlobalReviewResult(
                passed=self.fail_open,
                skipped=self.fail_open,
                fixed=False,
                domain="general",
                confidence=0.0,
                message=message,
                report=report,
                translated_title=translated_title,
                translated_description=translated_description,
                target_text=target_text,
                blocking_reason="" if self.fail_open else message,
            )

        domain = detector["domain"]
        confidence = detector["confidence"]
        glossary = detector["glossary"]
        report = self._base_report(
            task=task,
            domain=domain,
            confidence=confidence,
            reason=detector["reason"],
        )
        report["glossary"] = glossary

        if (
            domain != "music"
            or not self.music_enabled
            or confidence < self.domain_confidence_threshold
        ):
            report.update(
                {
                    "status": "skipped",
                    "passed": True,
                    "skipped": True,
                    "summary": self._summarize(domain="general", fixed=False, issues_after=[]),
                }
            )
            report = self._finalize_report(working_dir, report)
            return GlobalReviewResult(
                passed=True,
                skipped=True,
                fixed=False,
                domain=domain,
                confidence=confidence,
                message=report["summary"],
                report=report,
                translated_title=translated_title,
                translated_description=translated_description,
                target_text=target_text,
            )

        issues_before = self._music_term_issues(
            source_groups=source_groups,
            target_groups=current_target_groups,
            glossary=glossary,
            translated_title=translated_title,
            translated_description=translated_description,
            source_title=task.source_title or "",
        )
        report["issues_before"] = issues_before

        rewritten_groups: List[str] = []
        try:
            for start in range(0, len(source_groups), self.chunk_size):
                end = start + self.chunk_size
                rewritten_groups.extend(
                    await self._rewrite_group_chunk(
                        task=task,
                        chunk_sources=source_groups[start:end],
                        chunk_targets=current_target_groups[start:end],
                        glossary=glossary,
                    )
                )
            rewritten_meta = await self._rewrite_metadata(
                task=task,
                glossary=glossary,
                translated_title=translated_title,
                translated_description=translated_description,
            )
        except Exception as exc:
            message = f"全局审阅自动修订失败: {exc}"
            report.update(
                {
                    "status": "failed" if not self.fail_open else "skipped",
                    "passed": self.fail_open,
                    "skipped": self.fail_open,
                    "summary": message,
                    "blocking_reason": "" if self.fail_open else message,
                }
            )
            report = self._finalize_report(working_dir, report)
            return GlobalReviewResult(
                passed=self.fail_open,
                skipped=self.fail_open,
                fixed=False,
                domain=domain,
                confidence=confidence,
                message=message,
                report=report,
                translated_title=translated_title,
                translated_description=translated_description,
                target_text=target_text,
                blocking_reason="" if self.fail_open else message,
            )

        if len(rewritten_groups) != len(resolved_groups):
            message = (
                f"全局审阅修订结果数量异常: expected={len(resolved_groups)} got={len(rewritten_groups)}"
            )
            report.update(
                {
                    "status": "failed",
                    "passed": False,
                    "summary": message,
                    "blocking_reason": message,
                }
            )
            report = self._finalize_report(working_dir, report)
            return GlobalReviewResult(
                passed=False,
                skipped=False,
                fixed=False,
                domain=domain,
                confidence=confidence,
                message=message,
                report=report,
                translated_title=translated_title,
                translated_description=translated_description,
                target_text=target_text,
                blocking_reason=message,
            )

        reviewed_lines = list(target_lines)
        changed_groups = 0
        for idx, (group, rewritten) in enumerate(zip(resolved_groups, rewritten_groups)):
            if self._normalize_lookup(rewritten) != self._normalize_lookup(current_target_groups[idx]):
                changed_groups += 1
            projected = SentenceRegrouper.project_translation(rewritten, group.source_lines)
            if len(projected) != len(group.cue_indexes):
                projected = list(projected[: len(group.cue_indexes)]) + [""] * max(
                    0, len(group.cue_indexes) - len(projected)
                )
            for cue_index, line in zip(group.cue_indexes, projected):
                cleaned = SubtitleRepairer.sanitize_translation_text(line)
                reviewed_lines[cue_index] = cleaned or reviewed_lines[cue_index]

        reviewed_target_entries: List[Dict[str, Any]] = []
        reviewed_bilingual_entries: List[Dict[str, Any]] = []
        for idx, entry in enumerate(origin_entries):
            reviewed_target_entries.append(
                {
                    "index": idx + 1,
                    "start": entry.get("start", "00:00:00,000"),
                    "end": entry.get("end", "00:00:01,000"),
                    "lines": [reviewed_lines[idx] or " "],
                }
            )
            reviewed_bilingual_entries.append(
                {
                    "index": idx + 1,
                    "start": entry.get("start", "00:00:00,000"),
                    "end": entry.get("end", "00:00:01,000"),
                    "lines": [reviewed_lines[idx] or " ", source_lines[idx] or " "],
                }
            )

        self._write_srt(reviewed_target_entries, target_path)
        self._write_srt(reviewed_bilingual_entries, bilingual_path)

        translated_title_new = str(rewritten_meta.get("title") or translated_title).strip()
        translated_description_new = str(
            rewritten_meta.get("description") or translated_description
        ).strip()[:2000]

        reviewed_group_texts = self._group_texts(reviewed_lines, resolved_groups)
        reviewed_target_text = (
            SentenceRegrouper.render_grouped_text(reviewed_lines, resolved_groups)
            or "\n".join(text for text in reviewed_group_texts if text).strip()
            or target_text
        )
        target_text_path.write_text(
            reviewed_target_text + ("\n" if reviewed_target_text else ""),
            encoding="utf-8",
        )

        issues_after = self._music_term_issues(
            source_groups=source_groups,
            target_groups=reviewed_group_texts,
            glossary=glossary,
            translated_title=translated_title_new,
            translated_description=translated_description_new,
            source_title=task.source_title or "",
        )
        fixed = bool(changed_groups) or (
            self._normalize_lookup(translated_title_new) != self._normalize_lookup(translated_title)
            or self._normalize_lookup(translated_description_new)
            != self._normalize_lookup(translated_description)
        )
        report["issues_after"] = issues_after
        report["fixes_applied"] = {
            "subtitle_groups_changed": changed_groups,
            "metadata_changed": bool(
                self._normalize_lookup(translated_title_new) != self._normalize_lookup(translated_title)
                or self._normalize_lookup(translated_description_new)
                != self._normalize_lookup(translated_description)
            ),
        }
        report["fixed"] = fixed
        report["summary"] = self._summarize(
            domain=domain,
            fixed=fixed,
            issues_after=issues_after,
        )
        report["status"] = "passed" if not issues_after else "failed"
        report["passed"] = not issues_after
        report["skipped"] = False
        if issues_after:
            report["blocking_reason"] = report["summary"]

        report = self._finalize_report(working_dir, report)

        return GlobalReviewResult(
            passed=not issues_after,
            skipped=False,
            fixed=fixed,
            domain=domain,
            confidence=confidence,
            message=report["summary"],
            report=report,
            translated_title=translated_title_new,
            translated_description=translated_description_new,
            target_text=reviewed_target_text,
            blocking_reason=report.get("blocking_reason", ""),
        )
