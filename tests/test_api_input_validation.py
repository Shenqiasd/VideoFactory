"""Tests for API input validation."""
import pytest
from pydantic import ValidationError
from api.routes.production import SubmitAndRunRequest


class TestSourceUrlValidation:
    def test_valid_https_url(self):
        req = SubmitAndRunRequest(source_url="https://www.youtube.com/watch?v=abc123")
        assert req.source_url == "https://www.youtube.com/watch?v=abc123"

    def test_valid_http_url(self):
        req = SubmitAndRunRequest(source_url="http://example.com/video.mp4")
        assert "http://" in req.source_url

    def test_valid_local_path(self):
        req = SubmitAndRunRequest(source_url="/tmp/video.mp4")
        assert req.source_url == "/tmp/video.mp4"

    def test_empty_url_rejected(self):
        with pytest.raises(ValidationError, match="source_url"):
            SubmitAndRunRequest(source_url="  ")

    def test_invalid_url_rejected(self):
        with pytest.raises(ValidationError, match="source_url"):
            SubmitAndRunRequest(source_url="not-a-url")


class TestLanguageValidation:
    def test_valid_lang(self):
        req = SubmitAndRunRequest(source_url="https://x.com", source_lang="en", target_lang="zh_cn")
        assert req.source_lang == "en"

    def test_invalid_lang_rejected(self):
        with pytest.raises(ValidationError, match="语言"):
            SubmitAndRunRequest(source_url="https://x.com", source_lang="invalid_lang_xyz")


class TestEmbedTypeValidation:
    def test_valid_embed_type(self):
        req = SubmitAndRunRequest(source_url="https://x.com", embed_subtitle_type="vertical")
        assert req.embed_subtitle_type == "vertical"

    def test_invalid_embed_type_rejected(self):
        with pytest.raises(ValidationError, match="embed_subtitle_type"):
            SubmitAndRunRequest(source_url="https://x.com", embed_subtitle_type="diagonal")
