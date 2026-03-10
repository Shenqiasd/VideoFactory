"""
creation 子系统的结构化类型。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class SegmentCandidate:
    segment_id: str
    start: float
    end: float
    title: str = ""
    summary: str = ""
    keywords: List[str] = field(default_factory=list)
    semantic_score: float = 0.0
    audio_score: float = 0.0
    scene_score: float = 0.0
    score: float = 0.0
    source: str = "heuristic"
    explain: str = ""
    source_signals: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HighlightSegment(SegmentCandidate):
    subtitle_text: str = ""
    transcript_excerpt: str = ""
    total_score: float = 0.0
    source_clip_path: str = ""
    cropped_clip_path: str = ""
    subtitle_path: str = ""
    crop_track: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max(0.0, float(self.end) - float(self.start))

    def to_state_dict(self) -> Dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
            "title": self.title,
            "summary": self.summary,
            "keywords": list(self.keywords),
            "semantic_score": self.semantic_score,
            "audio_score": self.audio_score,
            "scene_score": self.scene_score,
            "total_score": self.total_score,
            "transcript_excerpt": self.transcript_excerpt or self.subtitle_text,
            "source_signals": dict(self.source_signals),
            "crop_track": dict(self.crop_track),
        }


@dataclass
class CropTrack:
    mode: str = "center"
    bbox: List[int] = field(default_factory=list)
    samples: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RenderVariant:
    segment_id: str
    platform: str
    profile: str
    title: str
    local_path: str
    description: str = ""
    review_status: str = "pending"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CreationResult:
    engine: str = "creation_pipeline"
    used_fallback: bool = False
    review_required: bool = True
    review_status: str = "pending"
    segments: List[HighlightSegment] = field(default_factory=list)
    variants: List[RenderVariant] = field(default_factory=list)
    masters: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["segments"] = [segment.to_dict() for segment in self.segments]
        data["variants"] = [variant.to_dict() for variant in self.variants]
        data["fallback_used"] = data.pop("used_fallback", False)
        return data
