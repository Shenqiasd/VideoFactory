import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.task import TaskState, TaskStore  # noqa: E402
from production.pipeline import ProductionPipeline  # noqa: E402


class NoopNotifier:
    async def notify_task_state_change(self, *args, **kwargs):
        return None

    async def notify(self, *args, **kwargs):
        return None

    async def notify_error(self, *args, **kwargs):
        return None

    async def close(self):
        return None


class FakeProcess:
    def __init__(self, returncode: int, stderr_text: str):
        self.returncode = returncode
        self._stderr = stderr_text.encode("utf-8")

    async def communicate(self):
        return b"", self._stderr


@pytest.mark.asyncio
async def test_step_download_retries_without_cookies_when_cookie_invalid(monkeypatch, tmp_path):
    calls = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        calls.append(list(cmd))
        if len(calls) == 1:
            return FakeProcess(1, "ERROR: Sign in to confirm you're not a bot.")
        return FakeProcess(0, "")

    monkeypatch.setattr(
        "production.pipeline.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    store = TaskStore(store_path=str(tmp_path / "tasks.json"))
    task = store.create(source_url="https://www.youtube.com/watch?v=test")
    pipeline = ProductionPipeline(task_store=store, notifier=NoopNotifier())

    working_dir = tmp_path / "working" / task.task_id
    working_dir.mkdir(parents=True, exist_ok=True)
    cookies_file = working_dir.parent.parent / "config" / "youtube_cookies.txt"
    cookies_file.parent.mkdir(parents=True, exist_ok=True)
    cookies_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    ok = await pipeline._step_download(task, working_dir)

    assert ok is True
    assert task.state == TaskState.DOWNLOADED.value
    assert len(calls) == 2
    assert "--cookies" in calls[0]
    assert "--cookies" not in calls[1]


@pytest.mark.asyncio
async def test_step_download_reuses_existing_source_for_remote_url(monkeypatch, tmp_path):
    async def fake_create_subprocess_exec(*cmd, **kwargs):
        raise AssertionError("yt-dlp should not run when existing source file is reusable")

    monkeypatch.setattr(
        "production.pipeline.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    store = TaskStore(store_path=str(tmp_path / "tasks.json"))
    task = store.create(source_url="https://www.youtube.com/watch?v=test")
    pipeline = ProductionPipeline(task_store=store, notifier=NoopNotifier())

    working_dir = tmp_path / "working" / task.task_id
    working_dir.mkdir(parents=True, exist_ok=True)
    source_video = working_dir / "source_video.mp4"
    source_video.write_bytes(b"x" * 1_500_000)

    ok = await pipeline._step_download(task, working_dir)

    assert ok is True
    assert task.state == TaskState.DOWNLOADED.value
    assert task.source_local_path == str(source_video)


@pytest.mark.asyncio
async def test_step_download_cookie_invalid_still_fails_with_cookie_code(monkeypatch, tmp_path):
    calls = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        calls.append(list(cmd))
        return FakeProcess(1, "ERROR: Sign in to confirm you're not a bot.")

    monkeypatch.setattr(
        "production.pipeline.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    store = TaskStore(store_path=str(tmp_path / "tasks.json"))
    task = store.create(source_url="https://www.youtube.com/watch?v=test")
    pipeline = ProductionPipeline(task_store=store, notifier=NoopNotifier())

    working_dir = tmp_path / "working" / task.task_id
    working_dir.mkdir(parents=True, exist_ok=True)
    cookies_file = working_dir.parent.parent / "config" / "youtube_cookies.txt"
    cookies_file.parent.mkdir(parents=True, exist_ok=True)
    cookies_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    ok = await pipeline._step_download(task, working_dir)

    assert ok is False
    assert task.state == TaskState.FAILED.value
    assert task.last_error_code == "DOWNLOAD_COOKIES_INVALID"
    assert "无效或已过期" in task.error_message
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_step_download_skip_in_youtube_subtitle_mode(monkeypatch, tmp_path):
    async def fake_create_subprocess_exec(*cmd, **kwargs):
        raise AssertionError("yt-dlp should not run when youtube subtitle mode skips download")

    monkeypatch.setattr(
        "production.pipeline.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    store = TaskStore(store_path=str(tmp_path / "tasks.json"))
    task = store.create(source_url="https://www.youtube.com/watch?v=test")
    pipeline = ProductionPipeline(task_store=store, notifier=NoopNotifier())

    monkeypatch.setattr(pipeline, "_should_skip_download_for_youtube_subtitle", lambda _task: True)

    working_dir = tmp_path / "working" / task.task_id
    working_dir.mkdir(parents=True, exist_ok=True)

    ok = await pipeline._step_download(task, working_dir)

    assert ok is True
    assert task.state == TaskState.DOWNLOADED.value
