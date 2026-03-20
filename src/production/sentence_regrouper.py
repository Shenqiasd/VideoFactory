"""
字幕句子级重组翻译。

目标：把自动字幕的碎片 cue 临时合并为更完整的句子/子句做翻译，
再按原 cue 数量投影回字幕行，降低逐行碎片翻译带来的语义断裂。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Sequence

from core.config import Config


_LATIN_WORD_PATTERN = re.compile(r"[A-Za-z0-9']+")
_TERMINAL_PUNCT_PATTERN = re.compile(r"[.!?。！？；;:：]$")
_BOUNDARY_CHARS = set("，,。！？；;：:、 ")
_OPENING_PUNCT = "《“‘([【（"
_CLOSING_PUNCT = "》”’)]】）"
_STRONG_BREAK_CHARS = set("。！？.!?；;：:》”’)]】）")
_MEDIUM_BREAK_CHARS = set("，,、")
_FUNCTION_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "but",
    "by",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}
_PAIR_MAP = {
    "《": "》",
    "“": "”",
    "‘": "’",
    "(": ")",
    "（": "）",
    "[": "]",
    "【": "】",
}
_NUMBER_UNIT_PATTERN = re.compile(
    r"(?:\d+(?:\.\d+)?\s*(?:mm|cm|kg|g|%|fps|hz|khz|mhz|gb|mb|tb))|(?:v\d+(?:\.\d+)+)",
    re.IGNORECASE,
)


@dataclass
class ProtectedSpan:
    start: int
    end: int
    kind: str


@dataclass
class BoundaryFeature:
    index: int
    strong_break: bool
    medium_break: bool
    weak_break: bool
    inside_protected_span: bool
    after_closing_punct: bool
    before_opening_punct: bool
    ends_with_function_word: bool
    starts_with_function_word: bool


@dataclass
class SentenceGroup:
    cue_indexes: List[int]
    source_lines: List[str]
    source_text: str


@dataclass
class SentenceTranslationResult:
    cue_lines: List[str]
    groups: List[SentenceGroup]


class SentenceRegrouper:
    """句子级重组 + 翻译回填。"""

    def __init__(self):
        cfg = Config()
        self.max_cues_per_group = int(
            cfg.get("quality", "sentence_regroup_max_cues", default=4)
        )
        self.target_words_per_group = int(
            cfg.get("quality", "sentence_regroup_target_words", default=30)
        )
        self.max_words_per_group = int(
            cfg.get("quality", "sentence_regroup_max_words", default=32)
        )
        self.max_chars_per_group = int(
            cfg.get("quality", "sentence_regroup_max_chars", default=160)
        )
        self.max_pause_seconds = float(
            cfg.get("quality", "sentence_regroup_max_pause_seconds", default=1.2)
        )

    @staticmethod
    def _entry_text(entry: Dict[str, Any]) -> str:
        lines = entry.get("lines") or []
        return " ".join(str(line).strip() for line in lines if str(line).strip()).strip()

    @staticmethod
    def _word_count(text: str) -> int:
        return len(_LATIN_WORD_PATTERN.findall(str(text or "")))

    @staticmethod
    def _compact_length(text: str) -> int:
        return len(re.sub(r"\s+", "", str(text or "")))

    @staticmethod
    def _ends_with_terminal(text: str) -> bool:
        return bool(_TERMINAL_PUNCT_PATTERN.search(str(text or "").strip()))

    @staticmethod
    def _parse_srt_seconds(raw: str) -> float:
        text = str(raw or "").strip()
        if not text:
            return 0.0
        hh, mm, rest = text.split(":")
        ss, ms = rest.split(",")
        return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0

    def _pause_seconds(self, left: Dict[str, Any], right: Dict[str, Any]) -> float:
        return self._parse_srt_seconds(right.get("start", "")) - self._parse_srt_seconds(
            left.get("end", "")
        )

    def group_entries(self, entries: Sequence[Dict[str, Any]]) -> List[SentenceGroup]:
        groups: List[SentenceGroup] = []
        total = len(entries)
        idx = 0

        while idx < total:
            current_indexes = [idx]
            current_lines = [self._entry_text(entries[idx])]
            total_words = self._word_count(current_lines[0])
            total_chars = self._compact_length(current_lines[0])

            while current_indexes[-1] + 1 < total:
                current_idx = current_indexes[-1]
                next_idx = current_idx + 1
                if self._pause_seconds(entries[current_idx], entries[next_idx]) > self.max_pause_seconds:
                    break
                if len(current_indexes) >= self.max_cues_per_group:
                    break
                if self._ends_with_terminal(current_lines[-1]) and total_words >= 6:
                    break
                if len(current_indexes) >= 2 and total_words >= self.target_words_per_group:
                    break
                if total_words >= self.max_words_per_group or total_chars >= self.max_chars_per_group:
                    break

                next_line = self._entry_text(entries[next_idx])
                current_indexes.append(next_idx)
                current_lines.append(next_line)
                total_words += self._word_count(next_line)
                total_chars += self._compact_length(next_line)

            source_text = " ".join(line for line in current_lines if line).strip()
            groups.append(
                SentenceGroup(
                    cue_indexes=current_indexes,
                    source_lines=current_lines,
                    source_text=source_text,
                )
            )
            idx = current_indexes[-1] + 1

        return groups

    @staticmethod
    def _normalize_translation_text(text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "").strip())

    @classmethod
    def _source_weights(cls, lines: Sequence[str]) -> List[int]:
        weights: List[int] = []
        for line in lines:
            compact = cls._compact_length(line)
            words = cls._word_count(line)
            weights.append(max(1, compact + words * 2))
        return weights

    @staticmethod
    def _last_latin_word(text: str) -> str:
        matches = list(_LATIN_WORD_PATTERN.finditer(str(text or "")))
        if not matches:
            return ""
        return matches[-1].group(0).lower()

    @staticmethod
    def _first_latin_word(text: str) -> str:
        match = _LATIN_WORD_PATTERN.search(str(text or ""))
        if not match:
            return ""
        return match.group(0).lower()

    @classmethod
    def _scan_paired_spans(cls, text: str) -> List[ProtectedSpan]:
        spans: List[ProtectedSpan] = []
        stack: List[tuple[str, int]] = []
        closing_to_open = {closing: opening for opening, closing in _PAIR_MAP.items()}

        for index, char in enumerate(text):
            if char in _PAIR_MAP:
                stack.append((char, index))
                continue
            opening = closing_to_open.get(char)
            if not opening:
                continue
            for stack_index in range(len(stack) - 1, -1, -1):
                open_char, start = stack[stack_index]
                if open_char != opening:
                    continue
                spans.append(ProtectedSpan(start=start, end=index + 1, kind="paired"))
                del stack[stack_index:]
                break

        return spans

    @classmethod
    def _scan_english_phrase_spans(cls, text: str) -> List[ProtectedSpan]:
        spans: List[ProtectedSpan] = []
        matches = list(_LATIN_WORD_PATTERN.finditer(text))
        if len(matches) < 2:
            return spans

        run_start = matches[0].start()
        run_end = matches[0].end()
        run_count = 1

        for match in matches[1:]:
            gap = text[run_end:match.start()]
            if gap and re.fullmatch(r"[\s'\-–—]+", gap):
                run_end = match.end()
                run_count += 1
                continue
            if run_count >= 2:
                spans.append(ProtectedSpan(start=run_start, end=run_end, kind="english_phrase"))
            run_start = match.start()
            run_end = match.end()
            run_count = 1

        if run_count >= 2:
            spans.append(ProtectedSpan(start=run_start, end=run_end, kind="english_phrase"))

        return spans

    @classmethod
    def _scan_number_unit_spans(cls, text: str) -> List[ProtectedSpan]:
        return [
            ProtectedSpan(start=match.start(), end=match.end(), kind="number_unit")
            for match in _NUMBER_UNIT_PATTERN.finditer(text)
        ]

    @classmethod
    def _protected_spans(cls, text: str) -> List[ProtectedSpan]:
        spans = cls._scan_paired_spans(text)
        spans.extend(cls._scan_english_phrase_spans(text))
        spans.extend(cls._scan_number_unit_spans(text))
        spans.sort(key=lambda span: (span.start, span.end))
        return spans

    @classmethod
    def _is_inside_protected_span(cls, index: int, spans: Sequence[ProtectedSpan]) -> bool:
        return any(span.start < index < span.end for span in spans)

    @classmethod
    def _boundary_feature(
        cls,
        text: str,
        index: int,
        spans: Sequence[ProtectedSpan],
    ) -> BoundaryFeature:
        prev = text[index - 1] if index > 0 else ""
        next_char = text[index] if index < len(text) else ""
        left_text = text[:index]
        right_text = text[index:]
        return BoundaryFeature(
            index=index,
            strong_break=prev in _STRONG_BREAK_CHARS,
            medium_break=prev in _MEDIUM_BREAK_CHARS,
            weak_break=(prev in _BOUNDARY_CHARS or prev.isspace() or next_char.isspace()),
            inside_protected_span=cls._is_inside_protected_span(index, spans),
            after_closing_punct=prev in _CLOSING_PUNCT,
            before_opening_punct=next_char in _OPENING_PUNCT,
            ends_with_function_word=cls._last_latin_word(left_text) in _FUNCTION_WORDS,
            starts_with_function_word=cls._first_latin_word(right_text) in _FUNCTION_WORDS,
        )

    @classmethod
    def _boundary_penalty(
        cls,
        text: str,
        index: int,
        spans: Sequence[ProtectedSpan],
    ) -> float:
        if index <= 0 or index >= len(text):
            return 0.0

        feature = cls._boundary_feature(text, index, spans)
        if feature.inside_protected_span:
            return 1000.0

        penalty = 0.0
        if feature.strong_break:
            penalty += 0.0
        elif feature.medium_break:
            penalty += 2.0
        elif feature.weak_break:
            penalty += 6.0
        else:
            penalty += 14.0

        if feature.after_closing_punct:
            penalty -= 2.0
        if feature.before_opening_punct:
            penalty += 12.0
        if feature.ends_with_function_word:
            penalty += 35.0
        if feature.starts_with_function_word:
            penalty += 20.0

        return penalty

    @classmethod
    def _segment_penalty(cls, segment: str) -> float:
        stripped = str(segment or "").strip()
        if not stripped:
            return 1000.0

        penalty = 0.0
        compact = cls._compact_length(stripped)
        latin_words = _LATIN_WORD_PATTERN.findall(stripped)
        if compact <= 2:
            penalty += 80.0
        elif compact <= 3:
            penalty += 32.0

        if len(latin_words) == 1 and latin_words[0].lower() in _FUNCTION_WORDS:
            penalty += 120.0

        if all(char in _CLOSING_PUNCT for char in stripped):
            penalty += 120.0

        if stripped[0] in _CLOSING_PUNCT and compact <= 4:
            penalty += 60.0
        if stripped[-1] in _OPENING_PUNCT and compact <= 6:
            penalty += 60.0

        for opening, closing in _PAIR_MAP.items():
            if stripped.count(opening) != stripped.count(closing):
                penalty += 70.0

        return penalty

    @classmethod
    def _local_segment_cost(
        cls,
        text: str,
        left: int,
        right: int,
        *,
        spans: Sequence[ProtectedSpan],
        expected_length: float,
        min_segment_chars: int,
        max_segment_chars: int,
    ) -> float:
        segment = text[left:right].strip()
        actual_length = cls._compact_length(segment)
        if actual_length <= 0:
            return 1000.0

        cost = abs(actual_length - expected_length) * 1.4
        if actual_length < min_segment_chars:
            cost += (min_segment_chars - actual_length) * 24.0
        if actual_length > max_segment_chars:
            cost += (actual_length - max_segment_chars) * 18.0

        cost += cls._segment_penalty(segment)
        cost += cls._boundary_penalty(text, right, spans)
        return cost

    @classmethod
    def _rebalance_cuts(
        cls,
        text: str,
        cuts: List[int],
        *,
        spans: Sequence[ProtectedSpan],
        weights: Sequence[int],
    ) -> List[int]:
        if len(cuts) <= 2:
            return cuts

        total_length = max(1, cls._compact_length(text))
        total_weight = sum(weights) or len(weights)
        min_segment_chars = 4 if total_length >= len(weights) * 4 else 1
        max_segment_chars = max(min_segment_chars + 2, int(total_length * 0.7))

        rebalanced = list(cuts)
        for boundary_index in range(1, len(rebalanced) - 1):
            left_edge = rebalanced[boundary_index - 1]
            current = rebalanced[boundary_index]
            right_edge = rebalanced[boundary_index + 1]
            left_segment = text[left_edge:current].strip()
            right_segment = text[current:right_edge].strip()
            if not left_segment or not right_segment:
                continue
            if cls._segment_penalty(right_segment) < 60 and cls._segment_penalty(left_segment) < 60:
                continue

            left_expected = total_length * weights[boundary_index - 1] / total_weight
            right_expected = total_length * weights[boundary_index] / total_weight
            best_cut = current
            best_cost = cls._local_segment_cost(
                text,
                left_edge,
                current,
                spans=spans,
                expected_length=left_expected,
                min_segment_chars=min_segment_chars,
                max_segment_chars=max_segment_chars,
            ) + cls._local_segment_cost(
                text,
                current,
                right_edge,
                spans=spans,
                expected_length=right_expected,
                min_segment_chars=min_segment_chars,
                max_segment_chars=max_segment_chars,
            )

            for candidate in range(left_edge + 1, right_edge):
                if cls._is_inside_protected_span(candidate, spans):
                    continue
                candidate_cost = cls._local_segment_cost(
                    text,
                    left_edge,
                    candidate,
                    spans=spans,
                    expected_length=left_expected,
                    min_segment_chars=min_segment_chars,
                    max_segment_chars=max_segment_chars,
                ) + cls._local_segment_cost(
                    text,
                    candidate,
                    right_edge,
                    spans=spans,
                    expected_length=right_expected,
                    min_segment_chars=min_segment_chars,
                    max_segment_chars=max_segment_chars,
                )
                if candidate_cost < best_cost:
                    best_cost = candidate_cost
                    best_cut = candidate

            rebalanced[boundary_index] = best_cut

        return rebalanced

    @classmethod
    def project_translation(
        cls,
        translated_text: str,
        source_lines: Sequence[str],
    ) -> List[str]:
        text = cls._normalize_translation_text(translated_text)
        count = len(source_lines)
        if count <= 0:
            return []
        if count == 1:
            return [text]
        if not text:
            return [""] * count

        weights = cls._source_weights(source_lines)
        total_weight = sum(weights) or count
        total_length = len(text)
        compact_total_length = max(1, cls._compact_length(text))
        min_segment_chars = 4 if compact_total_length >= count * 4 else 1
        max_segment_chars = max(min_segment_chars + 2, int(compact_total_length * 0.72))
        spans = cls._protected_spans(text)

        dp: List[List[float]] = [[float("inf")] * (count + 1) for _ in range(total_length + 1)]
        prev: List[List[int]] = [[-1] * (count + 1) for _ in range(total_length + 1)]
        dp[0][0] = 0.0

        for end in range(1, total_length + 1):
            for part in range(1, count + 1):
                remaining_parts = count - part
                if total_length - end < remaining_parts:
                    continue
                expected_length = compact_total_length * weights[part - 1] / total_weight
                for start in range(part - 1, end):
                    if dp[start][part - 1] == float("inf"):
                        continue
                    if total_length - start < count - part + 1:
                        continue
                    if cls._is_inside_protected_span(end, spans):
                        continue
                    segment = text[start:end].strip()
                    if not segment:
                        continue
                    segment_cost = cls._local_segment_cost(
                        text,
                        start,
                        end,
                        spans=spans,
                        expected_length=expected_length,
                        min_segment_chars=min_segment_chars,
                        max_segment_chars=max_segment_chars,
                    )
                    total_cost = dp[start][part - 1] + segment_cost
                    if total_cost < dp[end][part]:
                        dp[end][part] = total_cost
                        prev[end][part] = start

        if dp[total_length][count] == float("inf"):
            equal_span = total_length / count
            rough_cuts = [0]
            for part in range(1, count):
                rough_cuts.append(int(round(equal_span * part)))
            rough_cuts.append(total_length)
            final_cuts = rough_cuts
        else:
            final_cuts = [total_length]
            cursor = total_length
            current_part = count
            while current_part > 0 and cursor >= 0:
                start = prev[cursor][current_part]
                if start < 0:
                    break
                final_cuts.append(start)
                cursor = start
                current_part -= 1
            final_cuts = sorted(set(final_cuts + [0]))
            if len(final_cuts) != count + 1:
                equal_span = total_length / count
                final_cuts = [0] + [int(round(equal_span * part)) for part in range(1, count)] + [total_length]

        final_cuts = cls._rebalance_cuts(text, final_cuts, spans=spans, weights=weights)

        parts: List[str] = []
        for left, right in zip(final_cuts, final_cuts[1:]):
            parts.append(text[left:right].strip())

        if len(parts) != count:
            parts = parts[:count] + [""] * max(0, count - len(parts))

        return parts

    async def translate_entries(
        self,
        entries: Sequence[Dict[str, Any]],
        *,
        target_lang: str,
        source_lang: str,
        translate_lines: Callable[[Sequence[str], str, str], Awaitable[List[str]]],
    ) -> SentenceTranslationResult:
        groups = self.group_entries(entries)
        source_texts = [group.source_text for group in groups]
        translated_groups = await translate_lines(source_texts, target_lang, source_lang)

        if len(translated_groups) != len(groups):
            translated_groups = list(translated_groups[: len(groups)]) + source_texts[len(translated_groups) :]

        cue_lines = [self._entry_text(entry) for entry in entries]
        for group, translated_text in zip(groups, translated_groups):
            projected = self.project_translation(translated_text, group.source_lines)
            if len(projected) != len(group.cue_indexes):
                projected = list(projected[: len(group.cue_indexes)]) + list(
                    group.source_lines[len(projected) :]
                )
            for cue_index, text in zip(group.cue_indexes, projected):
                cue_lines[cue_index] = text or cue_lines[cue_index]

        return SentenceTranslationResult(cue_lines=cue_lines, groups=groups)

    @staticmethod
    def render_grouped_text(lines: Sequence[str], groups: Sequence[SentenceGroup]) -> str:
        chunks: List[str] = []
        for group in groups:
            merged = " ".join(
                str(lines[idx] or "").strip()
                for idx in group.cue_indexes
                if 0 <= idx < len(lines) and str(lines[idx] or "").strip()
            ).strip()
            if merged:
                chunks.append(merged)
        return "\n".join(chunks).strip()
