"""
长视频加工模块
- 添加片头/片尾
- 烧录字幕
- 画面调整
"""
import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from core.config import Config
from core.subtitle_style import normalize_subtitle_style

logger = logging.getLogger(__name__)


class LongVideoProcessor:
    """
    长视频加工器
    对翻译完成的视频进行二次加工
    """

    def __init__(self, ffmpeg_path: Optional[str] = None, ffprobe_path: Optional[str] = None):
        config = Config()
        configured_ffmpeg = config.get("ffmpeg", "path", default="ffmpeg")
        configured_ffprobe = config.get("ffmpeg", "ffprobe_path", default="")
        configured_font_name = config.get("ffmpeg", "subtitle_font_name", default="")
        configured_fonts_dir = config.get("ffmpeg", "subtitle_fonts_dir", default="")
        configured_font_candidates = config.get("ffmpeg", "subtitle_font_candidates", default=[])
        configured_visibility_threshold = config.get("ffmpeg", "preview_visibility_threshold", default=0.0015)

        self.ffmpeg = ffmpeg_path or configured_ffmpeg
        self.ffprobe = ffprobe_path or configured_ffprobe or self._guess_ffprobe_path(self.ffmpeg)
        self.subtitle_font_name = configured_font_name or self._default_font_name()
        self.subtitle_fonts_dir = configured_fonts_dir or self._default_fonts_dir()
        if isinstance(configured_font_candidates, list):
            candidates = [str(item).strip() for item in configured_font_candidates if str(item).strip()]
        else:
            candidates = []
        self.subtitle_font_candidates = candidates or self._default_font_candidates()
        try:
            self.preview_visibility_threshold = float(configured_visibility_threshold)
        except (TypeError, ValueError):
            self.preview_visibility_threshold = 0.0015

    @staticmethod
    def _guess_ffprobe_path(ffmpeg_path: str) -> str:
        if not ffmpeg_path:
            return "ffprobe"
        if ffmpeg_path.endswith("ffmpeg"):
            return ffmpeg_path[:-6] + "ffprobe"
        return "ffprobe"

    @staticmethod
    def _default_font_name() -> str:
        # 按优先级探测系统中可用的 CJK 字体。
        # macOS
        if os.path.exists("/System/Library/Fonts/Hiragino Sans GB.ttc"):
            return "Hiragino Sans GB"
        # Linux – Noto Sans CJK（Docker 镜像通过 fonts-noto-cjk 安装）
        for noto in (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        ):
            if os.path.exists(noto):
                return "Noto Sans CJK SC"
        # Linux – WenQuanYi（Ubuntu 默认中文字体）
        if os.path.exists("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"):
            return "WenQuanYi Zen Hei"
        return "Arial Unicode MS"

    @staticmethod
    def _default_font_candidates() -> List[str]:
        return [
            # Linux CJK fonts
            "Noto Sans CJK SC",
            "WenQuanYi Zen Hei",
            # macOS CJK fonts
            "Hiragino Sans GB",
            "PingFang SC",
            "Arial Unicode MS",
            "Helvetica",
        ]

    @staticmethod
    def _default_fonts_dir() -> str:
        for candidate in (
            # Linux
            "/usr/share/fonts/opentype/noto",
            "/usr/share/fonts/truetype/wqy",
            "/usr/share/fonts",
            # macOS
            "/System/Library/Fonts/Supplemental",
            "/System/Library/Fonts",
            "/Library/Fonts",
        ):
            if os.path.isdir(candidate):
                return candidate
        return ""

    @staticmethod
    def _escape_filter_path(path: str) -> str:
        return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")

    @staticmethod
    def _dedupe_font_candidates(candidates: List[str]) -> List[str]:
        result: List[str] = []
        seen = set()
        for item in candidates:
            value = (item or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    def _build_ass_filter(self, ass_path: str) -> str:
        ass_path_escaped = self._escape_filter_path(ass_path)
        subtitle_filter = f"ass={ass_path_escaped}"
        if self.subtitle_fonts_dir:
            fonts_dir_escaped = self._escape_filter_path(self.subtitle_fonts_dir)
            subtitle_filter = f"{subtitle_filter}:fontsdir={fonts_dir_escaped}"
        return subtitle_filter

    @staticmethod
    def _normalize_alignment(alignment: int) -> int:
        return alignment if 1 <= alignment <= 9 else 2

    @classmethod
    def _calculate_position(cls, width: int, height: int, alignment: int, margin_v: int) -> Tuple[int, int]:
        align = cls._normalize_alignment(alignment)
        horizontal = (align - 1) % 3  # 0=left, 1=center, 2=right
        vertical_group = (align - 1) // 3  # 0=bottom, 1=middle, 2=top

        min_pad = 16
        margin_x = max(24, min(96, int(width * 0.06)))
        margin_y = max(0, margin_v)

        if horizontal == 0:
            x = margin_x
        elif horizontal == 1:
            x = width // 2
        else:
            x = width - margin_x

        if vertical_group == 2:
            y = margin_y
        elif vertical_group == 1:
            y = height // 2
        else:
            y = height - margin_y

        x = max(min_pad, min(width - min_pad, x))
        y = max(min_pad, min(height - min_pad, y))
        return x, y

    @classmethod
    def _calculate_visibility_bbox(cls, width: int, height: int, style: Dict[str, int]) -> Tuple[int, int, int, int]:
        line_bounds = []
        for prefix in ("cn", "en"):
            alignment = cls._normalize_alignment(style[f"{prefix}_alignment"])
            margin_v = style[f"{prefix}_margin_v"]
            font_size = style[f"{prefix}_font_size"]
            _, y = cls._calculate_position(width, height, alignment, margin_v)
            half_h = max(40, int(font_size * 1.8))
            line_bounds.append((y - half_h, y + half_h))

        top = max(0, min(min(a for a, _ in line_bounds) - 24, height - 1))
        bottom = min(height, max(b for _, b in line_bounds) + 24)
        if bottom <= top:
            top = max(0, height - 260)
            bottom = height

        x = max(0, int(width * 0.05))
        w = max(1, min(width - x, int(width * 0.90)))
        h = max(1, bottom - top)
        return x, top, w, h

    async def _extract_frame_crop_raw(
        self,
        video_path: str,
        raw_output: str,
        *,
        at_second: float,
        crop_box: Tuple[int, int, int, int],
    ) -> bool:
        x, y, w, h = crop_box
        crop_expr = f"crop={w}:{h}:{x}:{y},format=gray"
        cmd = [
            self.ffmpeg,
            "-ss", f"{at_second:.2f}",
            "-i", video_path,
            "-frames:v", "1",
            "-vf", crop_expr,
            "-f", "rawvideo",
            "-y",
            raw_output,
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            logger.warning(f"提取预览帧失败: {stderr.decode(errors='ignore')[-240:]}")
            return False
        return os.path.exists(raw_output) and os.path.getsize(raw_output) > 0

    async def _calculate_visibility_score(
        self,
        source_video: str,
        rendered_video: str,
        style: Dict[str, int],
        width: int,
        height: int,
    ) -> float:
        crop_box = self._calculate_visibility_bbox(width, height, style)
        tmp_dir = Path(rendered_video).parent
        source_raw = str(tmp_dir / ".preview_source_crop.raw")
        rendered_raw = str(tmp_dir / ".preview_rendered_crop.raw")

        try:
            ok1 = await self._extract_frame_crop_raw(
                source_video,
                source_raw,
                at_second=1.0,
                crop_box=crop_box,
            )
            ok2 = await self._extract_frame_crop_raw(
                rendered_video,
                rendered_raw,
                at_second=1.0,
                crop_box=crop_box,
            )
            if not ok1 or not ok2:
                return 0.0

            source_bytes = Path(source_raw).read_bytes()
            rendered_bytes = Path(rendered_raw).read_bytes()
            if not source_bytes or not rendered_bytes:
                return 0.0

            n = min(len(source_bytes), len(rendered_bytes))
            if n <= 0:
                return 0.0

            changed = 0
            for src, dst in zip(source_bytes[:n], rendered_bytes[:n]):
                if abs(src - dst) >= 20:
                    changed += 1
            return changed / n
        finally:
            for path in (source_raw, rendered_raw):
                if os.path.exists(path):
                    os.remove(path)

    @staticmethod
    def _extract_first_cue_window(subtitle_path: str) -> str:
        try:
            with open(subtitle_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            m = re.search(r"([0-9:,]+)\s*-->\s*([0-9:,]+)", content)
            if m:
                return f"{m.group(1)} -> {m.group(2)}"
        except Exception:
            pass
        return ""

    async def _run_ffmpeg(self, args: List[str], timeout: int = 600) -> bool:
        """执行ffmpeg命令"""
        cmd = [self.ffmpeg] + args
        logger.info(f"🎬 FFmpeg: {' '.join(cmd[:8])}...")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )

            if process.returncode != 0:
                logger.error(f"FFmpeg失败: {stderr.decode(errors='ignore')[-500:]}")
                return False
            return True

        except asyncio.TimeoutError:
            process.kill()
            logger.error(f"FFmpeg超时 ({timeout}秒)")
            return False

    async def burn_subtitles(
        self,
        video_path: str,
        subtitle_path: str,
        output_path: str,
        subtitle_style: Optional[Dict[str, Any]] = None,
        font_size: int = 24,
        font_name: Optional[str] = None,
        margin_v: int = 30,
        allow_soft_fallback: bool = True,
    ) -> bool:
        success, _ = await self.burn_subtitles_with_debug(
            video_path=video_path,
            subtitle_path=subtitle_path,
            output_path=output_path,
            subtitle_style=subtitle_style,
            font_size=font_size,
            font_name=font_name,
            margin_v=margin_v,
            allow_soft_fallback=allow_soft_fallback,
            probe_font_candidates=False,
            visibility_check=False,
        )
        return success

    async def burn_subtitles_with_debug(
        self,
        video_path: str,
        subtitle_path: str,
        output_path: str,
        subtitle_style: Optional[Dict[str, Any]] = None,
        font_size: int = 24,
        font_name: Optional[str] = None,
        margin_v: int = 30,
        allow_soft_fallback: bool = True,
        probe_font_candidates: bool = False,
        visibility_check: bool = False,
        visibility_threshold: Optional[float] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        烧录字幕并返回诊断信息（预览接口使用）。
        """
        threshold = (
            self.preview_visibility_threshold
            if visibility_threshold is None
            else max(0.0, float(visibility_threshold))
        )
        style = normalize_subtitle_style(
            subtitle_style,
            defaults={
                "cn_font_size": font_size,
                "en_font_size": max(12, int(font_size * 0.8)),
                "cn_margin_v": margin_v + 20,
                "en_margin_v": margin_v,
                "cn_alignment": 2,
                "en_alignment": 2,
            },
        )

        video_info = await self.get_video_info(video_path)
        render_width = int((video_info or {}).get("width", 1920) or 1920)
        render_height = int((video_info or {}).get("height", 1080) or 1080)
        render_width = max(320, render_width)
        render_height = max(240, render_height)

        preview_debug: Dict[str, Any] = {
            "font_requested": font_name or self.subtitle_font_name,
            "font_used": "",
            "filter_expr": "",
            "visibility_score": 0.0,
            "cue_window": self._extract_first_cue_window(subtitle_path),
            "attempts": [],
            "render_size": {"width": render_width, "height": render_height},
        }

        candidate_fonts = [font_name or self.subtitle_font_name]
        if probe_font_candidates:
            candidate_fonts.extend(self.subtitle_font_candidates)
        candidate_fonts = self._dedupe_font_candidates(candidate_fonts)

        accepted = False
        for idx, candidate_font in enumerate(candidate_fonts):
            ass_path = os.path.join(
                os.path.dirname(output_path),
                f"subtitle_preview_{idx}.ass" if len(candidate_fonts) > 1 else "subtitle_preview.ass",
            )
            try:
                self._generate_ass_from_srt(
                    srt_path=subtitle_path,
                    ass_path=ass_path,
                    style=style,
                    font_name=candidate_font,
                    render_width=render_width,
                    render_height=render_height,
                )
                subtitle_filter = self._build_ass_filter(ass_path)
                args = [
                    "-i", video_path,
                    "-vf", subtitle_filter,
                    "-c:v", "libx264",
                    "-preset", "medium",
                    "-crf", "23",
                    "-c:a", "copy",
                    "-y",
                    output_path,
                ]

                ffmpeg_ok = await self._run_ffmpeg(args, timeout=1200)
                attempt = {
                    "font": candidate_font,
                    "ffmpeg_ok": ffmpeg_ok,
                    "visibility_score": 0.0,
                }
                if not ffmpeg_ok:
                    preview_debug["attempts"].append(attempt)
                    continue

                score = 0.0
                if visibility_check:
                    score = await self._calculate_visibility_score(
                        source_video=video_path,
                        rendered_video=output_path,
                        style=style,
                        width=render_width,
                        height=render_height,
                    )
                    attempt["visibility_score"] = round(score, 6)
                    if score < threshold:
                        attempt["reason"] = "visibility_too_low"
                        preview_debug["attempts"].append(attempt)
                        if probe_font_candidates:
                            continue
                        if os.path.exists(output_path):
                            os.remove(output_path)
                        preview_debug["error"] = (
                            "字幕渲染失败：字体不可用或字幕位置越界（可见性校验未通过）"
                        )
                        return False, preview_debug

                preview_debug["attempts"].append(attempt)
                preview_debug["font_used"] = candidate_font
                preview_debug["filter_expr"] = subtitle_filter
                preview_debug["visibility_score"] = round(score, 6)
                accepted = True
                break
            finally:
                if os.path.exists(ass_path):
                    os.remove(ass_path)

        if accepted:
            return True, preview_debug

        if not allow_soft_fallback:
            logger.error("❌ 硬字幕烧录失败（已禁用软回退）")
            preview_debug["error"] = "字幕渲染失败：未找到可用字体或渲染失败"
            return False, preview_debug

        # 方案2: 回退到软字幕嵌入（SRT作为字幕流嵌入到mp4中）
        logger.info("尝试软字幕嵌入方式...")
        args_soft = [
            "-i", video_path,
            "-i", subtitle_path,
            "-c:v", "copy",
            "-c:a", "copy",
            "-c:s", "mov_text",
            "-metadata:s:s:0", "language=zho",
            "-y",
            output_path
        ]

        success = await self._run_ffmpeg(args_soft, timeout=300)

        if success:
            logger.info("✅ 软字幕嵌入成功（字幕作为独立流）")
            preview_debug["fallback"] = "soft_subtitle"
            return True, preview_debug

        # 方案3: 都失败了，复制原视频，字幕作为独立文件
        logger.warning("⚠️ 字幕嵌入均失败，复制原始视频")
        import shutil
        shutil.copy2(video_path, output_path)
        # 同时复制字幕到输出目录
        output_dir = os.path.dirname(output_path)
        srt_dest = os.path.join(output_dir, "bilingual_srt.srt")
        shutil.copy2(subtitle_path, srt_dest)
        preview_debug["fallback"] = "copy_with_sidecar_srt"
        return True, preview_debug

    @staticmethod
    def _escape_ass_text(text: str) -> str:
        escaped = (text or "").replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")
        return escaped.replace("\n", r"\N")

    @staticmethod
    def _srt_time_to_ass(ts: str) -> str:
        # 00:00:01,234 -> 0:00:01.23
        m = re.match(r"(\d+):(\d+):(\d+),(\d+)", ts.strip())
        if not m:
            return "0:00:00.00"
        hh, mm, ss, ms = m.groups()
        return f"{int(hh)}:{int(mm):02d}:{int(ss):02d}.{int(ms) // 10:02d}"

    def _generate_ass_from_srt(
        self,
        srt_path: str,
        ass_path: str,
        style: Dict[str, int],
        font_name: str,
        render_width: int = 1920,
        render_height: int = 1080,
    ):
        """
        将双语 SRT 转为 ASS，支持中英文独立字号/位置。
        - 第一行视为目标语言（CN style）
        - 第二行视为原文（EN style）
        """
        with open(srt_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read().replace("\r\n", "\n")

        pattern = re.compile(
            r"(\d+)\s*\n([0-9:,]+)\s*-->\s*([0-9:,]+)\s*\n(.*?)(?=\n\s*\n\d+\s*\n|\Z)",
            re.S,
        )
        entries = []
        for m in pattern.finditer(content):
            lines = [line.strip() for line in m.group(4).strip().split("\n") if line.strip()]
            entries.append(
                {
                    "start": self._srt_time_to_ass(m.group(2)),
                    "end": self._srt_time_to_ass(m.group(3)),
                    "lines": lines,
                }
            )

        ass_lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {render_width}",
            f"PlayResY: {render_height}",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
            "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
            "Alignment,MarginL,MarginR,MarginV,Encoding",
            (
                "Style: CN,{font},{cn_size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
                "0,0,0,0,100,100,0,0,1,2,0,{cn_align},30,30,{cn_margin},1"
            ).format(
                font=font_name,
                cn_size=style["cn_font_size"],
                cn_align=style["cn_alignment"],
                cn_margin=style["cn_margin_v"],
            ),
            (
                "Style: EN,{font},{en_size},&H00DADADA,&H000000FF,&H00000000,&H64000000,"
                "0,0,0,0,100,100,0,0,1,2,0,{en_align},30,30,{en_margin},1"
            ).format(
                font=font_name,
                en_size=style["en_font_size"],
                en_align=style["en_alignment"],
                en_margin=style["en_margin_v"],
            ),
            "",
            "[Events]",
            "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
        ]

        for entry in entries:
            lines = entry.get("lines", [])
            if not lines:
                continue
            cn_align = self._normalize_alignment(style["cn_alignment"])
            cn_x, cn_y = self._calculate_position(
                render_width,
                render_height,
                cn_align,
                style["cn_margin_v"],
            )
            cn_text = "{\\an%d\\pos(%d,%d)}%s" % (
                cn_align,
                cn_x,
                cn_y,
                self._escape_ass_text(lines[0]),
            )

            en_text = self._escape_ass_text(lines[1] if len(lines) > 1 else "")
            ass_lines.append(
                f"Dialogue: 0,{entry['start']},{entry['end']},CN,,0,0,0,,{cn_text}"
            )
            if en_text:
                en_align = self._normalize_alignment(style["en_alignment"])
                en_x, en_y = self._calculate_position(
                    render_width,
                    render_height,
                    en_align,
                    style["en_margin_v"],
                )
                en_text = "{\\an%d\\pos(%d,%d)}%s" % (
                    en_align,
                    en_x,
                    en_y,
                    en_text,
                )
                ass_lines.append(
                    f"Dialogue: 0,{entry['start']},{entry['end']},EN,,0,0,0,,{en_text}"
                )

        with open(ass_path, "w", encoding="utf-8") as f:
            f.write("\n".join(ass_lines) + "\n")

    async def add_intro_outro(
        self,
        video_path: str,
        output_path: str,
        intro_path: Optional[str] = None,
        outro_path: Optional[str] = None,
    ) -> bool:
        """
        添加片头/片尾

        Args:
            video_path: 主视频路径
            output_path: 输出路径
            intro_path: 片头视频路径（可选）
            outro_path: 片尾视频路径（可选）

        Returns:
            bool: 是否成功
        """
        parts = []
        if intro_path and os.path.exists(intro_path):
            parts.append(intro_path)
        parts.append(video_path)
        if outro_path and os.path.exists(outro_path):
            parts.append(outro_path)

        if len(parts) == 1:
            # 没有片头片尾，直接复制
            import shutil
            shutil.copy2(video_path, output_path)
            return True

        # 创建concat文件
        concat_file = output_path + ".concat.txt"
        with open(concat_file, "w") as f:
            for part in parts:
                f.write(f"file '{part}'\n")

        args = [
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-c", "copy",
            "-y",
            output_path
        ]

        success = await self._run_ffmpeg(args)

        # 清理concat文件
        if os.path.exists(concat_file):
            os.remove(concat_file)

        return success

    async def adjust_resolution(
        self,
        video_path: str,
        output_path: str,
        width: int = 1920,
        height: int = 1080,
    ) -> bool:
        """
        调整视频分辨率（保持比例，填充黑边）

        Args:
            video_path: 输入视频路径
            output_path: 输出路径
            width: 目标宽度
            height: 目标高度

        Returns:
            bool: 是否成功
        """
        scale_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
        )

        args = [
            "-i", video_path,
            "-vf", scale_filter,
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-c:a", "copy",
            "-y",
            output_path
        ]

        return await self._run_ffmpeg(args, timeout=900)

    async def get_video_info(self, video_path: str) -> Optional[Dict[str, Any]]:
        """
        获取视频技术信息

        Args:
            video_path: 视频路径

        Returns:
            Optional[Dict]: {"duration": float, "width": int, "height": int, "fps": float, "size_mb": float}
        """
        cmd = [
            self.ffprobe,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            video_path
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                return None

            import json
            data = json.loads(stdout.decode())

            # 提取视频流信息
            video_stream = None
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    video_stream = stream
                    break

            if not video_stream:
                return None

            # 解析帧率
            fps_str = video_stream.get("r_frame_rate", "30/1")
            if "/" in fps_str:
                num, den = fps_str.split("/")
                fps = float(num) / float(den) if float(den) > 0 else 30.0
            else:
                fps = float(fps_str)

            duration = float(data.get("format", {}).get("duration", 0))
            size_bytes = int(data.get("format", {}).get("size", 0))

            return {
                "duration": duration,
                "width": int(video_stream.get("width", 0)),
                "height": int(video_stream.get("height", 0)),
                "fps": round(fps, 2),
                "size_mb": round(size_bytes / (1024 * 1024), 2),
                "codec": video_stream.get("codec_name", ""),
                "bitrate": int(data.get("format", {}).get("bit_rate", 0)),
            }

        except Exception as e:
            logger.error(f"获取视频信息异常: {e}")
            return None

    async def process(
        self,
        video_path: str,
        subtitle_path: Optional[str],
        output_dir: str,
        subtitle_style: Optional[Dict[str, Any]] = None,
        intro_path: Optional[str] = None,
        outro_path: Optional[str] = None,
        burn_subs: bool = True,
        target_resolution: Optional[tuple] = None,
    ) -> Optional[str]:
        """
        完整的长视频加工流程

        Args:
            video_path: 翻译后的视频路径
            subtitle_path: 字幕文件路径
            output_dir: 输出目录
            intro_path: 片头路径
            outro_path: 片尾路径
            burn_subs: 是否烧录字幕
            target_resolution: 目标分辨率 (width, height)

        Returns:
            Optional[str]: 最终输出视频路径
        """
        os.makedirs(output_dir, exist_ok=True)
        current_video = video_path

        # Step 1: 烧录字幕
        if burn_subs and subtitle_path and os.path.exists(subtitle_path):
            sub_output = os.path.join(output_dir, "long_video_subbed.mp4")
            success = await self.burn_subtitles(
                current_video,
                subtitle_path,
                sub_output,
                subtitle_style=subtitle_style,
            )
            if success:
                current_video = sub_output
                logger.info("✅ 字幕烧录完成")
            else:
                logger.warning("⚠️ 字幕烧录失败，继续使用原视频")

        # Step 2: 调整分辨率
        if target_resolution:
            res_output = os.path.join(output_dir, "long_video_resized.mp4")
            success = await self.adjust_resolution(
                current_video, res_output,
                width=target_resolution[0], height=target_resolution[1]
            )
            if success:
                current_video = res_output
                logger.info("✅ 分辨率调整完成")

        # Step 3: 添加片头/片尾
        if intro_path or outro_path:
            final_output = os.path.join(output_dir, "long_video_final.mp4")
            success = await self.add_intro_outro(current_video, final_output, intro_path, outro_path)
            if success:
                current_video = final_output
                logger.info("✅ 片头片尾添加完成")

        # 确保最终文件名一致
        final_path = os.path.join(output_dir, "long_video.mp4")
        if current_video != final_path:
            import shutil
            shutil.copy2(current_video, final_path)

        logger.info(f"✅ 长视频加工完成: {final_path}")
        return final_path
