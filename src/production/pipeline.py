"""
生产管线 - 编排完整的翻译配音流程
下载 → 上传R2 → 自管转写/翻译/TTS → 质检 → 输出
"""
import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from asr import ASRRouter
from core.task import Task, TaskState, TaskStore, TaskProduct
from core.storage import StorageManager, LocalStorage
from core.notification import NotificationManager, NotifyLevel
from core.config import Config
from production.global_translation_reviewer import GlobalTranslationReviewer
from production.sentence_regrouper import SentenceRegrouper
from production.subtitle_repair import SubtitleRepairer
from source.ytdlp_runtime import build_ytdlp_base_cmd, has_yt_dlp_ejs
from tts import VolcengineTTS
from translation import get_translator
from translation.base import mask_secret

logger = logging.getLogger(__name__)

_SRT_BLOCK_PATTERN = re.compile(
    r"(\d+)\s*\n"
    r"([0-9:,]+)\s*-->\s*([0-9:,]+)\s*\n"
    r"(.*?)(?=\n\s*\n\d+\s*\n|\Z)",
    re.S,
)


class QualityChecker:
    """
    质检模块
    检查翻译产出物的质量
    """

    # 质检通过的最低分数
    PASS_THRESHOLD = 60.0

    def __init__(self):
        cfg = Config()
        self.min_zh_line_ratio = float(
            cfg.get("quality", "translation_min_zh_line_ratio", default=0.85)
        )
        self.max_unchanged_ratio = float(
            cfg.get("quality", "translation_max_unchanged_ratio", default=0.15)
        )
        self.max_english_residue_ratio = float(
            cfg.get("quality", "translation_max_english_residue_ratio", default=0.01)
        )

    @staticmethod
    def _normalize_text(text: str) -> str:
        import re

        t = (text or "").lower()
        return re.sub(r"[\W_]+", "", t, flags=re.UNICODE)

    @staticmethod
    def _count_zh_chars(text: str) -> int:
        return sum(1 for c in (text or "") if "\u4e00" <= c <= "\u9fff")

    @staticmethod
    def _count_ascii_letters(text: str) -> int:
        return sum(1 for c in (text or "") if c.isascii() and c.isalpha())

    @classmethod
    def _is_english_residue(cls, text: str) -> bool:
        content = str(text or "").strip()
        if not content:
            return False
        return cls._count_zh_chars(content) == 0 and cls._count_ascii_letters(content) >= 8

    @staticmethod
    def _parse_srt_first_lines(path: Path) -> list[str]:
        import re

        if not path.exists():
            return []

        content = path.read_text(encoding="utf-8", errors="ignore").replace("\r\n", "\n")
        pattern = re.compile(
            r"(\d+)\s*\n([0-9:,]+)\s*-->\s*([0-9:,]+)\s*\n(.*?)(?=\n\s*\n\d+\s*\n|\Z)",
            re.S,
        )
        lines: list[str] = []
        for m in pattern.finditer(content):
            text_lines = [line.strip() for line in m.group(4).strip().split("\n") if line.strip()]
            lines.append(text_lines[0] if text_lines else "")
        return lines

    @staticmethod
    def _parse_srt_time_seconds(ts: str) -> float:
        hh, mm, rest = str(ts or "").strip().split(":")
        ss, ms = rest.split(",")
        return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0

    @classmethod
    def _count_adjacent_time_overlaps(cls, path: Path) -> tuple[int, int]:
        if not path.exists():
            return 0, 0

        content = path.read_text(encoding="utf-8", errors="ignore").replace("\r\n", "\n")
        pattern = re.compile(
            r"(\d+)\s*\n([0-9:,]+)\s*-->\s*([0-9:,]+)\s*\n(.*?)(?=\n\s*\n\d+\s*\n|\Z)",
            re.S,
        )
        timings: list[tuple[float, float]] = []
        for m in pattern.finditer(content):
            try:
                timings.append(
                    (
                        cls._parse_srt_time_seconds(m.group(2)),
                        cls._parse_srt_time_seconds(m.group(3)),
                    )
                )
            except (ValueError, IndexError):
                continue

        if len(timings) < 2:
            return 0, 0

        overlaps = sum(1 for i in range(len(timings) - 1) if timings[i][1] > timings[i + 1][0])
        return overlaps, len(timings) - 1

    async def check(self, task: Task, working_dir: Path) -> Dict[str, Any]:
        """
        执行质检

        Args:
            task: 任务对象
            working_dir: 工作目录（包含翻译产出物）

        Returns:
            Dict: {"score": float, "passed": bool, "details": str}
        """
        issues = []
        score = 100.0

        # 1. 检查字幕文件是否存在且非空
        srt_file = working_dir / "bilingual_srt.srt"
        if not srt_file.exists():
            srt_file = working_dir / "target_language_srt.srt"

        if not srt_file.exists():
            issues.append("字幕文件不存在")
            score -= 40
        elif srt_file.stat().st_size < 100:
            issues.append("字幕文件过小，可能翻译失败")
            score -= 30

        # 2. 检查翻译后的视频文件
        video_files = list(working_dir.glob("output/*.mp4"))
        if not video_files:
            # 也检查直接目录
            video_files = list(working_dir.glob("*.mp4"))

        if not video_files:
            issues.append("未找到翻译后的视频文件")
            score -= 30
        else:
            for vf in video_files:
                # 视频文件至少应大于1MB
                if vf.stat().st_size < 1_000_000:
                    issues.append(f"视频文件过小: {vf.name} ({vf.stat().st_size} bytes)")
                    score -= 15

        # 3. 检查TTS音频（如果启用了TTS）
        if task.enable_tts:
            tts_candidates: List[Path] = []
            if getattr(task, "tts_audio_path", ""):
                tts_candidates.append(Path(task.tts_audio_path))
            tts_candidates.append(working_dir / "tts_final_audio.wav")
            tts_candidates.extend(sorted(working_dir.glob("tts_final_audio.*")))

            tts_audio = next(
                (
                    candidate
                    for candidate in tts_candidates
                    if candidate.exists() and candidate.is_file()
                ),
                None,
            )
            if not tts_audio:
                issues.append("TTS音频文件不存在")
                score -= 20
            elif tts_audio.stat().st_size < 10000:
                issues.append("TTS音频文件过小")
                score -= 15

        # 4. 检查字幕内容质量（简单检查）
        if srt_file.exists() and srt_file.stat().st_size > 100:
            content = srt_file.read_text(encoding="utf-8", errors="ignore")
            lines = content.strip().split("\n")

            # 字幕至少应有几行
            if len(lines) < 4:
                issues.append(f"字幕行数过少: {len(lines)} 行")
                score -= 15

            # 检查是否有中文内容（目标语言为中文时）
            if task.target_lang.startswith("zh"):
                chinese_chars = sum(1 for c in content if '\u4e00' <= c <= '\u9fff')
                if chinese_chars < 10:
                    issues.append(f"字幕中文内容过少 ({chinese_chars} 个中文字符)")
                    score -= 20

                # 行级翻译质量校验：避免“大部分未翻译仍放行”
                origin_path = working_dir / "origin_language_srt.srt"
                target_path = working_dir / "target_language_srt.srt"
                origin_lines = self._parse_srt_first_lines(origin_path)
                target_lines = self._parse_srt_first_lines(target_path)
                overlap_count, overlap_pairs = self._count_adjacent_time_overlaps(target_path)

                total = min(len(origin_lines), len(target_lines))
                if total > 0:
                    zh_lines = sum(1 for line in target_lines[:total] if self._count_zh_chars(line) > 0)
                    unchanged_lines = sum(
                        1
                        for i in range(total)
                        if origin_lines[i]
                        and target_lines[i]
                        and self._normalize_text(origin_lines[i]) == self._normalize_text(target_lines[i])
                    )

                    zh_ratio = zh_lines / total
                    unchanged_ratio = unchanged_lines / total
                    english_residue_lines = sum(
                        1 for line in target_lines[:total] if self._is_english_residue(line)
                    )
                    english_residue_ratio = english_residue_lines / total
                    meta_artifact_lines = sum(
                        1
                        for line in target_lines[:total]
                        if SubtitleRepairer.contains_translation_meta(line)
                    )
                    meta_artifact_ratio = meta_artifact_lines / total

                    if overlap_count:
                        issues.append(f"字幕时间轴存在重叠 {overlap_count} 处")
                        score -= 20
                        if overlap_pairs and overlap_count / overlap_pairs > 0.05:
                            issues.append(
                                f"字幕重叠占比过高 ({overlap_count / overlap_pairs:.1%} > 5%)"
                            )
                            score -= 30

                    if zh_ratio < self.min_zh_line_ratio:
                        issues.append(
                            f"中文字幕覆盖率不足 ({zh_ratio:.1%} < {self.min_zh_line_ratio:.0%})"
                        )
                        score -= 35
                    if unchanged_ratio > self.max_unchanged_ratio:
                        issues.append(
                            f"未翻译行占比过高 ({unchanged_ratio:.1%} > {self.max_unchanged_ratio:.0%})"
                        )
                        score -= 35
                    if english_residue_lines:
                        issues.append(f"存在英文残留 {english_residue_lines} 行")
                        score -= 5
                    if english_residue_ratio > self.max_english_residue_ratio:
                        issues.append(
                            f"英文残留行占比过高 ({english_residue_ratio:.1%} > {self.max_english_residue_ratio:.0%})"
                        )
                        score -= 35
                    if meta_artifact_lines:
                        issues.append(f"存在模型注解/说明文字 {meta_artifact_lines} 行")
                        score -= 15
                    if meta_artifact_ratio > 0.01:
                        issues.append(
                            f"模型注解行占比过高 ({meta_artifact_ratio:.1%} > 1%)"
                        )
                        score -= 35

        details = "; ".join(issues) if issues else "质检通过，无问题"
        passed = score >= self.PASS_THRESHOLD

        logger.info(f"📊 质检结果: 分数={score}, 通过={passed}, 详情={details}")

        return {
            "score": max(0, score),
            "passed": passed,
            "details": details
        }


