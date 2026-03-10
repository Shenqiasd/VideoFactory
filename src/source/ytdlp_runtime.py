"""
yt-dlp runtime helpers.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path


def has_yt_dlp_ejs() -> bool:
    return importlib.util.find_spec("yt_dlp_ejs") is not None


def has_yt_dlp_module() -> bool:
    return importlib.util.find_spec("yt_dlp") is not None


def resolve_ytdlp_cmd() -> list[str]:
    """
    Resolve yt-dlp from the active Python runtime before falling back to PATH.
    """
    runtime_bin = Path(sys.executable).resolve().parent / "yt-dlp"
    if runtime_bin.exists() and runtime_bin.is_file():
        return [str(runtime_bin)]

    system_bin = shutil.which("yt-dlp")
    if system_bin:
        return [system_bin]

    if has_yt_dlp_module():
        return [sys.executable, "-m", "yt_dlp"]

    raise FileNotFoundError(
        "yt-dlp is not installed in the current runtime. "
        "Run `./.venv/bin/python -m pip install yt-dlp` or reinstall `requirements.txt`."
    )


def select_js_runtime() -> str | None:
    """
    Prefer stable runtimes over inheriting user-machine defaults.

    Deno is the official recommendation for yt-dlp. If it is unavailable, prefer
    Bun on this machine before falling back to Node.
    """
    for runtime in ("deno", "bun", "node", "quickjs"):
        if shutil.which(runtime):
            return runtime
    return None


def build_ytdlp_base_cmd() -> list[str]:
    """
    Build a deterministic yt-dlp command prefix for this project.

    We ignore user-level configs because options in ~/.config/yt-dlp/config can
    break the service runtime. When yt-dlp-ejs and node are both available, we
    explicitly enable node so YouTube JS challenge solving does not depend on
    external machine state.
    """
    cmd = resolve_ytdlp_cmd() + ["--ignore-config"]

    runtime = select_js_runtime() if has_yt_dlp_ejs() else None
    if runtime:
        cmd.extend(["--no-js-runtimes", "--js-runtimes", runtime])
        # Allow yt-dlp to fetch fresher EJS challenge components when the
        # bundled yt-dlp-ejs distribution cannot solve a newly changed player.
        cmd.extend(["--remote-components", "ejs:github"])
        if runtime in {"bun", "deno", "node"}:
            cmd.extend(["--remote-components", "ejs:npm"])

    return cmd
