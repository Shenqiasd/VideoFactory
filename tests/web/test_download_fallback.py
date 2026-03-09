import asyncio
from pathlib import Path

from core.task import TaskState, TaskStore
from production.pipeline import ProductionPipeline


def test_step_download_fails_when_ytdlp_js_runtime_fails(tmp_path):
    store = TaskStore(store_path=str(tmp_path / "tasks.json"))
    task = store.create(
        source_url="https://www.youtube.com/watch?v=fallback_case",
        source_title="",
        source_lang="en",
        target_lang="zh_cn",
        task_scope="subtitle_dub",
        enable_tts=True,
        enable_short_clips=False,
        enable_article=False,
        embed_subtitle_type="none",
        subtitle_style={},
        priority=2,
    )
    pipeline = ProductionPipeline(task_store=store)

    async def _fake_download(*args, **kwargs):
        return False, 'WARNING: [youtube] [jsc] JS Challenge Provider "deno" returned an invalid response'

    pipeline._run_ytdlp_download = _fake_download  # type: ignore[method-assign]

    ok = asyncio.run(pipeline._step_download(task, Path(tmp_path) / "workdir"))

    assert ok is False
    assert task.state == TaskState.FAILED.value
    assert task.last_error_code == "DOWNLOAD_YTDLP_JS_RUNTIME"
    assert task.source_local_path == ""
