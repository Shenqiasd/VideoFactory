import os
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = PROJECT_ROOT / "tests" / "artifacts"
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def _resolve_python_3_11() -> str:
    candidates = []
    override = os.environ.get("VF_PYTHON_BIN")
    if override:
        candidates.append(override)

    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        candidates.append(str(venv_python))

    python311 = shutil.which("python3.11")
    if python311:
        candidates.append(python311)

    python3 = shutil.which("python3")
    if python3:
        candidates.append(python3)

    candidates.append(sys.executable)

    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            version = subprocess.check_output(
                [
                    candidate,
                    "-c",
                    "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')",
                ],
                text=True,
            ).strip()
        except Exception:
            continue
        if version == "3.11":
            return candidate

    raise RuntimeError("未找到可用的 Python 3.11（可设置 VF_PYTHON_BIN 或创建 .venv）")


def _wait_server_ready(process: subprocess.Popen, base_url: str, timeout_seconds: float = 20.0):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if process.poll() is not None:
            output = ""
            if process.stdout:
                output = process.stdout.read()
            raise RuntimeError(f"服务进程提前退出: {output.strip()}")

        try:
            response = httpx.get(f"{base_url}/api/health", timeout=1.0)
            if response.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"服务未在 {timeout_seconds}s 内就绪: {base_url}")


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    home_dir = tmp_path_factory.mktemp("vf-e2e-home")
    try:
        python_bin = _resolve_python_3_11()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["VF_DB_PATH"] = str(home_dir / "video_factory.db")

    pythonpath_items = [str(PROJECT_ROOT), str(PROJECT_ROOT / "src")]
    if env.get("PYTHONPATH"):
        pythonpath_items.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_items)

    base_url = "http://127.0.0.1:9010"
    cmd = [
        python_bin,
        "-m",
        "uvicorn",
        "api.server:app",
        "--host",
        "127.0.0.1",
        "--port",
        "9010",
        "--log-level",
        "warning",
    ]
    process = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        _wait_server_ready(process, base_url)
    except Exception as exc:
        process.terminate()
        pytest.skip(f"当前环境无法启动本地 HTTP 服务，跳过 E2E: {exc}")

    yield base_url

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


@pytest.fixture(scope="module")
def live_server_home(tmp_path_factory):
    candidates = sorted(tmp_path_factory.getbasetemp().glob("vf-e2e-home*"))
    if not candidates:
        pytest.skip("未找到 E2E 隔离 HOME")
    return candidates[-1]


@pytest.fixture(scope="module")
def browser_page():
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except Exception:
        pytest.skip("playwright 未安装，跳过 E2E")

    with sync_playwright() as playwright:
        browser = None
        launch_errors = []

        launch_attempts = [
            {"headless": True},
            {"headless": True, "channel": "chrome"},
        ]

        local_browser_paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
        for browser_path in local_browser_paths:
            if Path(browser_path).exists():
                launch_attempts.append({"headless": True, "executable_path": browser_path})

        for kwargs in launch_attempts:
            try:
                browser = playwright.chromium.launch(**kwargs)
                break
            except PlaywrightError as exc:
                launch_errors.append(str(exc))

        if browser is None:
            detail = " | ".join(launch_errors[-2:]) if launch_errors else "未知错误"
            pytest.skip(f"Chromium/Chrome 不可用，跳过 E2E: {detail}")

        page = browser.new_page()
        yield page
        browser.close()


def test_dashboard_loads(live_server, browser_page):
    page = browser_page
    page.goto(f"{live_server}/", wait_until="domcontentloaded")
    page.screenshot(path=str(ARTIFACT_DIR / "e2e_dashboard.png"), full_page=True)

    assert page.locator("h1", has_text="总览").count() > 0
    assert page.locator("div[hx-get='/web/partials/stats_cards']").count() == 1
    assert page.locator("div[hx-get='/web/partials/active_tasks']").count() == 1


def test_create_task_api_and_tasks_page_render(live_server, browser_page):
    page = browser_page
    response = page.request.post(
        f"{live_server}/api/tasks/create",
        form={
            "youtube_url": "https://www.youtube.com/watch?v=e2e_case",
            "source_lang": "en",
            "target_lang": "zh_cn",
            "create_clips": "on",
            "create_article": "on",
        },
    )
    assert response.ok
    task_id = response.json()["task_id"]

    page.goto(f"{live_server}/tasks", wait_until="domcontentloaded")
    page.screenshot(path=str(ARTIFACT_DIR / "e2e_tasks.png"), full_page=True)
    assert page.locator("#task-list-container").count() == 1
    assert page.locator("text=" + task_id[:8]).count() > 0


