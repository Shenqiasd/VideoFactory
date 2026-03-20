"""
YouTube频道监控测试 - 测试 MonitoredChannel、去重、退避、状态持久化
"""
import sys
import json
import time
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from source.youtube_monitor import MonitoredChannel, YouTubeMonitor


# ========== MonitoredChannel 序列化/反序列化 ==========

class TestMonitoredChannel:

    def test_to_dict(self):
        ch = MonitoredChannel(
            channel_id="UC123",
            name="TestChannel",
            enabled=True,
            check_interval=1800,
            default_scope="subtitle_only",
        )
        d = ch.to_dict()
        assert d["channel_id"] == "UC123"
        assert d["name"] == "TestChannel"
        assert d["enabled"] is True
        assert d["check_interval"] == 1800
        assert d["default_scope"] == "subtitle_only"
        assert d["consecutive_failures"] == 0

    def test_from_dict(self):
        data = {
            "channel_id": "UC456",
            "name": "AnotherChannel",
            "enabled": False,
            "check_interval": 7200,
            "consecutive_failures": 3,
            "extra_field": "should_be_ignored",
        }
        ch = MonitoredChannel.from_dict(data)
        assert ch.channel_id == "UC456"
        assert ch.enabled is False
        assert ch.check_interval == 7200
        assert ch.consecutive_failures == 3

    def test_roundtrip(self):
        original = MonitoredChannel(
            channel_id="UC789",
            name="Roundtrip",
            check_interval=900,
            default_target_lang="ja",
            max_video_duration=3600,
        )
        restored = MonitoredChannel.from_dict(original.to_dict())
        assert restored.channel_id == original.channel_id
        assert restored.check_interval == original.check_interval
        assert restored.max_video_duration == original.max_video_duration


# ========== is_duplicate ==========

class TestIsDuplicate:

    def test_duplicate_in_seen_set(self, tmp_path):
        monitor = YouTubeMonitor(state_file=str(tmp_path / "state.json"))
        monitor._seen_videos.add("https://www.youtube.com/watch?v=abc123")
        mock_store = SimpleNamespace(list_all=lambda: [])
        assert monitor.is_duplicate("https://www.youtube.com/watch?v=abc123", mock_store) is True

    def test_duplicate_in_task_store(self, tmp_path):
        monitor = YouTubeMonitor(state_file=str(tmp_path / "state.json"))
        mock_task = SimpleNamespace(source_url="https://www.youtube.com/watch?v=xyz789")
        mock_store = SimpleNamespace(list_all=lambda: [mock_task])
        assert monitor.is_duplicate("https://www.youtube.com/watch?v=xyz789", mock_store) is True

    def test_not_duplicate(self, tmp_path):
        monitor = YouTubeMonitor(state_file=str(tmp_path / "state.json"))
        monitor._seen_videos.add("https://www.youtube.com/watch?v=abc123")
        mock_task = SimpleNamespace(source_url="https://www.youtube.com/watch?v=existing")
        mock_store = SimpleNamespace(list_all=lambda: [mock_task])
        assert monitor.is_duplicate("https://www.youtube.com/watch?v=new_video", mock_store) is False


# ========== should_check / _backoff_seconds ==========

