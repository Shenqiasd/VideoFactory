#!/usr/bin/env python3
"""
查看任务状态
用法: python scripts/check_status.py [task_id] [--all] [--active]
"""
import sys
import os
import argparse
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.task import TaskStore, TaskState


def format_time(timestamp: float) -> str:
    """格式化时间戳"""
    if not timestamp:
        return "N/A"
    return datetime.fromtimestamp(timestamp).strftime("%m-%d %H:%M:%S")


def format_duration(seconds: float) -> str:
    """格式化耗时"""
    if seconds < 60:
        return f"{seconds:.0f}秒"
    elif seconds < 3600:
        return f"{seconds/60:.1f}分钟"
    else:
        return f"{seconds/3600:.1f}小时"


def print_task(task, detail=False):
    """打印任务信息"""
    state_emoji = {
        "queued": "⏳", "downloading": "📥", "downloaded": "📦",
        "uploading_source": "☁️", "translating": "🔄", "qc_checking": "🔍",
        "qc_passed": "✅", "qc_failed": "⚠️", "processing": "🏭",
        "uploading_products": "📤", "ready_to_publish": "📋",
        "publishing": "📡", "completed": "🎉", "failed": "❌",
    }

    emoji = state_emoji.get(task.state, "❓")
    print(f"\n{emoji} [{task.task_id}]")
    print(f"   标题: {task.source_title or task.source_url[:60]}")
    print(f"   状态: {task.state} ({task.progress}%)")
    print(f"   创建: {format_time(task.created_at)}")
    print(f"   耗时: {format_duration(task.duration_seconds)}")

    if task.error_message:
        print(f"   ❌ 错误: {task.error_message}")

    if detail:
        print(f"   URL: {task.source_url}")
        print(f"   语言: {task.source_lang} → {task.target_lang}")
        print(f"   TTS: {'是' if task.enable_tts else '否'}")
        print(f"   KlicStudio: {task.klic_task_id or 'N/A'} ({task.klic_progress}%)")

        if task.translated_title:
            print(f"   翻译标题: {task.translated_title}")

        if task.qc_score > 0:
            print(f"   质检: {task.qc_score} 分 - {task.qc_details}")

        if task.products:
            print(f"   产出物: {len(task.products)} 个")
            for p in task.products:
                print(f"     - [{p.get('type')}] {p.get('platform')} → {p.get('local_path', '')[:50]}")


def main():
    parser = argparse.ArgumentParser(description="查看任务状态")
    parser.add_argument("task_id", nargs="?", default=None, help="任务ID")
    parser.add_argument("--all", action="store_true", help="显示所有任务")
    parser.add_argument("--active", action="store_true", help="只显示活跃任务")
    parser.add_argument("--stats", action="store_true", help="显示统计信息")
    parser.add_argument("--detail", "-d", action="store_true", help="显示详细信息")

    args = parser.parse_args()

    store = TaskStore()

    if args.stats:
        stats = store.get_stats()
        print("\n📊 任务统计:")
        for state, count in sorted(stats.items()):
            if state != "total":
                print(f"   {state}: {count}")
        print(f"   -------")
        print(f"   总计: {stats.get('total', 0)}")
        return

    if args.task_id:
        task = store.get(args.task_id)
        if task:
            print_task(task, detail=True)
        else:
            print(f"❌ 任务不存在: {args.task_id}")
        return

    if args.active:
        tasks = store.list_active()
        print(f"\n🔄 活跃任务: {len(tasks)} 个")
    else:
        tasks = store.list_all()
        print(f"\n📋 所有任务: {len(tasks)} 个")

    if not tasks:
        print("   (空)")
    else:
        for task in tasks[:20]:
            print_task(task, detail=args.detail)


if __name__ == "__main__":
    main()
