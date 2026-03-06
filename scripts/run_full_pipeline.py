#!/usr/bin/env python3
"""
运行完整管线（生产 + 加工 + 发布调度）
用法: python scripts/run_full_pipeline.py <task_id>
      python scripts/run_full_pipeline.py <youtube_url> --auto
"""
import sys
import os
import argparse
import asyncio
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.task import TaskStore, TaskState
from workers.orchestrator import Orchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    parser = argparse.ArgumentParser(description="运行完整管线")
    parser.add_argument("target", help="任务ID 或 YouTube URL")
    parser.add_argument("--auto", action="store_true", help="自动创建任务并运行")
    parser.add_argument("--title", default="", help="视频标题（自动创建时使用）")
    parser.add_argument("--no-tts", action="store_true", help="不启用TTS")

    args = parser.parse_args()

    store = TaskStore()
    orchestrator = Orchestrator(task_store=store)

    try:
        # 确定任务
        if args.auto and args.target.startswith("http"):
            # 自动创建任务
            task = store.create(
                source_url=args.target,
                source_title=args.title,
                enable_tts=not args.no_tts,
            )
            print(f"✅ 创建任务: {task.task_id}")
        else:
            task = store.get(args.target)
            if not task:
                print(f"❌ 任务不存在: {args.target}")
                return

        print(f"\n🚀 开始完整管线: {task.task_id}")
        print(f"   URL: {task.source_url}")
        print(f"   状态: {task.state}")
        print()

        # 运行完整流程
        success = await orchestrator.process_task(task)

        print()
        if success:
            print(f"✅ 管线完成!")
            print(f"   最终状态: {task.state}")
            print(f"   产出物: {len(task.products)} 个")
            if task.translated_title:
                print(f"   翻译标题: {task.translated_title}")
        else:
            print(f"❌ 管线失败: {task.error_message}")

    finally:
        await orchestrator.close()


if __name__ == "__main__":
    asyncio.run(main())