class TestBackoff:

    def test_should_check_disabled_channel(self, tmp_path):
        monitor = YouTubeMonitor(state_file=str(tmp_path / "state.json"))
        ch = MonitoredChannel(channel_id="UC1", enabled=False)
        assert monitor.should_check(ch) is False

    def test_should_check_never_checked(self, tmp_path):
        monitor = YouTubeMonitor(state_file=str(tmp_path / "state.json"))
        ch = MonitoredChannel(channel_id="UC1", enabled=True, last_checked_at=0.0)
        assert monitor.should_check(ch) is True

    def test_should_check_recently_checked(self, tmp_path):
        monitor = YouTubeMonitor(state_file=str(tmp_path / "state.json"))
        ch = MonitoredChannel(
            channel_id="UC1", enabled=True,
            check_interval=3600, last_checked_at=time.time(),
        )
        assert monitor.should_check(ch) is False

    def test_should_check_overdue(self, tmp_path):
        monitor = YouTubeMonitor(state_file=str(tmp_path / "state.json"))
        ch = MonitoredChannel(
            channel_id="UC1", enabled=True,
            check_interval=3600, last_checked_at=time.time() - 4000,
        )
        assert monitor.should_check(ch) is True

    def test_backoff_no_failures(self, tmp_path):
        monitor = YouTubeMonitor(state_file=str(tmp_path / "state.json"))
        ch = MonitoredChannel(channel_id="UC1", check_interval=3600, consecutive_failures=0)
        assert monitor._backoff_seconds(ch) == 3600.0

    def test_backoff_one_failure(self, tmp_path):
        monitor = YouTubeMonitor(state_file=str(tmp_path / "state.json"))
        ch = MonitoredChannel(channel_id="UC1", check_interval=3600, consecutive_failures=1)
        assert monitor._backoff_seconds(ch) == 7200.0

    def test_backoff_two_failures(self, tmp_path):
        monitor = YouTubeMonitor(state_file=str(tmp_path / "state.json"))
        ch = MonitoredChannel(channel_id="UC1", check_interval=3600, consecutive_failures=2)
        assert monitor._backoff_seconds(ch) == 14400.0

    def test_backoff_capped_at_24x(self, tmp_path):
        monitor = YouTubeMonitor(state_file=str(tmp_path / "state.json"))
        ch = MonitoredChannel(channel_id="UC1", check_interval=3600, consecutive_failures=10)
        assert monitor._backoff_seconds(ch) == 86400.0

    def test_backoff_large_failures_still_capped(self, tmp_path):
        monitor = YouTubeMonitor(state_file=str(tmp_path / "state.json"))
        ch = MonitoredChannel(channel_id="UC1", check_interval=3600, consecutive_failures=100)
        assert monitor._backoff_seconds(ch) == 86400.0

    def test_should_check_with_backoff(self, tmp_path):
        monitor = YouTubeMonitor(state_file=str(tmp_path / "state.json"))
        ch = MonitoredChannel(
            channel_id="UC1", enabled=True,
            check_interval=3600, consecutive_failures=2,
            last_checked_at=time.time() - 5000,
        )
        # backoff = 3600 * 4 = 14400, 5000 < 14400
        assert monitor.should_check(ch) is False


# ========== 状态持久化 ==========