class ProductionPipeline:
    """
    生产管线
    编排: 下载 → 上传R2 → 自管翻译配音 → 质检
    """

    def __init__(
        self,
        task_store: Optional[TaskStore] = None,
        storage: Optional[StorageManager] = None,
        local_storage: Optional[LocalStorage] = None,
        notifier: Optional[NotificationManager] = None,
    ):
        config = Config()
        self.config = config

        self.task_store = task_store or TaskStore()
        self.storage = storage or StorageManager(
            bucket=config.get("storage", "r2", "bucket", default="videoflow"),
            rclone_remote=config.get("storage", "r2", "rclone_remote", default="r2"),
        )
        self.local_storage = local_storage or LocalStorage(
            working_dir=config.get("storage", "local", "mac_working_dir", default="/tmp/video-factory/working"),
            output_dir=config.get("storage", "local", "mac_output_dir", default="/tmp/video-factory/output"),
        )
        self.notifier = notifier or NotificationManager()
        self.qc = QualityChecker()
        self.sentence_regrouper = SentenceRegrouper()
        self.subtitle_repairer = SubtitleRepairer()
        self.global_translation_reviewer = GlobalTranslationReviewer(config=config)
        self.asr_router = ASRRouter(config=config)
        self.volcengine_tts = VolcengineTTS(config=config)
        self.translator = get_translator(config=config)
        translator_cfg = self.translator.runtime_config()
        logger.info(
            "生产翻译 Translator 已加载: provider=%s enabled=%s base_url=%s model=%s api_key=%s",
            translator_cfg.provider,
            bool(getattr(self.translator, "enabled", True)),
            translator_cfg.base_url,
            translator_cfg.model,
            mask_secret(translator_cfg.api_key),
        )

    @staticmethod
    def classify_download_failure(error_msg: str, has_cookies: bool) -> tuple[str, str]:
        """下载失败分型，返回 (error_code, display_message)。"""
        normalized = (error_msg or "").lower()
        stripped = normalized.strip()
        if (
            "js challenge provider" in normalized
            or "[jsc]" in normalized
            or "yt-dlp-ejs" in normalized
            or "javascript runtime" in normalized
            or "no supported javascript runtime" in normalized
            or "only deno is enabled by default" in normalized
        ):
            return "DOWNLOAD_YTDLP_JS_RUNTIME", "yt-dlp 的 YouTube JS 解算环境异常，请安装/修复 yt-dlp-ejs，并检查 node/deno 配置"
        if (
            "yt-dlp is not installed" in normalized
            or "no module named yt_dlp" in normalized
            or "no module named 'yt_dlp'" in normalized
            or (
                stripped.startswith("[errno 2] no such file or directory:")
                and (
                    "'yt-dlp'" in normalized
                    or "\"yt-dlp\"" in normalized
                    or "/yt-dlp'" in normalized
                    or "/yt-dlp\"" in normalized
                )
            )
        ):
            return "DOWNLOAD_YTDLP_MISSING", "yt-dlp 未安装到当前运行环境，请执行 `./.venv/bin/python -m pip install -r requirements.txt` 后重试"
        if "cookies are no longer valid" in normalized or "cookies have been rotated" in normalized:
            return "DOWNLOAD_COOKIES_INVALID", "YouTube Cookies 已过期，请到设置页面重新导入新的 Cookies"
        if "sign in to confirm you" in normalized and "not a bot" in normalized:
            if has_cookies:
                return "DOWNLOAD_COOKIES_INVALID", "YouTube Cookies 无效或已过期，请到设置页面重新导入"
            return "DOWNLOAD_BOT_VERIFICATION", "YouTube 需要验证，请到设置页面配置 Cookies"
        if "failed to resolve" in normalized or "nodename nor servname provided" in normalized:
            return "DOWNLOAD_NETWORK_ERROR", "下载失败: 网络或 DNS 异常"
        if "http error 429" in normalized or "too many requests" in normalized:
            return "DOWNLOAD_RATE_LIMITED", "下载失败: 请求过于频繁，被目标平台限流"
        return "DOWNLOAD_EXEC_FAILED", f"下载失败: {error_msg[:200]}"

    def _mark_step(self, task: Task, step: str):
        """记录当前步骤，便于前端和诊断接口展示。"""
        task.mark_step(step)

    def _fail_task(self, task: Task, message: str, error_code: str):
        """统一失败写入，确保错误码可追踪。"""
        task.fail(message, error_code=error_code)
        self.task_store.update(task)

    def _ffmpeg_bin(self) -> str:
        return str(self.config.get("ffmpeg", "path", default="ffmpeg")).strip() or "ffmpeg"

    def _ffprobe_bin(self) -> str:
        return str(self.config.get("ffmpeg", "ffprobe_path", default="ffprobe")).strip() or "ffprobe"

    @staticmethod
    def _tts_encoding_to_ext(encoding: str) -> str:
        normalized = str(encoding or "").strip().lower()
        if normalized == "mp3":
            return "mp3"
        if normalized == "ogg_opus":
            return "ogg"
        if normalized == "pcm":
            return "pcm"
        if normalized == "wav":
            return "wav"
        return "wav"

    def _resolve_source_video_path(self, task: Task, working_dir: Path) -> Optional[Path]:
        candidates: List[Path] = []
        if task.source_local_path:
            candidates.append(Path(task.source_local_path))
        candidates.append(working_dir / "source_video.mp4")
        for candidate in candidates:
            if candidate.exists() and candidate.is_file() and candidate.stat().st_size > 1_000_000:
                return candidate
        return None

    def _pick_tts_audio_file(self, task: Task, working_dir: Path) -> Optional[Path]:
        candidates: List[Path] = []
        if getattr(task, "tts_audio_path", ""):
            candidates.append(Path(task.tts_audio_path))
        candidates.append(working_dir / "tts_final_audio.wav")
        for ext in ("mp3", "ogg", "pcm", "wav", "m4a", "aac"):
            candidates.append(working_dir / f"tts_final_audio.{ext}")
        for candidate in sorted(working_dir.glob("tts_final_audio.*")):
            candidates.append(candidate)

        seen = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if candidate.exists() and candidate.is_file() and candidate.stat().st_size > 4_096:
                return candidate
        return None

    async def _probe_media(self, file_path: Path) -> Optional[Dict[str, Any]]:
        if not file_path.exists() or not file_path.is_file():
            return None

        proc = await asyncio.create_subprocess_exec(
            self._ffprobe_bin(),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(file_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = (stderr.decode(errors="ignore") or "").strip()
            logger.warning("ffprobe 检测失败: %s (%s)", file_path, err[:300])
            return None

        try:
            return json.loads(stdout.decode(errors="ignore") or "{}")
        except Exception:
            logger.warning("ffprobe 输出解析失败: %s", file_path)
            return None

    @staticmethod
    def _probe_duration_seconds(probe_data: Dict[str, Any]) -> float:
        if not isinstance(probe_data, dict):
            return 0.0

        fmt = probe_data.get("format", {}) if isinstance(probe_data.get("format"), dict) else {}
        try:
            dur = float(fmt.get("duration", 0) or 0)
            if dur > 0:
                return dur
        except Exception:
            pass

        streams = probe_data.get("streams", []) if isinstance(probe_data.get("streams"), list) else []
        max_dur = 0.0
        for stream in streams:
            if not isinstance(stream, dict):
                continue
            try:
                stream_dur = float(stream.get("duration", 0) or 0)
            except Exception:
                stream_dur = 0.0
            if stream_dur > max_dur:
                max_dur = stream_dur
        return max_dur

    async def _is_valid_video_file(self, file_path: Path) -> Tuple[bool, str]:
        if not file_path.exists() or not file_path.is_file():
            return False, "文件不存在"
        size = file_path.stat().st_size
        if size < 100_000:
            return False, f"文件过小({size} bytes)"

        probe = await self._probe_media(file_path)
        if not probe:
            return False, "ffprobe 失败"

        streams = probe.get("streams", []) if isinstance(probe.get("streams"), list) else []
        has_video = any(isinstance(s, dict) and s.get("codec_type") == "video" for s in streams)
        if not has_video:
            return False, "缺少视频流"

        duration = self._probe_duration_seconds(probe)
        if duration <= 0.2:
            return False, f"时长异常({duration:.3f}s)"
        return True, ""

    async def _is_valid_audio_file(self, file_path: Path) -> Tuple[bool, str]:
        if not file_path.exists() or not file_path.is_file():
            return False, "文件不存在"
        size = file_path.stat().st_size
        if size < 4_096:
            return False, f"文件过小({size} bytes)"

        probe = await self._probe_media(file_path)
        if not probe:
            return False, "ffprobe 失败"

        streams = probe.get("streams", []) if isinstance(probe.get("streams"), list) else []
        has_audio = any(isinstance(s, dict) and s.get("codec_type") == "audio" for s in streams)
        if not has_audio:
            return False, "缺少音频流"

        duration = self._probe_duration_seconds(probe)
        if duration <= 0.2:
            return False, f"时长异常({duration:.3f}s)"
        return True, ""

    async def _remux_video_with_tts_audio(self, source_video: Path, tts_audio: Path, output_video: Path) -> bool:
        output_video.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg = self._ffmpeg_bin()

        # 优先无损拷贝视频轨，速度快；失败后再回退转码。
        fast_cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(source_video),
            "-i",
            str(tts_audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(output_video),
        ]
        proc = await asyncio.create_subprocess_exec(
            *fast_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode == 0:
            return True

        logger.warning(
            "无损重混流失败，尝试视频转码兜底: %s",
            stderr.decode(errors="ignore")[-300:],
        )

        fallback_cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(source_video),
            "-i",
            str(tts_audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-shortest",
            str(output_video),
        ]
        proc2 = await asyncio.create_subprocess_exec(
            *fallback_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr2 = await proc2.communicate()
        if proc2.returncode != 0:
            logger.error("重混流兜底失败: %s", stderr2.decode(errors="ignore")[-500:])
            return False
        return True

    async def _ensure_valid_translated_video(
        self,
        task: Task,
        working_dir: Path,
        *,
        video_candidate: Optional[Path] = None,
        audio_candidate: Optional[Path] = None,
    ) -> bool:
        """
        校验翻译后视频。若损坏且存在有效 TTS 音频，则自动从源视频+TTS音频重建。
        """
        current_video = video_candidate or (Path(task.translated_video_path) if task.translated_video_path else None)
        if current_video:
            ok, reason = await self._is_valid_video_file(current_video)
            if ok:
                task.translated_video_path = str(current_video)
                return True
            logger.warning("翻译后视频无效: %s (%s)", current_video, reason)

        tts_audio = audio_candidate or self._pick_tts_audio_file(task, working_dir)
        if tts_audio:
            audio_ok, audio_reason = await self._is_valid_audio_file(tts_audio)
            if audio_ok:
                task.tts_audio_path = str(tts_audio)
            else:
                logger.warning("TTS 音频无效: %s (%s)", tts_audio, audio_reason)
                tts_audio = None

        source_video = self._resolve_source_video_path(task, working_dir)
        if source_video and tts_audio:
            rebuilt = working_dir / "output" / "video_with_tts.mp4"
            logger.info("🔧 检测到无效翻译视频，尝试自动重建: %s", rebuilt)
            remux_ok = await self._remux_video_with_tts_audio(source_video, tts_audio, rebuilt)
            if remux_ok:
                video_ok, reason = await self._is_valid_video_file(rebuilt)
                if video_ok:
                    task.translated_video_path = str(rebuilt)
                    logger.info("✅ 已重建有效翻译视频: %s", rebuilt)
                    return True
                logger.warning("重建后视频仍无效: %s (%s)", rebuilt, reason)

        task.translated_video_path = ""
        return False

    @staticmethod
    def _format_srt_time(seconds: float) -> str:
        total_ms = max(0, int(round(seconds * 1000)))
        hours = total_ms // 3_600_000
        minutes = (total_ms % 3_600_000) // 60_000
        secs = (total_ms % 60_000) // 1000
        millis = total_ms % 1000
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    @classmethod
    def _parse_srt_entries(cls, content: str) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        normalized = (content or "").replace("\r\n", "\n").strip()
        if not normalized:
            return entries

        for m in _SRT_BLOCK_PATTERN.finditer(normalized):
            lines = [line.strip() for line in m.group(4).strip().split("\n") if line.strip()]
            entries.append(
                {
                    "index": int(m.group(1)),
                    "start": m.group(2).strip(),
                    "end": m.group(3).strip(),
                    "lines": lines or [""],
                }
            )
        if entries:
            return entries

        # 容错：按双换行拆分后重建
        chunks = [chunk.strip() for chunk in normalized.split("\n\n") if chunk.strip()]
        for idx, chunk in enumerate(chunks, start=1):
            lines = [line for line in chunk.split("\n") if line.strip()]
            if len(lines) < 2 or "-->" not in lines[1]:
                continue
            start_end = lines[1].split("-->")
            if len(start_end) != 2:
                continue
            entries.append(
                {
                    "index": idx,
                    "start": start_end[0].strip(),
                    "end": start_end[1].strip(),
                    "lines": [line.strip() for line in lines[2:] if line.strip()] or [""],
                }
            )
        return entries

    @staticmethod
    def _write_srt_entries(entries: List[Dict[str, Any]], output_path: Path):
        blocks: List[str] = []
        for idx, entry in enumerate(entries, start=1):
            lines = [line.strip() for line in (entry.get("lines") or [""]) if line.strip()]
            text = "\n".join(lines) if lines else " "
            start = str(entry.get("start") or "00:00:00,000")
            end = str(entry.get("end") or "00:00:01,000")
            blocks.append(f"{idx}\n{start} --> {end}\n{text}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")

    @staticmethod
    def _entry_main_line(entry: Dict[str, Any]) -> str:
        lines = entry.get("lines") or []
        if not lines:
            return ""
        return " ".join(str(line).strip() for line in lines if str(line).strip()).strip()

    @staticmethod
    def _render_plain_text(lines: List[str]) -> str:
        return "\n".join(line.strip() for line in lines if str(line or "").strip()).strip()

    @staticmethod
    def _normalize_lang_code(lang: str) -> str:
        normalized = str(lang or "").strip().replace("_", "-")
        mapping = {
            "zh-cn": "zh-CN",
            "zh-hans": "zh-CN",
            "zh-tw": "zh-TW",
            "en-us": "en-US",
            "en-gb": "en-GB",
        }
        lowered = normalized.lower()
        if lowered in mapping:
            return mapping[lowered]
        if "-" in normalized:
            first, second = normalized.split("-", 1)
            return f"{first.lower()}-{second.upper()}"
        return normalized or "zh-CN"

    async def _safe_translate_text(self, text: str, source_lang: str, target_lang: str) -> str:
        content = (text or "").strip()
        if not content:
            return ""
        try:
            translated = await self.translator.translate_text(
                text=content,
                source_lang=self._normalize_lang_code(source_lang),
                target_lang=self._normalize_lang_code(target_lang),
            )
        except (ConnectionError, TimeoutError, OSError) as exc:
            logger.error("翻译服务异常，回退原文: %s (type=%s)", exc, type(exc).__name__)
            self._translation_degraded = True
            return content
        except Exception as exc:
            logger.error("翻译未预期异常，回退原文: %s (type=%s)", exc, type(exc).__name__)
            self._translation_degraded = True
            return content
        cleaned = SubtitleRepairer.sanitize_translation_text((translated or content).strip())
        return cleaned or content

    async def _populate_translated_metadata(
        self,
        task: Task,
        *,
        origin_text: str,
        target_text: str,
    ):
        source_title = (task.source_title or "").strip()
        if source_title:
            task.translated_title = await self._safe_translate_text(
                source_title,
                task.source_lang,
                task.target_lang,
            )
        elif not task.translated_title:
            task.translated_title = ""

        if target_text.strip():
            task.translated_description = target_text[:2000]
        elif origin_text.strip():
            task.translated_description = await self._safe_translate_text(
                origin_text[:1200],
                task.source_lang,
                task.target_lang,
            )
        else:
            task.translated_description = ""

    def _should_skip_download_for_youtube_subtitle(self, task: Task) -> bool:
        if not self.asr_router.is_router_enabled():
            return False
        if task.enable_tts:
            return False
        if not bool(self.config.get("asr", "youtube_skip_download", default=False)):
            return False
        if not task.source_url.startswith(("http://", "https://")):
            return False
        return self.asr_router.can_use_youtube(task.source_url)

    async def _step_translate_via_asr_router(
        self,
        task: Task,
        working_dir: Path,
    ) -> tuple[bool, str, str]:
        """
        使用 ASRRouter + LLM 翻译生成字幕文件。
        """
        try:
            asr_result = await self.asr_router.transcribe(
                video_url=task.source_url,
                video_path=task.source_local_path or None,
                source_lang=task.source_lang,
            )
        except Exception as exc:
            logger.warning("ASR 路由失败: %s", exc)
            return False, "ASR_ROUTER_FAILED", f"ASR 路由失败: {exc}"

        origin_entries = self._parse_srt_entries(asr_result.srt_content)
        if not origin_entries:
            logger.warning("ASR 路由返回空字幕，method=%s", asr_result.method)
            return False, "ASR_EMPTY_SUBTITLE", "ASR 未返回有效字幕"

        origin_lines = [self._entry_main_line(entry) for entry in origin_entries]
        translation_result = await self.sentence_regrouper.translate_entries(
            origin_entries,
            target_lang=task.target_lang,
            source_lang=task.source_lang,
            translate_lines=self.subtitle_repairer.translate_lines,
        )
        target_lines = translation_result.cue_lines
        if len(target_lines) != len(origin_entries):
            logger.warning(
                "ASR 路由翻译数量不一致: origin=%s target=%s",
                len(origin_entries),
                len(target_lines),
            )
            target_lines = target_lines[: len(origin_entries)] + origin_lines[len(target_lines) :]

        target_entries: List[Dict[str, Any]] = []
        bilingual_entries: List[Dict[str, Any]] = []
        for idx, entry in enumerate(origin_entries):
            origin_text = origin_lines[idx] if idx < len(origin_lines) else ""
            target_text = target_lines[idx] if idx < len(target_lines) else origin_text

            target_entries.append(
                {
                    "index": idx + 1,
                    "start": entry.get("start", "00:00:00,000"),
                    "end": entry.get("end", "00:00:01,000"),
                    "lines": [target_text or " "],
                }
            )
            bilingual_entries.append(
                {
                    "index": idx + 1,
                    "start": entry.get("start", "00:00:00,000"),
                    "end": entry.get("end", "00:00:01,000"),
                    "lines": [target_text or " ", origin_text or " "],
                }
            )

        origin_path = working_dir / "origin_language_srt.srt"
        target_path = working_dir / "target_language_srt.srt"
        bilingual_path = working_dir / "bilingual_srt.srt"
        origin_text_path = working_dir / "origin_language.txt"
        target_text_path = working_dir / "target_language.txt"
        self._write_srt_entries(origin_entries, origin_path)
        self._write_srt_entries(target_entries, target_path)
        self._write_srt_entries(bilingual_entries, bilingual_path)

        repair_result = await self.subtitle_repairer.repair_if_needed(task, working_dir)
        logger.info(
            "🩹 自管字幕校正: task=%s, passed=%s, repaired=%s, repaired_lines=%s, zh_ratio=%.2f, unchanged=%.2f",
            task.task_id,
            repair_result.passed,
            repair_result.repaired,
            repair_result.repaired_lines,
            repair_result.zh_line_ratio,
            repair_result.unchanged_ratio,
        )
        if not repair_result.passed:
            return False, "TRANSLATION_INCOMPLETE", repair_result.message

        repaired_origin_entries = self._parse_srt_entries(origin_path.read_text(encoding="utf-8", errors="ignore"))
        repaired_target_entries = self._parse_srt_entries(target_path.read_text(encoding="utf-8", errors="ignore"))
        origin_lines = [self._entry_main_line(entry) for entry in repaired_origin_entries or origin_entries]
        target_lines = [self._entry_main_line(entry) for entry in repaired_target_entries or target_entries]
        origin_text = self.sentence_regrouper.render_grouped_text(
            origin_lines,
            translation_result.groups,
        ) or self._render_plain_text(origin_lines)
        target_text = self.sentence_regrouper.render_grouped_text(
            target_lines,
            translation_result.groups,
        ) or self._render_plain_text(target_lines)
        origin_text_path.write_text(origin_text + ("\n" if origin_text else ""), encoding="utf-8")
        target_text_path.write_text(target_text + ("\n" if target_text else ""), encoding="utf-8")
        await self._populate_translated_metadata(task, origin_text=origin_text, target_text=target_text)

        self._mark_step(task, "global_reviewing")
        task._append_timeline("global_review_started", record_time=False)
        self.task_store.update(task)
        global_review_result = await self.global_translation_reviewer.review(
            task,
            working_dir,
            groups=translation_result.groups,
            origin_text=origin_text,
            target_text=target_text,
        )
        task.global_review_report = global_review_result.report
        task.translated_title = global_review_result.translated_title
        task.translated_description = global_review_result.translated_description
        target_text = global_review_result.target_text or target_text
        task._append_timeline(
            "global_review_finished",
            record_time=False,
            passed=global_review_result.passed,
            skipped=global_review_result.skipped,
            fixed=global_review_result.fixed,
            domain=global_review_result.domain,
            confidence=round(global_review_result.confidence, 3),
            blocking_reason=global_review_result.blocking_reason or None,
        )
        self.task_store.update(task)
        if not global_review_result.passed:
            return False, "GLOBAL_REVIEW_FAILED", global_review_result.message

        if task.enable_tts:
            source_audio_path = task.source_local_path if (task.source_local_path and os.path.exists(task.source_local_path)) else None
            if not source_audio_path:
                return False, "TTS_SOURCE_VIDEO_MISSING", "启用配音时必须先下载到本地源视频"

            tts_ext = self._tts_encoding_to_ext(self.volcengine_tts.encoding)
            tts_output_path = working_dir / f"tts_final_audio.{tts_ext}"
            tts_result = await self.volcengine_tts.synthesize(
                text=target_text,
                output_path=str(tts_output_path),
                source_audio_path=source_audio_path,
                language=task.target_lang,
            )
            if not tts_result:
                return False, "TTS_SYNTH_FAILED", self.volcengine_tts.last_error or "Volcengine TTS 合成失败"

            task.tts_audio_path = str(tts_result.audio_path)
            logger.info("✅ Volcengine TTS 合成成功: %s", tts_result.audio_path)
            rebuilt_ok = await self._ensure_valid_translated_video(
                task,
                working_dir,
                video_candidate=None,
                audio_candidate=Path(tts_result.audio_path),
            )
            if not rebuilt_ok:
                return False, "TTS_VIDEO_BUILD_FAILED", "TTS 音频已生成，但未能合成有效的配音视频"

        task.subtitle_path = str(bilingual_path)
        task.transcript_text = target_text
        task.translation_progress = 100
        task.translation_task_id = f"selfhosted_{asr_result.method}_{task.task_id}"
        task.progress = 70
        task.transition(TaskState.QC_CHECKING)
        self.task_store.update(task)
        logger.info(
            "✅ 自管翻译完成: task=%s method=%s lines=%s",
            task.task_id,
            asr_result.method,
            len(origin_entries),
        )
        return True, "", ""

    async def run(self, task: Task) -> bool:
        """
        运行完整生产管线

        Args:
            task: 任务对象

        Returns:
            bool: 是否成功完成
        """
        logger.info(f"🚀 开始生产管线: {task.task_id}")
        self._translation_degraded = False
        working_dir = self.local_storage.get_task_working_dir(task.task_id)

        try:
            # Step 1: 下载源视频（如果需要）
            if task.state == TaskState.QUEUED.value:
                if not await self._step_download(task, working_dir):
                    return False

            # Step 2: 上传源文件到R2（可选，用于VPS-Mac传输）
            if task.state == TaskState.DOWNLOADED.value:
                if not await self._step_upload_source(task, working_dir):
                    return False

            # Step 3: 自管翻译配音
            if task.state in [TaskState.QUEUED.value, TaskState.UPLOADING_SOURCE.value,
                              TaskState.DOWNLOADED.value, TaskState.QC_FAILED.value]:
                if not await self._step_translate(task, working_dir):
                    return False

            # Step 4: 质检
            if task.state == TaskState.QC_CHECKING.value:
                if not await self._step_qc(task, working_dir):
                    return False

            logger.info(f"✅ 生产管线完成: {task.task_id}, 状态: {task.state}")
            return task.state == TaskState.QC_PASSED.value

        except Exception as e:
            logger.exception("💥 生产管线异常: %s", task.task_id)
            self._fail_task(task, str(e), "PRODUCTION_PIPELINE_EXCEPTION")
            await self.notifier.notify_error(task.task_id, str(e), "production_pipeline")
            return False

    async def _step_download(self, task: Task, working_dir: Path) -> bool:
        """Step 1: 下载源视频"""
        # 重试任务时优先复用已存在的本地源文件，避免重复下载被 cookies 卡住
        existing_candidates = []
        if task.source_local_path:
            existing_candidates.append(Path(task.source_local_path))
        existing_candidates.append(working_dir / "source_video.mp4")
        seen = set()
        for candidate in existing_candidates:
            c = str(candidate)
            if c in seen:
                continue
            seen.add(c)
            if candidate.exists() and candidate.is_file() and candidate.stat().st_size > 1_000_000:
                if task.state == TaskState.QUEUED.value:
                    self._mark_step(task, TaskState.DOWNLOADING.value)
                    task.transition(TaskState.DOWNLOADING)
                task.source_local_path = c
                self._mark_step(task, TaskState.DOWNLOADED.value)
                task.transition(TaskState.DOWNLOADED)
                task.progress = max(task.progress, 15)
                self.task_store.update(task)
                logger.info(f"♻️ 复用已下载源视频: {candidate}")
                return True

        if self._should_skip_download_for_youtube_subtitle(task):
            self._mark_step(task, TaskState.DOWNLOADING.value)
            task.transition(TaskState.DOWNLOADING)
            self._mark_step(task, TaskState.DOWNLOADED.value)
            task.transition(TaskState.DOWNLOADED)
            task.progress = max(task.progress, 15)
            self.task_store.update(task)
            logger.info("⚡ YouTube 字幕模式启用，跳过视频下载")
            return True

        # 如果是YouTube URL，使用yt-dlp下载
        if task.source_url.startswith(("http://", "https://")):
            self._mark_step(task, TaskState.DOWNLOADING.value)
            task.transition(TaskState.DOWNLOADING)
            self.task_store.update(task)
            await self.notifier.notify_task_state_change(task.task_id, "queued", "downloading")

            try:
                output_path = str(working_dir / "source_video.mp4")
                cookies_file = working_dir.parent.parent / "config" / "youtube_cookies.txt"
                has_cookies = cookies_file.exists()

                if has_cookies:
                    logger.info(f"🍪 使用 cookies 文件: {cookies_file}")

                success, error_msg = await self._run_ytdlp_download(
                    source_url=task.source_url,
                    output_path=output_path,
                    cookies_file=cookies_file if has_cookies else None,
                )

                # cookies 失效时自动回退到无 cookies 模式再试一次，避免任务直接失败
                if not success and has_cookies:
                    error_code, _ = self.classify_download_failure(
                        error_msg=error_msg,
                        has_cookies=True,
                    )
                    if error_code == "DOWNLOAD_COOKIES_INVALID":
                        logger.warning("🍪 Cookies 可能已失效，尝试无 cookies 重新下载")
                        success, retry_error = await self._run_ytdlp_download(
                            source_url=task.source_url,
                            output_path=output_path,
                            cookies_file=None,
                        )
                        if success:
                            logger.info("✅ 已通过无 cookies 回退下载成功")
                        else:
                            retry_code, retry_message = self.classify_download_failure(
                                error_msg=retry_error,
                                has_cookies=False,
                            )
                            # 第一跳已确认 cookies 失效，失败归因保持为 cookies 问题更准确
                            if retry_code == "DOWNLOAD_BOT_VERIFICATION":
                                self._fail_task(
                                    task,
                                    "YouTube Cookies 无效或已过期，请到设置页面重新导入",
                                    "DOWNLOAD_COOKIES_INVALID",
                                )
                                return False
                            self._fail_task(task, retry_message, retry_code)
                            return False

                if not success:
                    error_code, display_message = self.classify_download_failure(
                        error_msg=error_msg,
                        has_cookies=has_cookies,
                    )
                    self._fail_task(task, display_message, error_code)
                    return False

                task.source_local_path = output_path
                self._mark_step(task, TaskState.DOWNLOADED.value)
                task.transition(TaskState.DOWNLOADED)
                task.progress = 15
                self.task_store.update(task)
                logger.info(f"✅ 下载完成: {output_path}")
                return True

            except asyncio.TimeoutError:
                self._fail_task(task, "下载超时", "DOWNLOAD_TIMEOUT")
                return False
        else:
            # 本地文件路径，直接跳过下载
            if task.state == TaskState.QUEUED.value:
                self._mark_step(task, TaskState.DOWNLOADING.value)
                task.transition(TaskState.DOWNLOADING)
            task.source_local_path = task.source_url
            self._mark_step(task, TaskState.DOWNLOADED.value)
            task.transition(TaskState.DOWNLOADED)
            task.progress = max(task.progress, 15)
            self.task_store.update(task)
            return True

    async def _run_ytdlp_download(
        self,
        source_url: str,
        output_path: str,
        cookies_file: Optional[Path] = None,
    ) -> tuple[bool, str]:
        """执行一次 yt-dlp 下载，返回 (是否成功, stderr)。"""
        try:
            cmd = build_ytdlp_base_cmd() + [
                "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
                "--merge-output-format", "mp4",
                "-o", output_path,
                "--no-playlist",
            ]
        except FileNotFoundError as exc:
            logger.error("yt-dlp 命令缺失: %s", exc)
            return False, str(exc)
        if not has_yt_dlp_ejs():
            logger.warning("yt-dlp-ejs 未安装，YouTube 下载可能在 JS challenge 阶段失败")
        if cookies_file:
            cmd.extend(["--cookies", str(cookies_file)])
        cmd.append(source_url)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            logger.error("yt-dlp 进程启动失败: %s", exc)
            return False, str(exc)
        _, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=Config().get("tasks", "download_timeout", default=600),
        )

        error_msg = stderr.decode(errors="ignore")
        if process.returncode != 0:
            logger.error(f"yt-dlp下载失败: {error_msg}")
            return False, error_msg
        return True, ""

    async def _step_upload_source(self, task: Task, working_dir: Path) -> bool:
        """Step 2: 上传源文件到R2"""
        if not task.source_local_path or not os.path.exists(task.source_local_path):
            # 没有本地文件需要上传，跳过
            return True

        self._mark_step(task, TaskState.UPLOADING_SOURCE.value)
        task.transition(TaskState.UPLOADING_SOURCE)
        self.task_store.update(task)

        r2_path = f"raw/{task.task_id}/source_video.mp4"
        success = self.storage.upload_to_r2(task.source_local_path, r2_path)

        if success:
            task.source_r2_path = r2_path
            task.progress = 20
            self.task_store.update(task)
            logger.info(f"✅ 源文件上传R2: {r2_path}")
            return True

        task.source_r2_path = ""
        task.progress = max(task.progress, 20)
        self.task_store.update(task)
        logger.warning(
            "⚠️ 源文件上传R2失败，继续使用本地源视频: task=%s local=%s",
            task.task_id,
            task.source_local_path,
        )
        return True

    async def _step_translate(self, task: Task, working_dir: Path) -> bool:
        """Step 3: 翻译配音（仅自管链路）"""
        # 跳转到翻译中状态
        self._mark_step(task, TaskState.TRANSLATING.value)
        task.transition(TaskState.TRANSLATING)
        self.task_store.update(task)
        await self.notifier.notify_task_state_change(task.task_id, task.state, "translating")

        if not self.asr_router.is_router_enabled():
            self._fail_task(
                task,
                "ASR provider 未启用。请在设置中选择 auto/youtube/volcengine/whisper",
                "ASR_ROUTER_DISABLED",
            )
            return False

        ok, error_code, error_message = await self._step_translate_via_asr_router(task, working_dir)
        if ok:
            return True

        self._fail_task(
            task,
            error_message or "自管翻译链路失败",
            error_code or "SELF_HOSTED_TRANSLATION_FAILED",
        )
        return False

    async def _step_qc(self, task: Task, working_dir: Path) -> bool:
        """Step 4: 质检"""
        self._mark_step(task, TaskState.QC_CHECKING.value)

        # 翻译降级检查：如果翻译过程中出现过异常回退，直接拒绝
        if getattr(self, "_translation_degraded", False):
            logger.error("翻译服务降级，QC 拒绝: %s", task.task_id)
            qc_result = {
                "passed": False,
                "score": 0.0,
                "details": "翻译服务降级，存在未翻译内容。请检查翻译服务状态后重试。",
            }
        else:
            qc_result = await self.qc.check(task, working_dir)

        task.qc_score = qc_result["score"]
        task.qc_details = qc_result["details"]

        if qc_result["passed"]:
            task.transition(TaskState.QC_PASSED)
            task.mark_step(TaskState.QC_PASSED.value)
            task.progress = 75
            await self.notifier.notify(
                "质检通过",
                f"分数: {qc_result['score']}\n详情: {qc_result['details']}",
                NotifyLevel.SUCCESS,
                task.task_id
            )
        else:
            task.transition(TaskState.QC_FAILED)
            task.mark_step(TaskState.QC_FAILED.value)
            task.last_error_code = "QC_FAILED"
            await self.notifier.notify(
                "质检未通过",
                f"分数: {qc_result['score']}\n详情: {qc_result['details']}",
                NotifyLevel.WARNING,
                task.task_id
            )

        self.task_store.update(task)
        return qc_result["passed"]

    async def close(self):
        """关闭资源"""
        await self.global_translation_reviewer.close()
        await self.subtitle_repairer.close()
        await self.notifier.close()
