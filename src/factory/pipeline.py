"""
加工管线 - 编排所有二次创作模块
长视频加工 + 短视频切片 + 封面生成 + 元数据生成 + 图文生成
"""
import asyncio
import logging
import os
from pathlib import Path
from typing import Optional, Dict, Any, List

from core.task import Task, TaskState, TaskStore, TaskProduct
from core.storage import StorageManager, LocalStorage
from core.notification import NotificationManager, NotifyLevel
from core.config import Config
from creation.models import CreationResult
from creation.pipeline import CreationPipeline
from factory.long_video import LongVideoProcessor
from factory.short_clips import ShortClipExtractor
from factory.cover import CoverGenerator
from factory.metadata import MetadataGenerator
from factory.article import ArticleGenerator

logger = logging.getLogger(__name__)


class FactoryPipeline:
    """
    加工管线
    编排: 长视频加工 → 短视频切片 → 封面 → 元数据 → 图文
    所有模块尽可能并行执行
    """

    def __init__(
        self,
        task_store: Optional[TaskStore] = None,
        storage: Optional[StorageManager] = None,
        local_storage: Optional[LocalStorage] = None,
        notifier: Optional[NotificationManager] = None,
    ):
        config = Config()

        self.task_store = task_store or TaskStore()
        self.storage = storage or StorageManager(
            bucket=config.get("storage", "r2", "bucket", default="videoflow"),
        )
        self.local_storage = local_storage or LocalStorage()
        self.notifier = notifier or NotificationManager()

        # 子模块
        self.long_video = LongVideoProcessor()
        self.short_clips = ShortClipExtractor()
        self.creation = CreationPipeline(
            task_store=self.task_store,
            fallback_short_clips=self.short_clips,
        )
        self.cover = CoverGenerator()
        self.metadata = MetadataGenerator()
        self.article = ArticleGenerator()

    async def run(self, task: Task) -> bool:
        """
        运行完整加工管线

        Args:
            task: 已通过质检的任务

        Returns:
            bool: 是否成功
        """
        if task.state != TaskState.QC_PASSED.value:
            logger.warning(f"任务 {task.task_id} 不在QC_PASSED状态，当前: {task.state}")
            return False

        logger.info(f"🏭 开始加工管线: {task.task_id}")

        # 进入加工状态
        task.transition(TaskState.PROCESSING)
        self.task_store.update(task)
        await self.notifier.notify_task_state_change(task.task_id, "qc_passed", "processing")

        working_dir = self.local_storage.get_task_working_dir(task.task_id)
        output_dir = self.local_storage.get_task_output_dir(task.task_id)

        try:
            # 确定翻译后的视频路径
            video_path = task.translated_video_path
            scope = getattr(task, "task_scope", "full")
            # 检查视频文件是否存在且大于1MB（避免使用损坏的文件）
            if video_path and os.path.exists(video_path) and os.path.getsize(video_path) < 1_000_000:
                logger.warning(f"⚠️ 翻译视频文件过小 ({os.path.getsize(video_path)} bytes)，回退到源视频")
                video_path = None

            # subtitle_only 模式默认无TTS，不会产出 translated_video_path，需要直接用源视频做字幕压制
            if (not video_path or not os.path.exists(video_path)) and scope == "subtitle_only":
                source_video = task.source_local_path or str(working_dir / "source_video.mp4")
                if source_video and os.path.exists(source_video) and os.path.getsize(source_video) > 1_000_000:
                    video_path = source_video
                    logger.info(f"📹 subtitle_only 使用源视频进行字幕压制: {video_path}")

            if not video_path or not os.path.exists(video_path):
                # 查找工作目录中有效的视频（大于1MB）
                all_videos = list(working_dir.glob("output/*.mp4")) + list(working_dir.glob("*.mp4"))
                video_candidates = [v for v in all_videos if v.stat().st_size > 1_000_000]
                if video_candidates:
                    video_path = str(video_candidates[0])
                    logger.info(f"📹 使用视频: {video_path}")
                else:
                    task.fail("找不到有效的视频文件（所有文件都过小或不存在）")
                    self.task_store.update(task)
                    return False

            subtitle_path = task.subtitle_path
            transcript = task.transcript_text

            if scope == "subtitle_only":
                logger.info("🎞️ subtitle_only 模式：仅执行字幕压制长视频")
                long_video_path = await self._process_long_video(task, video_path, subtitle_path, output_dir)
                clip_result = {"variants": [], "segments": [], "masters": [], "stats": {}, "review_status": "approved"}
                cover_paths = {}
                metadata_map = {}
                article_map = {}
                if not long_video_path:
                    task.fail("字幕压制失败，未生成长视频", error_code="LONG_VIDEO_BUILD_FAILED")
                    self.task_store.update(task)
                    return False
            else:
                # ========== 并行执行各加工模块 ==========
                results = await asyncio.gather(
                    # 1. 长视频加工
                    self._process_long_video(task, video_path, subtitle_path, output_dir),
                    # 2. 短视频切片
                    self._process_short_clips(task, video_path, subtitle_path, output_dir),
                    # 3. 封面生成
                    self._process_covers(task, video_path, output_dir),
                    # 4. 元数据生成
                    self._process_metadata(task, transcript),
                    # 5. 图文生成
                    self._process_articles(task, transcript, output_dir),
                    return_exceptions=True,
                )

                # 处理结果
                long_video_path, clip_result, cover_paths, metadata_map, article_map = results

                # 检查是否有异常
                errors = []
                for i, r in enumerate(results):
                    if isinstance(r, Exception):
                        errors.append(f"模块{i}异常: {r}")
                        logger.error(f"加工模块异常: {r}")

                if errors:
                    logger.warning(f"⚠️ 部分加工模块出错: {errors}")

            # 记录产出物
            await self._record_products(task, long_video_path, clip_result, cover_paths, metadata_map)

            # 上传产出物到R2
            task.transition(TaskState.UPLOADING_PRODUCTS)
            task.progress = 85
            self.task_store.update(task)

            await self._upload_products(task, output_dir)

            # 完成加工
            task.transition(TaskState.READY_TO_PUBLISH)
            review_required = bool((task.creation_status or {}).get("review_required"))
            review_status = str((task.creation_state or {}).get("review_status", "approved"))
            task.progress = 88 if review_required and review_status != "approved" else 90
            self.task_store.update(task)

            await self.notifier.notify(
                "加工完成" if not (review_required and review_status != "approved") else "加工完成，等待审核",
                (
                    f"产出物: {len(task.products)} 个\n准备发布"
                    if not (review_required and review_status != "approved")
                    else f"产出物: {len(task.products)} 个\n审核状态: {review_status}"
                ),
                NotifyLevel.SUCCESS,
                task.task_id,
            )

            logger.info(f"✅ 加工管线完成: {task.task_id}, 产出物: {len(task.products)}")
            return True

        except Exception as e:
            logger.error(f"💥 加工管线异常: {task.task_id}: {e}")
            task.fail(str(e))
            self.task_store.update(task)
            await self.notifier.notify_error(task.task_id, str(e), "factory_pipeline")
            return False

    async def _process_long_video(self, task: Task, video_path: str, subtitle_path: str, output_dir: Path):
        """长视频加工"""
        try:
            long_dir = str(output_dir / "long_video")
            result = await self.long_video.process(
                video_path=video_path,
                subtitle_path=subtitle_path,
                output_dir=long_dir,
                subtitle_style=getattr(task, "subtitle_style", None),
                burn_subs=True,
            )
            logger.info(f"✅ 长视频加工: {result}")
            return result
        except Exception as e:
            logger.error(f"长视频加工失败: {e}")
            return None

    async def _process_short_clips(self, task: Task, video_path: str, subtitle_path: str, output_dir: Path):
        """短视频切片"""
        if not task.enable_short_clips:
            logger.info("⏭️ 跳过短视频切片（未启用）")
            task.update_creation_state(
                enabled=False,
                status="skipped",
                stage="skipped",
                review_status="not_required",
                selected_segments=[],
                segments_total=0,
                segments_completed=0,
                variants_total=0,
                variants_completed=0,
            )
            self.task_store.update(task)
            return CreationResult()

        try:
            clips_dir = str(output_dir / "short_clips")
            creation_result = await self.creation.process(
                task,
                video_path=video_path,
                subtitle_path=subtitle_path,
                output_dir=clips_dir,
            )
            logger.info(
                "✅ 创作短视频: segments=%s variants=%s fallback=%s",
                len(creation_result.segments),
                len(creation_result.variants),
                creation_result.used_fallback,
            )
            return creation_result
        except Exception as e:
            logger.error(f"短视频切片失败: {e}")
            task.update_creation_state(
                enabled=True,
                status="failed",
                stage="failed",
                review_status="rejected",
                warnings=list((task.creation_state or {}).get("warnings", [])) + [str(e)],
            )
            self.task_store.update(task)
            return CreationResult(warnings=[str(e)])

    async def _process_covers(self, task: Task, video_path: str, output_dir: Path):
        """封面生成"""
        try:
            cover_dir = str(output_dir / "covers")
            covers = await self.cover.process(
                video_path=video_path,
                output_dir=cover_dir,
                generate_vertical=True,
            )
            logger.info(f"✅ 封面生成: {covers}")
            return covers
        except Exception as e:
            logger.error(f"封面生成失败: {e}")
            return {}

    async def _process_metadata(self, task: Task, transcript: str):
        """元数据生成"""
        try:
            metadata = await self.metadata.generate_for_all_platforms(
                original_title=task.source_title,
                translated_title=task.translated_title,
                transcript=transcript,
                platforms=["bilibili", "douyin", "xiaohongshu", "youtube"],
            )
            parse_modes = {p: m.get("parse_mode", "unknown") for p, m in metadata.items()}
            logger.info(f"✅ 元数据生成: platforms={list(metadata.keys())}, parse_modes={parse_modes}")
            return metadata
        except Exception as e:
            logger.error(f"元数据生成失败: {e}")
            return {}

    async def _process_articles(self, task: Task, transcript: str, output_dir: Path):
        """图文生成"""
        if not task.enable_article:
            logger.info("⏭️ 跳过图文生成（未启用）")
            return {}

        try:
            article_dir = str(output_dir / "articles")
            articles = await self.article.process(
                title=task.translated_title or task.source_title,
                transcript=transcript,
                output_dir=article_dir,
                platforms=["xiaohongshu", "wechat"],
            )
            logger.info(f"✅ 图文生成: {list(articles.keys())}")
            return articles
        except Exception as e:
            logger.error(f"图文生成失败: {e}")
            return {}

    async def _record_products(
        self,
        task: Task,
        long_video_path,
        clip_result,
        cover_paths,
        metadata_map,
    ):
        """记录产出物到任务"""
        # 长视频
        if long_video_path and not isinstance(long_video_path, Exception):
            product = TaskProduct(
                type="long_video",
                platform="all",
                local_path=str(long_video_path),
                title=task.translated_title,
                description=task.translated_description,
                metadata=metadata_map.get("bilibili", {}) if isinstance(metadata_map, dict) else {},
            )
            task.add_product(product)

        # 短视频
        if isinstance(clip_result, CreationResult):
            for i, variant in enumerate(clip_result.variants):
                variant_metadata = variant.metadata if isinstance(variant.metadata, dict) else {}
                platform_metadata = metadata_map.get(variant.platform, {}) if isinstance(metadata_map, dict) else {}
                product = TaskProduct(
                    type="short_clip",
                    platform=variant.platform,
                    local_path=variant.local_path,
                    title=variant.title or f"{task.translated_title} #{i+1}",
                    description=variant.description,
                    metadata={
                        **platform_metadata,
                        **variant_metadata,
                        "segment_id": variant.segment_id,
                        "review_status": variant_metadata.get(
                            "review_status",
                            (task.creation_state or {}).get("review_status", "pending"),
                        ),
                    },
                )
                task.add_product(product)
        elif clip_result and not isinstance(clip_result, Exception):
            variants = clip_result.get("variants", []) if isinstance(clip_result, dict) else []
            for i, variant in enumerate(variants):
                product = TaskProduct(
                    type="short_clip",
                    platform=variant.get("platform", "douyin"),
                    local_path=variant.get("local_path", ""),
                    title=variant.get("title", f"{task.translated_title} #{i+1}"),
                    metadata={
                        **(metadata_map.get(variant.get("platform", ""), {}) if isinstance(metadata_map, dict) else {}),
                        **(variant.get("metadata", {}) if isinstance(variant, dict) else {}),
                        "segment_id": variant.get("segment_id", f"clip_{i+1:02d}"),
                        "review_status": variant.get(
                            "review_status",
                            (task.creation_state or {}).get("review_status", "pending"),
                        ),
                    },
                )
                task.add_product(product)

        # 封面
        if cover_paths and isinstance(cover_paths, dict):
            for cover_type, cover_path in cover_paths.items():
                product = TaskProduct(
                    type="cover",
                    platform="all",
                    local_path=cover_path,
                    metadata={"cover_type": cover_type},
                )
                task.add_product(product)

        self.task_store.update(task)

    async def _upload_products(self, task: Task, output_dir: Path):
        """上传所有产出物到R2"""
        r2_base = f"processed/{task.task_id}"

        # 同步整个输出目录到R2
        success = self.storage.sync_to_r2(str(output_dir), r2_base)

        if success:
            logger.info(f"✅ 产出物已上传R2: {r2_base}")
            # 更新产出物的R2路径
            for product in task.products:
                local_path = product.get("local_path", "")
                if local_path:
                    relative = os.path.relpath(local_path, str(output_dir))
                    product["r2_path"] = f"{r2_base}/{relative}"
            self.task_store.update(task)
        else:
            logger.warning("⚠️ 产出物上传R2失败")

    async def close(self):
        """关闭资源"""
        await self.metadata.close()
        await self.article.close()
        await self.notifier.close()