class TestStatePersistence:

    def test_save_and_load(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        monitor = YouTubeMonitor(state_file=state_file)
        monitor.add_channel("UC111", name="Channel1", check_interval=1800)
        monitor.add_channel("UC222", name="Channel2", default_scope="subtitle_only")
        monitor.mark_seen("video1")
        monitor.mark_seen("video2")
        monitor._auto_created_count = 5
        monitor._save_state()

        monitor2 = YouTubeMonitor(state_file=state_file)
        assert len(monitor2.channels) == 2
        assert monitor2.channels[0].channel_id == "UC111"
        assert monitor2.channels[0].check_interval == 1800
        assert monitor2.channels[1].default_scope == "subtitle_only"
        assert "video1" in monitor2._seen_videos
        assert "video2" in monitor2._seen_videos
        assert monitor2._auto_created_count == 5

    def test_backward_compat_old_format(self, tmp_path):
        state_file = tmp_path / "state.json"
        old_data = {
            "seen_videos": ["vid1", "vid2"],
            "channels": [
                {"channel_id": "UC_OLD1", "name": "OldChannel1"},
                {"channel_id": "UC_OLD2", "name": "OldChannel2"},
            ],
        }
        state_file.write_text(json.dumps(old_data))

        monitor = YouTubeMonitor(state_file=str(state_file))
        assert len(monitor.channels) == 2
        assert isinstance(monitor.channels[0], MonitoredChannel)
        assert monitor.channels[0].channel_id == "UC_OLD1"
        assert monitor.channels[0].enabled is True
        assert "vid1" in monitor._seen_videos

    def test_add_and_remove_channel(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        monitor = YouTubeMonitor(state_file=state_file)
        monitor.add_channel("UC_A", name="A")
        monitor.add_channel("UC_B", name="B")
        assert len(monitor.channels) == 2
        monitor.remove_channel("UC_A")
        assert len(monitor.channels) == 1
        assert monitor.channels[0].channel_id == "UC_B"

    def test_toggle_channel(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        monitor = YouTubeMonitor(state_file=state_file)
        monitor.add_channel("UC_T", name="Toggle")
        result = monitor.toggle_channel("UC_T", False)
        assert result is not None
        assert result.enabled is False
        result = monitor.toggle_channel("UC_T", True)
        assert result.enabled is True
        result = monitor.toggle_channel("UC_NONEXIST", True)
        assert result is None


# ========== check_channel (mock yt-dlp) ==========

class TestCheckChannel:

    @pytest.mark.asyncio
    async def test_check_channel_success(self, tmp_path):
        monitor = YouTubeMonitor(state_file=str(tmp_path / "state.json"))
        mock_stdout = (
            '{"id": "abc123", "title": "Video 1", "url": "https://www.youtube.com/watch?v=abc123", "duration": 300}\n'
            '{"id": "def456", "title": "Video 2", "url": "https://www.youtube.com/watch?v=def456", "duration": 600}\n'
        )
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(mock_stdout.encode(), b""))
        mock_process.returncode = 0
        with patch("source.youtube_monitor.build_ytdlp_base_cmd", return_value=["yt-dlp"]), \
             patch("asyncio.create_subprocess_exec", return_value=mock_process):
            videos = await monitor.check_channel("UC_TEST")
        assert len(videos) == 2
        assert videos[0]["video_id"] == "abc123"
        assert videos[1]["video_id"] == "def456"

    @pytest.mark.asyncio
    async def test_check_channel_filters_seen(self, tmp_path):
        monitor = YouTubeMonitor(state_file=str(tmp_path / "state.json"))
        monitor._seen_videos.add("abc123")
        mock_stdout = (
            '{"id": "abc123", "title": "Already Seen", "url": "x", "duration": 300}\n'
            '{"id": "new789", "title": "New Video", "url": "y", "duration": 600}\n'
        )
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(mock_stdout.encode(), b""))
        mock_process.returncode = 0
        with patch("source.youtube_monitor.build_ytdlp_base_cmd", return_value=["yt-dlp"]), \
             patch("asyncio.create_subprocess_exec", return_value=mock_process):
            videos = await monitor.check_channel("UC_TEST")
        assert len(videos) == 1
        assert videos[0]["video_id"] == "new789"

    @pytest.mark.asyncio
    async def test_check_channel_failure(self, tmp_path):
        monitor = YouTubeMonitor(state_file=str(tmp_path / "state.json"))
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b"Error"))
        mock_process.returncode = 1
        with patch("source.youtube_monitor.build_ytdlp_base_cmd", return_value=["yt-dlp"]), \
             patch("asyncio.create_subprocess_exec", return_value=mock_process):
            videos = await monitor.check_channel("UC_FAIL")
        assert videos == []

    @pytest.mark.asyncio
    async def test_check_channel_timeout(self, tmp_path):
        monitor = YouTubeMonitor(state_file=str(tmp_path / "state.json"))
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        with patch("source.youtube_monitor.build_ytdlp_base_cmd", return_value=["yt-dlp"]), \
             patch("asyncio.create_subprocess_exec", return_value=mock_process):
            videos = await monitor.check_channel("UC_TIMEOUT")
        assert videos == []