def test_new_task_page_submit_button_creates_task(live_server, browser_page):
    page = browser_page
    page.goto(f"{live_server}/tasks/new", wait_until="domcontentloaded")

    page.locator("input[name='youtube_url']").fill("https://www.youtube.com/watch?v=e2e_form_case")
    page.get_by_role("button", name="创建任务").click()

    page.wait_for_url(f"{live_server}/tasks", timeout=3000)
    page.wait_for_timeout(300)

    assert page.locator("#task-list-container").count() == 1
    assert page.locator("text=e2e_form_case").count() > 0


def _seed_task(
    home_dir: Path,
    task_id: str,
    title: str,
    state: str,
    **overrides,
):
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from core.task import Task, TaskStore

    store = TaskStore(store_path=str(home_dir / ".video-factory" / "tasks.json"))
    task = Task(
        task_id=task_id,
        source_url="https://example.com/video",
        source_title=title,
        source_lang="en",
        target_lang="zh_cn",
    )
    task.state = state
    for key, value in overrides.items():
        setattr(task, key, value)
    store.update(task)


def test_tasks_page_honors_status_query_filter(live_server, live_server_home, browser_page):
    page = browser_page

    _seed_task(
        live_server_home,
        "vf_e2e_completed_filter",
        "筛选完成任务",
        "completed",
        progress=100,
    )
    _seed_task(
        live_server_home,
        "vf_e2e_failed_filter",
        "筛选失败任务",
        "failed",
        progress=63,
    )

    page.goto(f"{live_server}/tasks?status=completed", wait_until="domcontentloaded")
    page.locator("text=筛选完成任务").wait_for(timeout=3000)
    page.wait_for_timeout(300)

    assert page.locator("text=筛选完成任务").count() > 0
    assert page.locator("text=筛选失败任务").count() == 0


def test_task_detail_page_renders_translation_and_failed_step_context(live_server, live_server_home, browser_page):
    page = browser_page
    now = time.time()
    task_id = "vf_e2e_failed_detail"
    timeline = [
        {"event": "task_created", "timestamp": now - 300, "to_state": "queued"},
        {"event": "state_transition", "timestamp": now - 240, "to_state": "downloading"},
        {"event": "step_transition", "timestamp": now - 180, "to_step": "translating"},
        {"event": "state_transition", "timestamp": now - 120, "to_state": "translating"},
        {"event": "step_transition", "timestamp": now - 30, "to_step": "failed"},
        {"event": "state_transition", "timestamp": now - 20, "to_state": "failed"},
    ]
    _seed_task(
        live_server_home,
        task_id,
        "详情失败任务",
        "failed",
        progress=67,
        last_step="failed",
        translated_title="失败任务中文标题",
        translation_task_id="selfhosted_whisper_vf_e2e_failed_detail",
        translation_progress=67,
        qc_score=58,
        qc_details="字幕覆盖率不足",
        timeline=timeline,
        error_message="翻译阶段失败",
    )

    page.goto(f"{live_server}/tasks/{task_id}", wait_until="domcontentloaded")
    page.locator("#task-title", has_text="详情失败任务").wait_for(timeout=3000)
    page.wait_for_timeout(300)

    assert page.locator("#task-state").text_content() == "失败"
    assert page.locator("#task-last-step").text_content() == "翻译中（失败）"
    assert page.locator("#task-translated-title").text_content() == "失败任务中文标题"
    assert "selfhosted_whisper_vf_e2e_failed_detail" in page.locator("#task-translation-meta").text_content()
    assert "67%" in page.locator("#task-translation-meta").text_content()


def test_publish_storage_settings_pages_load(live_server, browser_page):
    page = browser_page
    checks = [
        ("/publish", "发布管理"),
        ("/storage", "存储管理"),
        ("/settings", "设置"),
    ]
    for path, heading in checks:
        page.goto(f"{live_server}{path}", wait_until="domcontentloaded")
        assert page.locator("h1", has_text=heading).count() > 0


def test_accounts_page_can_create_and_validate_account(live_server, browser_page, tmp_path):
    page = browser_page
    cookie_file = tmp_path / "douyin_cookie.json"
    cookie_file.write_text("{}", encoding="utf-8")

    page.goto(f"{live_server}/accounts", wait_until="domcontentloaded")
    assert page.locator("text=Cookie 归档目录").count() > 0
    page.select_option("select", "douyin")
    page.locator("input[placeholder='输入账号名']").fill("抖音主号")
    page.locator("input[type='file']").set_input_files(str(cookie_file))
    page.get_by_role("button", name="添加").click()

    page.wait_for_timeout(300)
    assert page.locator("text=抖音主号").count() > 0
    page.get_by_role("button", name="检测").first.click()
    page.wait_for_timeout(300)
    assert page.locator("text=active").count() > 0


