from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.subtitle_style import normalize_subtitle_style
from core.task import Task, normalize_creation_config
from creation.audio_mixer import AudioMixer
from creation.clip_extractor import ClipExtractor
from creation.highlight_detector import HighlightDetector
from creation.intro_outro import IntroOutroComposer
from creation.models import CreationResult, HighlightSegment, RenderVariant
from creation.smart_cropper import SmartCropper
from creation.subject_detector import SubjectDetector
from creation.subtitle_renderer import SubtitleRenderer
from creation.transitions import TransitionComposer
from factory.long_video import LongVideoProcessor
from factory.short_clips import ShortClipExtractor

logger = logging.getLogger(__name__)


class CreationPipeline:
    """编排 AI 切片、智能裁剪与平台成片。"""

    def __init__(
        self,
        *,
        task_store=None,
        ffmpeg_path: str = "ffmpeg",
        fallback_short_clips: Optional[ShortClipExtractor] = None,
    ):
        self.task_store = task_store
        self.ffmpeg = ffmpeg_path
        self.highlight_detector = HighlightDetector()
        self.clip_extractor = ClipExtractor(ffmpeg_path=ffmpeg_path)
        self.subject_detector = SubjectDetector()
        self.smart_cropper = SmartCropper(ffmpeg_path=ffmpeg_path)
        self.subtitle_renderer = SubtitleRenderer(ffmpeg_path=ffmpeg_path)
        self.audio_mixer = AudioMixer(ffmpeg_path=ffmpeg_path)
        self.intro_outro = IntroOutroComposer(ffmpeg_path=ffmpeg_path)
        self.transitions = TransitionComposer(ffmpeg_path=ffmpeg_path)
        self.long_video = LongVideoProcessor(ffmpeg_path=ffmpeg_path)
        self.legacy_short_clips = fallback_short_clips or ShortClipExtractor(ffmpeg_path=ffmpeg_path)

    def _persist(self, task: Task):
        if self.task_store is not None:
            self.task_store.update(task)

    def _resolve_audio_source(self, task: Task, video_path: str) -> str:
        config = normalize_creation_config(task.creation_config, enable_short_clips=task.enable_short_clips)
        source = str(config.get("audio_signal_source", "dubbed_audio")).strip().lower()
        if source == "none":
            return ""
        if source == "source_audio":
            if task.source_local_path and os.path.exists(task.source_local_path):
                return task.source_local_path
            return video_path
        if task.tts_audio_path and os.path.exists(task.tts_audio_path):
            return task.tts_audio_path
        return video_path

    async def _fallback_segments(
        self,
        video_path: str,
        subtitle_path: str,
        *,
        clip_count: int,
        min_duration: int,
        max_duration: int,
    ) -> List[HighlightSegment]:
        fallback_segments = []
        if subtitle_path and os.path.exists(subtitle_path):
            entries = self.legacy_short_clips.parse_srt_timestamps(subtitle_path)
            raw_segments = self.legacy_short_clips.find_highlight_segments(
                entries,
                min_duration=min_duration,
                max_duration=max_duration,
                max_clips=clip_count,
            )
        else:
            raw_segments = await self.legacy_short_clips._uniform_segments(
                video_path,
                clip_count,
                min_duration,
                max_duration,
            )

        for index, (start, end, label) in enumerate(raw_segments, start=1):
            fallback_segments.append(
                HighlightSegment(
                    segment_id=f"fallback_{index:03d}_{label}",
                    start=float(start),
                    end=float(end),
                    title=f"高光片段 {index}",
                    summary="legacy fallback segment",
                    total_score=0.3,
                    semantic_score=0.3,
                    audio_score=0.0,
                    scene_score=0.0,
                    source_signals={"source": "legacy_short_clips"},
                )
            )
        return fallback_segments

    def _build_render_profiles(self, task: Task) -> List[Dict[str, Any]]:
        config = normalize_creation_config(task.creation_config, enable_short_clips=task.enable_short_clips)
        base_style = normalize_subtitle_style(task.subtitle_style, defaults=task.subtitle_style)
        platforms = config.get("platforms") or ["douyin", "xiaohongshu", "bilibili"]

        profiles: List[Dict[str, Any]] = []
        for platform in platforms:
            profile = {
                "platform": platform,
                "profile": "vertical_knowledge",
                "aspect_ratio": "9:16",
                "target_size": (1080, 1920),
                "subtitle_style": dict(base_style),
                "intro_path": config.get("intro_path", ""),
                "outro_path": config.get("outro_path", ""),
                "bgm_path": config.get("bgm_path", ""),
                "bgm_volume": float(config.get("bgm_volume", 0.18)),
                "transition": config.get("transition", "fade"),
                "transition_duration": float(config.get("transition_duration", 0.35)),
            }
            if platform == "douyin":
                profile["subtitle_style"] = normalize_subtitle_style(
                    {
                        **base_style,
                        "cn_font_size": int(base_style.get("cn_font_size", 28)) + 6,
                        "en_font_size": int(base_style.get("en_font_size", 18)) + 2,
                        "cn_margin_v": int(base_style.get("cn_margin_v", 90)) + 40,
                        "en_margin_v": int(base_style.get("en_margin_v", 48)) + 20,
                    },
                    defaults=base_style,
                )
            elif platform == "xiaohongshu":
                profile["subtitle_style"] = normalize_subtitle_style(
                    {
                        **base_style,
                        "cn_font_size": int(base_style.get("cn_font_size", 28)) + 8,
                        "en_font_size": int(base_style.get("en_font_size", 18)) + 2,
                        "cn_margin_v": int(base_style.get("cn_margin_v", 90)) + 30,
                    },
                    defaults=base_style,
                )
            elif platform == "bilibili":
                profile.update(
                    {
                        "profile": "horizontal_standard",
                        "aspect_ratio": "16:9",
                        "target_size": (1920, 1080),
                        "subtitle_style": normalize_subtitle_style(
                            {
                                **base_style,
                                "cn_margin_v": max(70, int(base_style.get("cn_margin_v", 90)) - 10),
                                "en_margin_v": max(36, int(base_style.get("en_margin_v", 48)) - 8),
                            },
                            defaults=base_style,
                        ),
                    }
                )
            profiles.append(profile)
        return profiles

    async def _prepare_profile_input(
        self,
        segment: HighlightSegment,
        profile: Dict[str, Any],
        output_dir: Path,
    ) -> str:
        aspect_ratio = profile.get("aspect_ratio", "9:16")
        if aspect_ratio == "9:16":
            if segment.cropped_clip_path and os.path.exists(segment.cropped_clip_path):
                return segment.cropped_clip_path
            return segment.source_clip_path

        target_size = tuple(profile.get("target_size", (1920, 1080)))
        prepared_path = output_dir / f"{profile['platform']}_prepared.mp4"
        ok = await self.long_video.adjust_resolution(
            segment.source_clip_path,
            str(prepared_path),
            width=int(target_size[0]),
            height=int(target_size[1]),
        )
        if not ok:
            shutil.copy2(segment.source_clip_path, prepared_path)
        return str(prepared_path)

    async def _render_variant(
        self,
        task: Task,
        segment: HighlightSegment,
        profile: Dict[str, Any],
        output_dir: Path,
    ) -> Optional[RenderVariant]:
        prepared_input = await self._prepare_profile_input(segment, profile, output_dir)
        subtitled_path = output_dir / f"{profile['platform']}_subtitled.mp4"
        composed_path = output_dir / f"{profile['platform']}_composed.mp4"
        transitioned_path = output_dir / f"{profile['platform']}_transitioned.mp4"
        final_path = output_dir / f"{profile['platform']}.mp4"

        sub_ok = await self.subtitle_renderer.render(
            prepared_input,
            segment.subtitle_path,
            str(subtitled_path),
            subtitle_style=profile.get("subtitle_style"),
        )
        if not sub_ok:
            shutil.copy2(prepared_input, subtitled_path)

        intro_ok = await self.intro_outro.compose(
            str(subtitled_path),
            str(composed_path),
            intro_path=str(profile.get("intro_path", "")),
            outro_path=str(profile.get("outro_path", "")),
        )
        if not intro_ok:
            shutil.copy2(subtitled_path, composed_path)

        transition_ok = await self.transitions.apply(
            str(composed_path),
            str(transitioned_path),
            transition=str(profile.get("transition", "fade")),
            duration=float(profile.get("transition_duration", 0.35)),
        )
        if not transition_ok:
            shutil.copy2(composed_path, transitioned_path)

        mix_ok = await self.audio_mixer.mix(
            str(transitioned_path),
            str(final_path),
            bgm_path=str(profile.get("bgm_path", "")),
            bgm_volume=float(profile.get("bgm_volume", 0.18)),
        )
        if not mix_ok:
            shutil.copy2(transitioned_path, final_path)

        title_seed = segment.title or task.translated_title or task.source_title or segment.segment_id
        title = f"{title_seed} · {profile['platform']}"
        return RenderVariant(
            segment_id=segment.segment_id,
            platform=str(profile["platform"]),
            profile=str(profile["profile"]),
            local_path=str(final_path),
            title=title,
            description=segment.summary,
            metadata={
                "segment_id": segment.segment_id,
                "render_profile": profile["profile"],
                "score_summary": {
                    "total": round(segment.total_score, 4),
                    "semantic": round(segment.semantic_score, 4),
                    "audio": round(segment.audio_score, 4),
                    "scene": round(segment.scene_score, 4),
                },
                "crop_track": dict(segment.crop_track),
                "review_status": (task.creation_state or {}).get("review_status", "pending"),
            },
        )

    async def process(
        self,
        task: Task,
        *,
        video_path: str,
        subtitle_path: str,
        output_dir: str,
    ) -> CreationResult:
        config = normalize_creation_config(task.creation_config, enable_short_clips=task.enable_short_clips)
        task.creation_config = config
        if not config.get("enabled", True):
            task.update_creation_state(
                enabled=False,
                status="skipped",
                stage="skipped",
                review_status="not_required",
            )
            self._persist(task)
            return CreationResult(review_required=False, review_status="not_required")

        render_profiles = self._build_render_profiles(task)
        review_enabled = config.get("review_mode") == "required"
        result = CreationResult(
            review_required=False,
            review_status="not_required",
        )
        warnings: List[str] = []
        output_root = Path(output_dir)
        output_root.mkdir(parents=True, exist_ok=True)

        task.mark_creation_stage(
            "highlight_detection",
            enabled=True,
            review_status="pending" if config.get("review_mode") == "required" else "not_required",
            segments_total=0,
            segments_completed=0,
            variants_total=0,
            variants_completed=0,
            warnings=[],
            used_fallback=False,
        )
        self._persist(task)

        audio_source_path = self._resolve_audio_source(task, video_path)
        highlight_strategy = str(config.get("highlight_strategy", "hybrid") or "hybrid").strip().lower()
        segments: List[HighlightSegment] = []
        if highlight_strategy == "legacy":
            result.used_fallback = True
            task.mark_creation_stage("highlight_legacy", used_fallback=True)
            self._persist(task)
            segments = await self._fallback_segments(
                video_path,
                subtitle_path,
                clip_count=int(config.get("clip_count", 5)),
                min_duration=int(config.get("duration_min", 30)),
                max_duration=int(config.get("duration_max", 180)),
            )
        else:
            try:
                segments = await self.highlight_detector.detect(
                    video_path,
                    subtitle_path,
                    clip_count=int(config.get("clip_count", 5)),
                    min_duration=int(config.get("duration_min", 30)),
                    max_duration=int(config.get("duration_max", 180)),
                    audio_source_path=audio_source_path,
                    strategy=highlight_strategy,
                )
            except Exception as exc:  # pragma: no cover - protective fallback
                logger.warning("高光识别失败，使用 legacy 回退: %s", exc)
                warnings.append(f"highlight_detector_failed: {exc}")
                segments = []

            if not segments:
                result.used_fallback = True
                task.mark_creation_stage("highlight_fallback", used_fallback=True)
                self._persist(task)
                segments = await self._fallback_segments(
                    video_path,
                    subtitle_path,
                    clip_count=int(config.get("clip_count", 5)),
                    min_duration=int(config.get("duration_min", 30)),
                    max_duration=int(config.get("duration_max", 180)),
                )

        result.segments = segments
        task.update_creation_state(
            selected_segments=[segment.to_state_dict() for segment in segments],
            segments_total=len(segments),
            variants_total=len(segments) * len(render_profiles),
            warnings=list(warnings),
            used_fallback=result.used_fallback,
        )
        self._persist(task)

        for index, segment in enumerate(segments, start=1):
            segment_dir = output_root / segment.segment_id
            segment_dir.mkdir(parents=True, exist_ok=True)
            source_clip_path = segment_dir / "source.mp4"
            segment_subtitle_path = segment_dir / "segment.srt"

            task.mark_creation_stage(
                "segment_extraction",
                segments_completed=index - 1,
                variants_completed=len(result.variants),
            )
            self._persist(task)

            extracted = await self.clip_extractor.extract_video(
                video_path,
                segment.start,
                segment.end,
                str(source_clip_path),
            )
            if not extracted:
                warnings.append(f"extract_failed:{segment.segment_id}")
                continue

            self.clip_extractor.extract_subtitles(
                subtitle_path,
                segment.start,
                segment.end,
                str(segment_subtitle_path),
            )
            segment.source_clip_path = str(source_clip_path)
            segment.subtitle_path = str(segment_subtitle_path)

            task.mark_creation_stage(
                "smart_crop",
                segments_completed=index - 1,
                variants_completed=len(result.variants),
            )
            self._persist(task)

            focus_track = await self.subject_detector.detect(str(source_clip_path))
            segment.crop_track = dict(focus_track)
            cropped_path = segment_dir / "vertical_master.mp4"
            if str(config.get("crop_mode", "smart")).strip().lower() == "center":
                focus_track["strategy"] = "center"
                focus_track["focus_class"] = "center"

            crop_ok, crop_meta = await self.smart_cropper.crop(
                str(source_clip_path),
                str(cropped_path),
                focus_track,
                aspect_ratio="9:16",
            )
            segment.crop_track = dict(crop_meta)
            segment.cropped_clip_path = str(cropped_path if crop_ok else source_clip_path)
            if segment.cropped_clip_path:
                result.masters.append(segment.cropped_clip_path)

            task.mark_creation_stage(
                "rendering",
                segments_completed=index - 1,
                variants_completed=len(result.variants),
            )
            self._persist(task)

            for profile in render_profiles:
                variant = await self._render_variant(task, segment, profile, segment_dir)
                if variant:
                    result.variants.append(variant)

            task.update_creation_state(
                selected_segments=[item.to_state_dict() for item in segments],
                segments_completed=index,
                variants_completed=len(result.variants),
                warnings=list(warnings),
            )
            self._persist(task)

        result.warnings = warnings
        review_required = review_enabled and bool(result.variants)
        review_status = "pending" if review_required else "not_required"
        result.review_required = review_required
        result.review_status = review_status
        result.stats = {
            "segments_total": len(segments),
            "segments_completed": len(segments),
            "variants_total": len(segments) * len(render_profiles),
            "variants_completed": len(result.variants),
        }
        task.update_creation_state(
            status="completed",
            stage="completed",
            review_status=review_status,
            selected_segments=[segment.to_state_dict() for segment in segments],
            segments_total=len(segments),
            segments_completed=len(segments),
            variants_total=len(segments) * len(render_profiles),
            variants_completed=len(result.variants),
            warnings=list(warnings),
            used_fallback=result.used_fallback,
        )
        self._persist(task)
        return result
