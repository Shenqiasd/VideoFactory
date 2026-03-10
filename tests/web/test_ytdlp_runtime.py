from production.pipeline import ProductionPipeline
from source import ytdlp_runtime


def test_build_ytdlp_base_cmd_ignores_user_config_and_enables_node(monkeypatch):
    monkeypatch.setattr(ytdlp_runtime, "resolve_ytdlp_cmd", lambda: ["yt-dlp"])
    monkeypatch.setattr(ytdlp_runtime, "has_yt_dlp_ejs", lambda: True)
    monkeypatch.setattr(
        ytdlp_runtime.shutil,
        "which",
        lambda name: "/usr/local/bin/node" if name == "node" else None,
    )

    cmd = ytdlp_runtime.build_ytdlp_base_cmd()

    assert cmd[:2] == ["yt-dlp", "--ignore-config"]
    assert "--no-js-runtimes" in cmd
    assert cmd[cmd.index("--js-runtimes"):cmd.index("--js-runtimes") + 2] == ["--js-runtimes", "node"]
    assert "ejs:github" in cmd
    assert "ejs:npm" in cmd


def test_build_ytdlp_base_cmd_prefers_bun_over_node_when_deno_missing(monkeypatch):
    monkeypatch.setattr(ytdlp_runtime, "resolve_ytdlp_cmd", lambda: ["yt-dlp"])
    monkeypatch.setattr(ytdlp_runtime, "has_yt_dlp_ejs", lambda: True)
    monkeypatch.setattr(
        ytdlp_runtime.shutil,
        "which",
        lambda name: f"/usr/local/bin/{name}" if name in {"bun", "node"} else None,
    )

    cmd = ytdlp_runtime.build_ytdlp_base_cmd()

    assert cmd[cmd.index("--js-runtimes"):cmd.index("--js-runtimes") + 2] == ["--js-runtimes", "bun"]
    assert "ejs:github" in cmd
    assert "ejs:npm" in cmd


def test_build_ytdlp_base_cmd_without_runtime_support(monkeypatch):
    monkeypatch.setattr(ytdlp_runtime, "resolve_ytdlp_cmd", lambda: ["yt-dlp"])
    monkeypatch.setattr(ytdlp_runtime, "has_yt_dlp_ejs", lambda: False)
    monkeypatch.setattr(ytdlp_runtime.shutil, "which", lambda name: None)

    cmd = ytdlp_runtime.build_ytdlp_base_cmd()

    assert cmd == ["yt-dlp", "--ignore-config"]


def test_resolve_ytdlp_cmd_prefers_current_python_runtime(monkeypatch, tmp_path):
    runtime_python = tmp_path / "venv" / "bin" / "python"
    runtime_python.parent.mkdir(parents=True, exist_ok=True)
    runtime_python.write_text("", encoding="utf-8")
    runtime_bin = runtime_python.parent / "yt-dlp"
    runtime_bin.write_text("", encoding="utf-8")

    monkeypatch.setattr(ytdlp_runtime.sys, "executable", str(runtime_python))
    monkeypatch.setattr(ytdlp_runtime.shutil, "which", lambda name: "/usr/local/bin/yt-dlp" if name == "yt-dlp" else None)

    assert ytdlp_runtime.resolve_ytdlp_cmd() == [str(runtime_bin)]


def test_resolve_ytdlp_cmd_falls_back_to_python_module(monkeypatch, tmp_path):
    runtime_python = tmp_path / "venv" / "bin" / "python"
    runtime_python.parent.mkdir(parents=True, exist_ok=True)
    runtime_python.write_text("", encoding="utf-8")

    monkeypatch.setattr(ytdlp_runtime.sys, "executable", str(runtime_python))
    monkeypatch.setattr(ytdlp_runtime.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        ytdlp_runtime.importlib.util,
        "find_spec",
        lambda name: object() if name == "yt_dlp" else None,
    )

    assert ytdlp_runtime.resolve_ytdlp_cmd() == [str(runtime_python), "-m", "yt_dlp"]


def test_classify_download_failure_reports_js_runtime_issue():
    code, message = ProductionPipeline.classify_download_failure(
        'WARNING: [youtube] [jsc] JS Challenge Provider "node" returned an invalid response',
        has_cookies=True,
    )

    assert code == "DOWNLOAD_YTDLP_JS_RUNTIME"
    assert "yt-dlp" in message
