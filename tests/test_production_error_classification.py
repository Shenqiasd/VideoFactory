import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from production.pipeline import ProductionPipeline  # noqa: E402


def test_classify_download_failure_bot_without_cookies():
    code, message = ProductionPipeline.classify_download_failure(
        "ERROR: Sign in to confirm you're not a bot.",
        has_cookies=False,
    )
    assert code == "DOWNLOAD_BOT_VERIFICATION"
    assert "配置 Cookies" in message


def test_classify_download_failure_bot_with_cookies():
    code, message = ProductionPipeline.classify_download_failure(
        "ERROR: Sign in to confirm you're not a bot.",
        has_cookies=True,
    )
    assert code == "DOWNLOAD_COOKIES_INVALID"
    assert "无效或已过期" in message


def test_classify_download_failure_dns():
    code, message = ProductionPipeline.classify_download_failure(
        "Failed to resolve 'www.youtube.com' (nodename nor servname provided)",
        has_cookies=False,
    )
    assert code == "DOWNLOAD_NETWORK_ERROR"
    assert "DNS" in message or "网络" in message


def test_classify_download_failure_fallback():
    code, message = ProductionPipeline.classify_download_failure(
        "some unknown yt-dlp stderr",
        has_cookies=False,
    )
    assert code == "DOWNLOAD_EXEC_FAILED"
    assert "下载失败" in message
