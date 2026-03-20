"""E2E测试共享fixtures"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
import httpx
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
    try:
        python_bin = _resolve_python_3_11()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["VF_DB_PATH"] = str(home_dir / "video_factory.db")
    env["VF_DISABLE_TITLE_RESOLVE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([str(PROJECT_ROOT), str(PROJECT_ROOT / "src")])

    base_url = "http://127.0.0.1:9010"
    cmd = [python_bin, "-m", "uvicorn", "api.server:app", "--host", "127.0.0.1", "--port", "9010", "--log-level", "warning"]
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


@pytest.fixture(scope="module")
def live_server_home(tmp_path_factory):
    candidates = sorted(tmp_path_factory.getbasetemp().glob("vf-e2e-home*"))
    if not candidates:
        pytest.skip("未找到 E2E 隔离 HOME")
    return candidates[-1]
