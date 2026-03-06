"""
字幕补翻与质量校验。
用于修复 KlicStudio 产出的 target_language_srt 仍为源语言的问题。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

from core.config import Config
from translation import get_translator

logger = logging.getLogger(__name__)


_SRT_BLOCK_PATTERN = re.compile(
    r"(\d+)\s*\n"
    r"([0-9:,]+)\s*-->\s*([0-9:,]+)\s*\n"
    r"(.*?)(?=\n\s*\n\d+\s*\n|\Z)",
    re.S,
)


def _parse_srt(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []

    content = path.read_text(encoding="utf-8", errors="ignore").replace("\r\n", "\n")
    entries: List[Dict[str, Any]] = []

    for match in _SRT_BLOCK_PATTERN.finditer(content):
        index = int(match.group(1))
        start = match.group(2).strip()
        end = match.group(3).strip()
        text_lines = [line.strip() for line in match.group(4).strip().split("\n") if line.strip()]
        entries.append(
            {
                "index": index,
                "start": start,
                "end": end,
                "lines": text_lines,
            }
        )

    if entries:
        return entries

    # 兼容极端格式：按双换行分块兜底
    chunks = [c.strip() for c in content.split("\n\n") if c.strip()]
    for i, chunk in enumerate(chunks, start=1):
        lines = [l for l in chunk.split("\n") if l.strip()]
        if len(lines) < 2 or "-->" not in lines[1]:
            continue
        ts = lines[1].split("-->")
        if len(ts) != 2:
            continue
        entries.append(
            {
                "index": i,
                "start": ts[0].strip(),
                "end": ts[1].strip(),
                "lines": [l.strip() for l in lines[2:] if l.strip()],
            }
        )
    return entries


def _write_srt(entries: Sequence[Dict[str, Any]], path: Path):
    blocks: List[str] = []
    for idx, entry in enumerate(entries, start=1):
        lines = entry.get("lines") or [""]
        text = "\n".join(lines).strip()
        if not text:
            text = " "
        blocks.append(
            f"{idx}\n{entry.get('start', '00:00:00,000')} --> {entry.get('end', '00:00:00,000')}\n{text}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def _normalize_text(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"[\W_]+", "", t, flags=re.UNICODE)
    return t


def _count_zh_chars(text: str) -> int:
    return sum(1 for ch in (text or "") if "\u4e00" <= ch <= "\u9fff")


def _line_text(entry: Dict[str, Any], prefer: str = "first") -> str:
    lines = entry.get("lines") or []
    if not lines:
        return ""
    if prefer == "all":
        return " ".join(lines).strip()
    return lines[0].strip()


@dataclass
class RepairResult:
    passed: bool
    repaired: bool
    total_lines: int
    repaired_lines: int
    zh_line_ratio: float
    unchanged_ratio: float
    message: str


class SubtitleRepairer:
    """补翻器：发现未翻译行后调用 LLM 修复。"""

    def __init__(self):
        cfg = Config()
        translator = get_translator(cfg)
        translator_cfg = translator.runtime_config()

        self.translation_provider = translator_cfg.provider
        self.api_base = translator_cfg.base_url
        self.api_key = translator_cfg.api_key
        self.model = translator_cfg.model

        self.min_zh_line_ratio = float(
            cfg.get("quality", "translation_min_zh_line_ratio", default=0.85)
        )
        self.max_unchanged_ratio = float(
            cfg.get("quality", "translation_max_unchanged_ratio", default=0.15)
        )
        self.batch_size = int(cfg.get("quality", "translation_repair_batch_size", default=40))
        self.max_retries = int(cfg.get("quality", "translation_repair_max_retries", default=2))
        self.max_effective_batch_size = int(
            cfg.get("quality", "translation_repair_max_effective_batch_size", default=20)
        )
        self.strict_json = bool(
            cfg.get("translation", "strict_json", default=cfg.get("llm", "strict_json", default=False))
        )

        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60)
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def translate_lines(self, texts: Sequence[str], target_lang: str) -> List[str]:
        """
        对外暴露的批量翻译接口（复用补翻同一套鲁棒逻辑）。

        Args:
            texts: 待翻译文本列表。
            target_lang: 目标语言代码。

        Returns:
            List[str]: 翻译结果，长度与输入一致。
        """
        items = [str(t or "").strip() for t in texts]
        if not items:
            return []

        # 分批调用，复用 _translate_batch 的解析/429 回退能力
        batch_size = max(1, int(self.max_effective_batch_size or 20))
        out: List[str] = []
        for i in range(0, len(items), batch_size):
            chunk = items[i : i + batch_size]
            translated = await self._translate_batch(chunk, target_lang)
            if len(translated) != len(chunk):
                translated = list(translated[: len(chunk)]) + list(chunk[len(translated) :])
            out.extend(translated)

        # 最终兜底：长度对齐
        if len(out) != len(items):
            out = out[: len(items)] + items[len(out) :]
        return out

    def _evaluate_pairs(
        self,
        origin_lines: Sequence[str],
        target_lines: Sequence[str],
        *,
        target_lang: str,
    ) -> Tuple[float, float, List[int]]:
        total = min(len(origin_lines), len(target_lines))
        if total <= 0:
            return 0.0, 1.0, []

        unchanged = 0
        zh_ok = 0
        need_repair: List[int] = []

        for i in range(total):
            o = (origin_lines[i] or "").strip()
            t = (target_lines[i] or "").strip()

            same = bool(o and t and _normalize_text(o) == _normalize_text(t))
            if same:
                unchanged += 1

            has_zh = _count_zh_chars(t) > 0
            if target_lang.startswith("zh"):
                if has_zh:
                    zh_ok += 1
                if same or not has_zh:
                    need_repair.append(i)
            else:
                if t:
                    zh_ok += 1

        zh_ratio = zh_ok / total if total else 0.0
        unchanged_ratio = unchanged / total if total else 1.0
        return zh_ratio, unchanged_ratio, need_repair

    async def _translate_batch(self, texts: Sequence[str], target_lang: str) -> List[str]:
        if not texts:
            return []

        # 无可用 key 时直接回原文，后续由阈值拦截失败
        if not self.api_key:
            logger.warning("LLM API key 为空，无法执行补翻")
            return list(texts)

        prompt = (
            "你是字幕翻译器。请把下面 JSON 数组中的每一项翻译成目标语言，"
            "保持语义完整，输出必须是同长度 JSON 字符串数组，不要任何额外文本。\n"
            f"目标语言: {target_lang}\n"
            f"输入: {json.dumps(list(texts), ensure_ascii=False)}"
        )

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "你只输出合法 JSON 数组，不输出 markdown。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": min(4000, 100 * max(1, len(texts))),
        }
        if self.strict_json:
            payload["response_format"] = {"type": "json_object"}

        client = await self._get_client()
        last_error = ""

        for attempt in range(self.max_retries + 1):
            try:
                resp = await client.post(
                    f"{self.api_base.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )

                # 兼容不支持 response_format 的网关
                if resp.status_code >= 400 and payload.get("response_format"):
                    payload.pop("response_format", None)
                    resp = await client.post(
                        f"{self.api_base.rstrip('/')}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )

                if resp.status_code == 429:
                    wait_s = self._extract_retry_seconds(resp.text, default=2.0 + attempt * 1.5)
                    last_error = f"HTTP 429, backoff {wait_s:.1f}s"
                    await asyncio.sleep(wait_s)
                    continue

                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}"
                    continue

                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if isinstance(content, list):
                    content = "\n".join(block.get("text", "") for block in content if isinstance(block, dict)).strip()
                parsed = self._parse_translation_response(str(content), expected=len(texts))
                if parsed:
                    # 容忍返回数量不一致：已解析部分尽量保留，缺失回退原文。
                    if len(parsed) != len(texts):
                        logger.warning(
                            "字幕补翻返回数量不匹配: expected=%s got=%s，缺失部分回退原文",
                            len(texts),
                            len(parsed),
                        )
                        parsed = list(parsed)[:len(texts)] + list(texts[len(parsed):])
                    return [str(x).strip() for x in parsed[:len(texts)]]
                last_error = "响应解析失败"

            except Exception as e:  # pragma: no cover
                last_error = str(e)

        # 大批次在解析失败时自动拆分，避免整批回退为原文。
        if len(texts) > 1:
            mid = len(texts) // 2
            left = await self._translate_batch(texts[:mid], target_lang)
            right = await self._translate_batch(texts[mid:], target_lang)
            return left + right

        logger.warning("字幕补翻请求失败，回退原文: %s", last_error)
        return list(texts)

    @staticmethod
    def _extract_retry_seconds(message: str, default: float = 2.0) -> float:
        m = re.search(r"try again in (\d+(?:\.\d+)?)s", message or "", flags=re.IGNORECASE)
        if m:
            try:
                return max(0.5, float(m.group(1)) + 0.3)
            except ValueError:
                pass
        return max(0.5, float(default))

    @staticmethod
    def _line_needs_repair(origin: str, target: str, target_lang: str) -> bool:
        t = (target or "").strip()
        if not t:
            return True
        if not target_lang.startswith("zh"):
            return False
        same = bool(origin and t and _normalize_text(origin) == _normalize_text(t))
        has_zh = _count_zh_chars(t) > 0
        return same or (not has_zh)

    @classmethod
    def _parse_translation_response(cls, raw: str, expected: int) -> Optional[List[str]]:
        text = raw.strip()

        # 常见 markdown 包裹清理
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n", "", text)
            text = re.sub(r"\n```$", "", text)

        # 直接数组
        try:
            obj = json.loads(text)
            if isinstance(obj, list):
                arr = [str(x).strip() for x in obj]
                if arr:
                    return arr
            if isinstance(obj, dict):
                cand = obj.get("translations") or obj.get("items") or obj.get("result")
                if isinstance(cand, list):
                    arr = [str(x).strip() for x in cand]
                    if arr:
                        return arr
                # 常见变体: {"0":"...", "1":"..."} / {"a":[...]} / {"data":"[...]"}
                if obj and all(str(k).isdigit() for k in obj.keys()):
                    arr = [str(obj[k]).strip() for k in sorted(obj.keys(), key=lambda x: int(x))]
                    if arr:
                        return arr
                list_values = [v for v in obj.values() if isinstance(v, list)]
                if list_values:
                    best = max(list_values, key=len)
                    arr = [str(x).strip() for x in best]
                    if arr:
                        return arr
                for value in obj.values():
                    if isinstance(value, str):
                        nested = cls._parse_translation_response(value, expected=expected)
                        if nested:
                            return nested
        except Exception:
            pass

        # 正则提取第一个 JSON 数组
        m = re.search(r"\[[\s\S]*\]", text)
        if m:
            try:
                arr = json.loads(m.group(0))
                if isinstance(arr, list) and arr:
                    return [str(x).strip() for x in arr]
            except Exception:
                return None

        return None

    async def repair_if_needed(self, task, working_dir: Path) -> RepairResult:
        target_lang = getattr(task, "target_lang", "") or ""
        if not target_lang.startswith("zh"):
            return RepairResult(
                passed=True,
                repaired=False,
                total_lines=0,
                repaired_lines=0,
                zh_line_ratio=1.0,
                unchanged_ratio=0.0,
                message="非中文目标语言，跳过补翻",
            )

        origin_path = working_dir / "origin_language_srt.srt"
        target_path = working_dir / "target_language_srt.srt"
        bilingual_path = working_dir / "bilingual_srt.srt"

        origin_entries = _parse_srt(origin_path)
        target_entries = _parse_srt(target_path)

        # 回退：若 target 丢失，尝试从 bilingual 第一行提取目标字幕
        if not target_entries and bilingual_path.exists():
            bilingual_entries = _parse_srt(bilingual_path)
            target_entries = [
                {
                    "index": e.get("index", i + 1),
                    "start": e.get("start", "00:00:00,000"),
                    "end": e.get("end", "00:00:00,000"),
                    "lines": [e.get("lines", [""])[0]] if e.get("lines") else [""],
                }
                for i, e in enumerate(bilingual_entries)
            ]

        if not origin_entries or not target_entries:
            return RepairResult(
                passed=False,
                repaired=False,
                total_lines=0,
                repaired_lines=0,
                zh_line_ratio=0.0,
                unchanged_ratio=1.0,
                message="缺少 origin/target 字幕文件，无法补翻",
            )

        total = min(len(origin_entries), len(target_entries))
        origin_entries = origin_entries[:total]
        target_entries = target_entries[:total]

        origin_lines = [_line_text(e, prefer="all") for e in origin_entries]
        target_lines = [_line_text(e, prefer="all") for e in target_entries]

        zh_ratio, unchanged_ratio, need_repair = self._evaluate_pairs(
            origin_lines,
            target_lines,
            target_lang=target_lang,
        )

        repaired = False
        repaired_lines = 0

        if need_repair:
            repaired = True
            effective_batch_size = max(1, min(self.batch_size, self.max_effective_batch_size))
            for start in range(0, len(need_repair), effective_batch_size):
                batch_indexes = need_repair[start:start + effective_batch_size]
                batch_texts = [origin_lines[i] for i in batch_indexes]
                translated = await self._translate_batch(batch_texts, target_lang)

                for idx, translated_text in zip(batch_indexes, translated):
                    cleaned = (translated_text or "").strip()
                    if cleaned:
                        target_lines[idx] = cleaned
                        if not self._line_needs_repair(origin_lines[idx], cleaned, target_lang):
                            repaired_lines += 1

            # 写回 target 字幕
            for i, entry in enumerate(target_entries):
                entry["lines"] = [target_lines[i]]
            _write_srt(target_entries, target_path)

            # 生成双语字幕：中文在上，原文在下
            bilingual_entries: List[Dict[str, Any]] = []
            for i in range(total):
                bilingual_entries.append(
                    {
                        "index": i + 1,
                        "start": origin_entries[i].get("start", target_entries[i].get("start")),
                        "end": origin_entries[i].get("end", target_entries[i].get("end")),
                        "lines": [target_lines[i], origin_lines[i]],
                    }
                )
            _write_srt(bilingual_entries, bilingual_path)

        # 复检
        zh_ratio2, unchanged_ratio2, _ = self._evaluate_pairs(
            origin_lines,
            target_lines,
            target_lang=target_lang,
        )

        passed = (
            zh_ratio2 >= self.min_zh_line_ratio
            and unchanged_ratio2 <= self.max_unchanged_ratio
        )

        if passed:
            message = (
                f"字幕通过: zh_ratio={zh_ratio2:.2%}, unchanged={unchanged_ratio2:.2%}, "
                f"repaired_lines={repaired_lines}"
            )
        else:
            message = (
                f"字幕未达标: zh_ratio={zh_ratio2:.2%} (<{self.min_zh_line_ratio:.0%}) 或 "
                f"unchanged={unchanged_ratio2:.2%} (>{self.max_unchanged_ratio:.0%})"
            )

        return RepairResult(
            passed=passed,
            repaired=repaired,
            total_lines=total,
            repaired_lines=repaired_lines,
            zh_line_ratio=zh_ratio2,
            unchanged_ratio=unchanged_ratio2,
            message=message,
        )
