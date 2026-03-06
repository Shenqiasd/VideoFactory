#!/usr/bin/env python3
"""
提交视频处理任务
用法: python scripts/submit_task.py <youtube_url> [--title "视频标题"] [--no-tts] [--no-clips]
"""
import sys
import os
import argparse
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.task import TaskStore
from production.pipeline import ProductionPipeline


async def main():
    parser = argparse.ArgumentParser(description="提交视频处理任务")
    parser.add_argument("url", help="YouTube视频URL或本地文件路径")
    parser.add_argument("--title", default="", help="视频标题")
    parser.add_argument("--source-lang", default="en", help="源语言 (默认: en)")
    parser.add_argument("--target-lang", default="zh_cn", help="目标语言 (默认: zh_cn)")
    parser.add_argument("--no-tts", action="store_true", help="不启用TTS配音")
    parser.add_argument("--no-clips", action="store_true", help="不生成短视频")
    parser.add_argument("--no-article", action="store_true", help="不生成图文")
    parser.add_argument("--subtitle", default="horizontal", choices=["horizontal", "vertical", "none"],
                        help="字幕嵌入类型")
    parser.add_argument("--priority", type=int, default=2, choices=[0, 1, 2, 3],
                        help="优先级 (0=紧急, 1=高, 2=普通, 3=低)")
    parser.add_argument("--run", action="store_true", help="提交后立即运行生产管线")

    args = parser.parse_args()

    # 创建任务
    store = TaskStore()
    task = store.create(
        source_url=args.url,
        source_title=args.title,
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        enable_tts=not args.no_tts,
        enable_short_clips=not args.no_clips,
        enable_article=not args.no_article,
        embed_subtitle_type=args.subtitle,
        priority=args.priority,
    )

    print(f"✅ 任务创建成功!")
    print(f"   ID:    {task.task_id}")
    print(f"   URL:   {task.source_url}")
    print(f"   状态:  {task.state}")
    print(f"   TTS:   {'启用' if task.enable_tts else '禁用'}")
    print(f"   短视频: {'启用' if task.enable_short_clips else '禁用'}")
    print(f"   图文:  {'启用' if task.enable_article else '禁用'}")
    print(f"   字幕:  {task.embed_subtitle_type}")

    if args.run:
        print(f"\n🚀 启动生产管线...")
        pipeline = ProductionPipeline(task_store=store)
        success = await pipeline.run(task)

        if success:
            print(f"\n✅ 生产管线完成! 状态: {task.state}")
            print(f"   翻译标题: {task.translated_title}")
            print(f"   质检分数: {task.qc_score}")
        else:
            print(f"\n❌ 生产管线失败: {task.error_message}")

        await pipeline.close()


if __name__ == "__main__":
    asyncio.run(main())