def _seed_ready_publish_task(home_dir: Path, task_id: str, title: str):
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from core.task import Task, TaskState, TaskStore

    store = TaskStore(store_path=str(home_dir / ".video-factory" / "tasks.json"))
    task = Task(task_id=task_id, source_url="https://example.com/video", source_title=title)
    task.state = TaskState.READY_TO_PUBLISH.value
    task.products = [
        {
            "type": "long_video",
            "platform": "all",
            "local_path": "/tmp/video.mp4",
            "title": title,
            "description": "desc",
            "tags": ["tag"],
        }
    ]
    store.update(task)


def _wait_task_state(page, base_url: str, task_id: str, expected_state: str, timeout_seconds: float = 8.0):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = page.request.get(f"{base_url}/api/tasks/{task_id}")
        if response.ok and response.json()["state"] == expected_state:
            return response.json()
        page.wait_for_timeout(200)
    raise AssertionError(f"任务 {task_id} 未在 {timeout_seconds}s 内进入状态 {expected_state}")


def test_publish_page_supports_cancel_retry_manual_and_partial_recovery(live_server, live_server_home, browser_page, tmp_path):
    page = browser_page

    bilibili_cookie = tmp_path / "bilibili_cookie.json"
    youtube_cookie = tmp_path / "youtube_cookie.json"
    bilibili_cookie.write_text("{}", encoding="utf-8")
    youtube_cookie.write_text("{}", encoding="utf-8")

    bilibili_account = page.request.post(
        f"{live_server}/api/publish/accounts",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"platform": "bilibili", "name": "B站运营号", "cookie_path": str(bilibili_cookie), "is_default": True}),
    ).json()["account_id"]
    youtube_account = page.request.post(
        f"{live_server}/api/publish/accounts",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"platform": "youtube", "name": "YouTube 主号", "cookie_path": str(youtube_cookie), "is_default": True}),
    ).json()["account_id"]

    cancel_task_id = "vf_e2e_cancel"
    _seed_ready_publish_task(live_server_home, cancel_task_id, "取消流程")
    cancel_resp = page.request.post(
        f"{live_server}/api/distribute/publish",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"task_id": cancel_task_id, "platforms": ["bilibili"], "publish_accounts": {"bilibili": bilibili_account}}),
    )
    assert cancel_resp.ok

    page.goto(f"{live_server}/publish", wait_until="domcontentloaded")
    cancel_row = page.locator("tr", has_text="取消流程").first
    cancel_row.wait_for()
    page.once("dialog", lambda dialog: dialog.accept())
    cancel_row.get_by_role("button", name="取消").click()
    expect_state = _wait_task_state(page, live_server, cancel_task_id, "failed")
    assert "失败或取消 1 个平台" in expect_state["error_message"]

    partial_task_id = "vf_e2e_partial_recovery"
    _seed_ready_publish_task(live_server_home, partial_task_id, "部分恢复流程")
    publish_resp = page.request.post(
        f"{live_server}/api/distribute/publish",
        headers={"Content-Type": "application/json"},
        data=json.dumps({
            "task_id": partial_task_id,
            "platforms": ["bilibili", "youtube"],
            "publish_accounts": {"bilibili": bilibili_account, "youtube": youtube_account},
        }),
    )
    assert publish_resp.ok

    page.goto(f"{live_server}/publish", wait_until="domcontentloaded")
    bilibili_row = page.locator("tr", has_text="部分恢复流程").filter(has_text="B站").first
    youtube_row = page.locator("tr", has_text="部分恢复流程").filter(has_text="YouTube").first
    bilibili_row.wait_for()
    youtube_row.wait_for()

    page.once("dialog", lambda dialog: dialog.accept("cookie bad"))
    bilibili_row.get_by_role("button", name="标记失败").click()

    page.once("dialog", lambda dialog: dialog.accept("https://example.com/youtube"))
    youtube_row.get_by_role("button", name="标记已发布").click()

    partial_state = _wait_task_state(page, live_server, partial_task_id, "partial_success")
    assert "成功 1/2" in partial_state["error_message"]

    retry_row = page.locator("tr", has_text="部分恢复流程").filter(has_text="B站").first
    retry_row.get_by_role("button", name="重试").click()
    page.wait_for_timeout(500)

    page.once("dialog", lambda dialog: dialog.accept("https://example.com/bilibili"))
    retry_row = page.locator("tr", has_text="部分恢复流程").filter(has_text="B站").first
    retry_row.get_by_role("button", name="标记已发布").click()

    completed_state = _wait_task_state(page, live_server, partial_task_id, "completed")
    assert completed_state["error_message"] == ""
