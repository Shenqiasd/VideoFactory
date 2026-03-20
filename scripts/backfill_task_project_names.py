#!/usr/bin/env python3
"""
按最新规则回填历史任务的 source_title / translated_title。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.project_naming import derive_local_source_title, fetch_remote_source_title, is_remote_url, translate_project_name
from core.task import TaskStore


async def _backfill_one(task, *, timeout_seconds: float) -> bool:
    previous_source_title = str(task.source_title or "").strip()
    previous_project_name = str(task.translated_title or "").strip()

    if is_remote_url(task.source_url):
        refreshed_source_title = await fetch_remote_source_title(
            task.source_url,
            timeout_seconds=timeout_seconds,
        )
        resolved_source_title = refreshed_source_title or previous_source_title
    else:
        resolved_source_title = previous_source_title or derive_local_source_title(task.source_url)

    resolved_project_name = ""
    if resolved_source_title:
        resolved_project_name = await translate_project_name(
            resolved_source_title,
            source_lang=task.source_lang,
            target_lang=task.target_lang,
        )

    changed = (
        resolved_source_title != previous_source_title
        or resolved_project_name != previous_project_name
    )
    if changed:
        task.source_title = resolved_source_title
        task.translated_title = resolved_project_name
    return changed


async def _run(store_path: str | None, *, limit: int | None, dry_run: bool, timeout_seconds: float) -> int:
    store = TaskStore(store_path=store_path)
    tasks = store.list_all()
    if limit is not None:
        tasks = tasks[: max(0, limit)]

    changed_count = 0
    for task in tasks:
        changed = await _backfill_one(task, timeout_seconds=timeout_seconds)
        if not changed:
            continue
        changed_count += 1
        if not dry_run:
            store.update(task)
        print(
            f"[updated] {task.task_id} | source_title={task.source_title!r} | project_name={task.translated_title!r}",
            flush=True,
        )

    total = len(tasks)
    mode = "dry-run" if dry_run else "write"
    print(f"[summary] mode={mode} total={total} changed={changed_count}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="回填 VideoFactory 历史任务项目名称")
    parser.add_argument("--store-path", default=None, help="任务存储文件路径，默认使用 ~/.video-factory/tasks.json")
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 条任务")
    parser.add_argument("--timeout", type=float, default=8.0, help="远端标题抓取超时时间（秒）")
    parser.add_argument("--dry-run", action="store_true", help="只打印结果，不写回 tasks.json")
    args = parser.parse_args()

    return asyncio.run(
        _run(
            args.store_path,
            limit=args.limit,
            dry_run=args.dry_run,
            timeout_seconds=args.timeout,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
