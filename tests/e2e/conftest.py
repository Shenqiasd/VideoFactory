"""E2E测试共享fixtures"""
import os
import subprocess
import sys
import time
from pathlib import Path
import httpx
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _wait_server_ready(process: subprocess.Popen, base_url: str, timeout_seconds: float = 20.0):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"服务进程提前退出")
        try:
            response = httpx.get(f"{base_url}/api/health", timeout=1.0)
            if response.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"服务未在 {timeout_seconds}s 内就绪")


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    home_dir = tmp_path_factory.mktemp("vf-e2e-home")
    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["PYTHONPATH"] = os.pathsep.join([str(PROJECT_ROOT), str(PROJECT_ROOT / "src")])

    base_url = "http://127.0.0.1:9010"
    cmd = [sys.executable, "-m", "uvicorn", "api.server:app", "--host", "127.0.0.1", "--port", "9010", "--log-level", "warning"]
    process = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    try:
        _wait_server_ready(process, base_url)
    except Exception as exc:
        process.terminate()
        pytest.skip(f"无法启动服务: {exc}")

    yield base_url

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
