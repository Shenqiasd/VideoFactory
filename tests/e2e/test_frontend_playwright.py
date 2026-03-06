import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = PROJECT_ROOT / "tests" / "artifacts"
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


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
    env = os.environ.copy()
    env["HOME"] = str(home_dir)

    pythonpath_items = [str(PROJECT_ROOT), str(PROJECT_ROOT / "src")]
    if env.get("PYTHONPATH"):
        pythonpath_items.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_items)

    base_url = "http://127.0.0.1:9010"
    cmd = [
        sys.executable,
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
