from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import Any, Dict, List

from creation.models import HighlightSegment
from creation.utils import parse_srt_file, slugify, subtitle_excerpt

logger = logging.getLogger(__name__)

try:
    import librosa  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    librosa = None

try:
    from scenedetect import ContentDetector, detect  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    ContentDetector = None
    detect = None


_BOUNDARY_CUES = (
    "first",
    "second",
    "third",
    "next",
    "finally",
    "in summary",
    "let's",
    "now",
    "接下来",
    "最后",
    "总结",
    "重点",
    "核心",
)


class HighlightDetector:
    """
    高光识别器

    P0 默认使用确定性启发式：字幕语义密度 + 场景边界 + 音频节奏。
    外部依赖缺失时自动降级，不阻断整条流水线。
    """

    def _semantic_candidates(
        self,
        entries: List[Dict[str, Any]],
        *,
        clip_count: int,
        min_duration: int,
        max_duration: int,
    ) -> List[HighlightSegment]:
        if not entries:
            return []

        total_duration = max(entry["end"] for entry in entries)
        target_duration = max(min_duration, min(max_duration, int((min_duration + max_duration) / 2)))
        step = max(8.0, target_duration / 2.5)
        candidates: List[HighlightSegment] = []
        t = 0.0
        idx = 1

        while t + min_duration <= total_duration:
            window_end = min(total_duration, t + target_duration)
            excerpt = subtitle_excerpt(entries, t, window_end)
            window_entries = [entry for entry in entries if entry["start"] < window_end and entry["end"] > t]
            text_length = len(excerpt)
            cue_hits = sum(1 for cue in _BOUNDARY_CUES if cue in excerpt.lower())
            sentence_hits = excerpt.count("。") + excerpt.count(".") + excerpt.count("!") + excerpt.count("?")
            density = text_length / max(1.0, window_end - t)
            semantic_score = min(1.0, (density / 32.0) + cue_hits * 0.08 + min(sentence_hits, 5) * 0.03)
            title = self._build_title(excerpt, idx)
            summary = excerpt[:140]
            keywords = self._extract_keywords(excerpt)

            candidates.append(
                HighlightSegment(
                    segment_id=f"seg_{idx:03d}_{slugify(title, fallback='segment')}",
                    start=max(0.0, t),
                    end=max(t + min_duration, window_end),
                    title=title,
                    summary=summary,
                    keywords=keywords,
                    transcript_excerpt=excerpt,
                    semantic_score=semantic_score,
                    source_signals={
                        "subtitle_density": round(density, 3),
                        "cue_hits": cue_hits,
                        "window_entries": len(window_entries),
                    },
                )
            )
            idx += 1
            t += step

        if not candidates and entries:
            excerpt = subtitle_excerpt(entries, 0.0, min(total_duration, max_duration))
            candidates.append(
                HighlightSegment(
                    segment_id="seg_001_full_video",
                    start=0.0,
                    end=min(total_duration, max_duration),
                    title=self._build_title(excerpt, 1),
                    summary=excerpt[:140],
                    keywords=self._extract_keywords(excerpt),
                    transcript_excerpt=excerpt,
                    semantic_score=0.5,
                )
            )

        return candidates[: max(clip_count * 5, clip_count)]

    def _detect_scene_boundaries(self, video_path: str) -> List[float]:
        if detect is None or ContentDetector is None:
            return []
        try:  # pragma: no cover - depends on optional library and media
            scenes = detect(video_path, ContentDetector())
        except Exception as exc:
            logger.warning("场景检测失败，降级跳过: %s", exc)
            return []

        boundaries: List[float] = []
        for start, end in scenes:
            try:
                boundaries.append(float(start.get_seconds()))
                boundaries.append(float(end.get_seconds()))
            except Exception:
                continue
        return sorted(set(boundaries))

    def _score_with_scenes(self, candidates: List[HighlightSegment], scene_points: List[float]):
        if not scene_points:
            for candidate in candidates:
                candidate.scene_score = 0.35
            return

        for candidate in candidates:
            distances = [
                min(abs(candidate.start - point), abs(candidate.end - point))
                for point in scene_points
            ]
            nearest = min(distances) if distances else 999.0
            candidate.scene_score = max(0.1, 1.0 - min(nearest, 12.0) / 12.0)
            candidate.source_signals["nearest_scene_boundary"] = round(nearest, 3)

    def _score_with_audio(self, candidates: List[HighlightSegment], audio_source_path: str):
        if librosa is None or not audio_source_path:
            for candidate in candidates:
                candidate.audio_score = 0.25
            return

        try:  # pragma: no cover - depends on optional library and media
            audio, sample_rate = librosa.load(audio_source_path, sr=16000)
            rms = librosa.feature.rms(y=audio)[0]
        except Exception as exc:
            logger.warning("音频特征提取失败，降级跳过: %s", exc)
            for candidate in candidates:
                candidate.audio_score = 0.25
            return

        if len(rms) == 0:
            for candidate in candidates:
                candidate.audio_score = 0.25
            return

        hop_seconds = len(audio) / max(1, len(rms)) / max(1, sample_rate)
        max_rms = max(float(value) for value in rms) or 1.0

        for candidate in candidates:
            start_idx = max(0, int(candidate.start / max(hop_seconds, 1e-6)))
            end_idx = min(len(rms), int(candidate.end / max(hop_seconds, 1e-6)) + 1)
            window = rms[start_idx:end_idx]
            if len(window) == 0:
                candidate.audio_score = 0.15
                continue
            mean_energy = sum(float(value) for value in window) / len(window)
            silence_ratio = sum(1 for value in window if float(value) < max_rms * 0.15) / len(window)
            candidate.audio_score = max(0.05, min(1.0, (mean_energy / max_rms) * 0.8 + (1.0 - silence_ratio) * 0.2))
            candidate.source_signals["silence_ratio"] = round(silence_ratio, 3)

    def _select_top_segments(
        self,
        candidates: List[HighlightSegment],
        clip_count: int,
        min_duration: int,
        max_duration: int,
        *,
        strategy: str = "hybrid",
    ) -> List[HighlightSegment]:
        strategy = str(strategy or "hybrid").strip().lower()
        for candidate in candidates:
            duration = max(1.0, candidate.duration)
            duration_penalty = 0.0
            if duration < min_duration:
                duration_penalty = 0.2
            elif duration > max_duration:
                duration_penalty = 0.15
            if strategy == "semantic":
                candidate.total_score = max(0.0, candidate.semantic_score - duration_penalty)
                continue
            candidate.total_score = max(
                0.0,
                candidate.semantic_score * 0.6
                + candidate.audio_score * 0.2
                + candidate.scene_score * 0.2
                - duration_penalty,
            )

        ranked = sorted(candidates, key=lambda item: item.total_score, reverse=True)
        selected: List[HighlightSegment] = []
        for candidate in ranked:
            if len(selected) >= clip_count:
                break
            if any(not (candidate.end <= item.start or candidate.start >= item.end) for item in selected):
                continue
            selected.append(candidate)

        return sorted(selected, key=lambda item: item.start)

    @staticmethod
    def _build_title(excerpt: str, index: int) -> str:
        cleaned = re.sub(r"\s+", " ", (excerpt or "")).strip()
        if not cleaned:
            return f"知识点片段 {index}"
        pieces = re.split(r"[。！？!?.,，；;:：]", cleaned)
        title = next((piece.strip() for piece in pieces if piece.strip()), cleaned[:24])
        return title[:30] or f"知识点片段 {index}"

    @staticmethod
    def _extract_keywords(excerpt: str) -> List[str]:
        if not excerpt:
            return []
        normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff\s]+", " ", excerpt.lower())
        tokens = [token.strip() for token in normalized.split() if len(token.strip()) >= 2]
        if not tokens:
            compact = re.sub(r"\s+", "", excerpt)
            return [compact[:6]] if compact else []
        counts = Counter(tokens)
        return [token for token, _ in counts.most_common(3)]

    async def detect(
        self,
        video_path: str,
        subtitle_path: str,
        *,
        clip_count: int = 5,
        min_duration: int = 30,
        max_duration: int = 180,
        audio_source_path: str = "",
        strategy: str = "hybrid",
    ) -> List[HighlightSegment]:
        entries = parse_srt_file(subtitle_path)
        if not entries:
            return []

        candidates = self._semantic_candidates(
            entries,
            clip_count=clip_count,
            min_duration=min_duration,
            max_duration=max_duration,
        )
        if not candidates:
            return []

        strategy = str(strategy or "hybrid").strip().lower()
        if strategy == "semantic":
            for candidate in candidates:
                candidate.scene_score = 0.0
                candidate.audio_score = 0.0
        else:
            scene_points = self._detect_scene_boundaries(video_path)
            self._score_with_scenes(candidates, scene_points)
            self._score_with_audio(candidates, audio_source_path)
        selected = self._select_top_segments(
            candidates,
            clip_count,
            min_duration,
            max_duration,
            strategy=strategy,
        )

        logger.info(
            "🎯 高光识别完成: strategy=%s candidates=%s selected=%s audio_source=%s",
            strategy,
            len(candidates),
            len(selected),
            audio_source_path or "none",
        )
        return selected
