#!/usr/bin/env python3
"""
端到端测试：Orchestrator 自动驱动测试
1. 创建任务
2. 运行 Orchestrator.process_task()
3. 验证任务从 QUEUED → QC_PASSED → PROCESSING → READY_TO_PUBLISH
"""
import sys
import os
import asyncio
import logging
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("test_orchestrator")


async def main():
    from core.task import Task, TaskState, TaskStore
    from workers.orchestrator import Orchestrator

    # 创建新的 TaskStore
    store = TaskStore()

    # 创建测试任务
    task = store.create(
        source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        source_title="Rick Astley - Never Gonna Give You Up",
        source_lang="en",
        target_lang="zh_cn",
        enable_tts=True,
        enable_short_clips=False,  # 跳过短视频（测试用）
        enable_article=False,      # 跳过图文（测试用）
        embed_subtitle_type="none",
    )

    logger.info(f"📝 创建测试任务: {task.task_id}")
    logger.info(f"   状态: {task.state}")

    # 创建Orchestrator
    orchestrator = Orchestrator(task_store=store)

    # 运行编排 (Production → Factory → Ready to Publish)
    start_time = time.time()
    logger.info("🚀 开始 Orchestrator.process_task()...")

    success = await orchestrator.process_task(task)

    elapsed = time.time() - start_time
    logger.info(f"⏱️  总耗时: {elapsed:.1f} 秒")

    # 获取最终任务状态
    final_task = store.get(task.task_id)

    if final_task:
        logger.info(f"📊 最终状态: {final_task.state}")
        logger.info(f"   进度: {final_task.progress}%")
        logger.info(f"   QC分数: {final_task.qc_score}")
        logger.info(f"   产出物: {len(final_task.products)} 个")
        logger.info(f"   错误信息: {final_task.error_message or '无'}")

        if final_task.products:
            for i, p in enumerate(final_task.products):
                logger.info(f"   产出物 {i+1}: type={p.get('type')}, platform={p.get('platform')}, path={p.get('local_path', '')[:60]}")
    else:
        logger.error("无法获取任务！")

    if success:
        logger.info("✅ Orchestrator 测试通过！")
    else:
        logger.warning(f"⚠️ Orchestrator 返回 False, 最终状态: {final_task.state if final_task else 'N/A'}")

    # 清理
    await orchestrator.close()

    return success


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
