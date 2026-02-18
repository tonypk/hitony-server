"""Tests for builtin tools — player, timer, volume, etc."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tools.registry import ToolResult


# ──────────────────────────────────────────────────────────
# Player tools
# ──────────────────────────────────────────────────────────

class TestPlayerPause:
    @pytest.mark.asyncio
    async def test_pause_playing(self, playing_session):
        from app.tools.builtin.player import player_pause
        result = await player_pause(session=playing_session)
        assert result.type == "tts"
        assert "暂停" in result.text
        assert playing_session.music_paused is True

    @pytest.mark.asyncio
    async def test_pause_nothing_playing(self, mock_session):
        from app.tools.builtin.player import player_pause
        result = await player_pause(session=mock_session)
        assert "没有" in result.text

    @pytest.mark.asyncio
    async def test_pause_no_session(self):
        from app.tools.builtin.player import player_pause
        result = await player_pause(session=None)
        assert "没有" in result.text


class TestPlayerResume:
    @pytest.mark.asyncio
    async def test_resume_paused(self, paused_session):
        from app.tools.builtin.player import player_resume
        result = await player_resume(session=paused_session)
        assert "继续" in result.text
        assert paused_session.music_paused is False

    @pytest.mark.asyncio
    async def test_resume_not_paused(self, mock_session):
        from app.tools.builtin.player import player_resume
        result = await player_resume(session=mock_session)
        assert "没有" in result.text


class TestPlayerStop:
    @pytest.mark.asyncio
    async def test_stop_playing(self, playing_session):
        from app.tools.builtin.player import player_stop
        result = await player_stop(session=playing_session)
        assert "停止" in result.text
        assert playing_session.music_abort is True

    @pytest.mark.asyncio
    async def test_stop_nothing_playing(self, mock_session):
        from app.tools.builtin.player import player_stop
        result = await player_stop(session=mock_session)
        assert "没有" in result.text


# ──────────────────────────────────────────────────────────
# Timer tools
# ──────────────────────────────────────────────────────────

class TestTimerSet:
    @pytest.mark.asyncio
    async def test_valid_seconds(self, mock_session):
        from app.tools.builtin.timer import timer_set
        result = await timer_set(seconds="300", label="测试", session=mock_session)
        assert result.type == "tts"
        assert "5分钟" in result.text

    @pytest.mark.asyncio
    async def test_invalid_seconds(self, mock_session):
        from app.tools.builtin.timer import timer_set
        result = await timer_set(seconds="abc", session=mock_session)
        assert "没有理解" in result.text

    @pytest.mark.asyncio
    async def test_zero_seconds(self, mock_session):
        from app.tools.builtin.timer import timer_set
        result = await timer_set(seconds="0", session=mock_session)
        assert "大于0" in result.text

    @pytest.mark.asyncio
    async def test_too_long(self, mock_session):
        from app.tools.builtin.timer import timer_set
        result = await timer_set(seconds="9999", session=mock_session)
        assert "2小时" in result.text

    @pytest.mark.asyncio
    async def test_no_session(self):
        from app.tools.builtin.timer import timer_set
        result = await timer_set(seconds="60", session=None)
        assert result.type == "error"

    @pytest.mark.asyncio
    async def test_seconds_only(self, mock_session):
        from app.tools.builtin.timer import timer_set
        result = await timer_set(seconds="30", session=mock_session)
        assert "30秒" in result.text


class TestTimerCancel:
    @pytest.mark.asyncio
    async def test_no_active_timers(self, mock_session):
        from app.tools.builtin.timer import timer_cancel
        result = await timer_cancel(session=mock_session)
        assert "没有" in result.text

    @pytest.mark.asyncio
    async def test_no_session(self):
        from app.tools.builtin.timer import timer_cancel
        result = await timer_cancel(session=None)
        assert result.type == "error"


# ──────────────────────────────────────────────────────────
# Volume tools
# ──────────────────────────────────────────────────────────

class TestVolumeSet:
    @pytest.mark.asyncio
    async def test_set_50(self, mock_session):
        from app.tools.builtin.volume import volume_set
        with patch("app.tools.builtin.volume._send_volume", new_callable=AsyncMock, return_value=True):
            result = await volume_set(level=50, session=mock_session)
        assert result.type == "tts"
        assert "50" in result.text
        assert mock_session.volume == 50

    @pytest.mark.asyncio
    async def test_set_zero_mute(self, mock_session):
        from app.tools.builtin.volume import volume_set
        with patch("app.tools.builtin.volume._send_volume", new_callable=AsyncMock, return_value=True):
            result = await volume_set(level=0, session=mock_session)
        assert "静音" in result.text

    @pytest.mark.asyncio
    async def test_clamp_over_100(self, mock_session):
        from app.tools.builtin.volume import volume_set
        with patch("app.tools.builtin.volume._send_volume", new_callable=AsyncMock, return_value=True):
            result = await volume_set(level=150, session=mock_session)
        assert mock_session.volume == 100

    @pytest.mark.asyncio
    async def test_no_session(self):
        from app.tools.builtin.volume import volume_set
        result = await volume_set(level=50, session=None)
        assert result.type == "error"


class TestVolumeUpDown:
    @pytest.mark.asyncio
    async def test_volume_up(self, mock_session):
        from app.tools.builtin.volume import volume_up
        mock_session.volume = 60
        with patch("app.tools.builtin.volume._send_volume", new_callable=AsyncMock, return_value=True):
            result = await volume_up(session=mock_session)
        assert mock_session.volume == 70
        assert "70" in result.text

    @pytest.mark.asyncio
    async def test_volume_up_at_max(self, mock_session):
        from app.tools.builtin.volume import volume_up
        mock_session.volume = 100
        with patch("app.tools.builtin.volume._send_volume", new_callable=AsyncMock, return_value=True):
            result = await volume_up(session=mock_session)
        assert mock_session.volume == 100

    @pytest.mark.asyncio
    async def test_volume_down(self, mock_session):
        from app.tools.builtin.volume import volume_down
        mock_session.volume = 60
        with patch("app.tools.builtin.volume._send_volume", new_callable=AsyncMock, return_value=True):
            result = await volume_down(session=mock_session)
        assert mock_session.volume == 50
        assert "50" in result.text

    @pytest.mark.asyncio
    async def test_volume_down_at_min(self, mock_session):
        from app.tools.builtin.volume import volume_down
        mock_session.volume = 0
        with patch("app.tools.builtin.volume._send_volume", new_callable=AsyncMock, return_value=True):
            result = await volume_down(session=mock_session)
        assert mock_session.volume == 0

    @pytest.mark.asyncio
    async def test_volume_up_device_disconnected(self, mock_session):
        """Volume up should return error when device not connected."""
        from app.tools.builtin.volume import volume_up
        mock_session.volume = 50
        with patch("app.tools.builtin.volume._send_volume", new_callable=AsyncMock, return_value=False):
            result = await volume_up(session=mock_session)
        assert mock_session.volume == 50  # state unchanged on failure
        assert result.type == "error"

    @pytest.mark.asyncio
    async def test_volume_down_device_disconnected(self, mock_session):
        """Volume down should return error when device not connected."""
        from app.tools.builtin.volume import volume_down
        mock_session.volume = 50
        with patch("app.tools.builtin.volume._send_volume", new_callable=AsyncMock, return_value=False):
            result = await volume_down(session=mock_session)
        assert mock_session.volume == 50  # state unchanged on failure
        assert result.type == "error"

    @pytest.mark.asyncio
    async def test_volume_set_low(self, mock_session):
        from app.tools.builtin.volume import volume_set
        with patch("app.tools.builtin.volume._send_volume", new_callable=AsyncMock, return_value=True):
            result = await volume_set(level=20, session=mock_session)
        assert "较小" in result.text

    @pytest.mark.asyncio
    async def test_volume_set_high(self, mock_session):
        from app.tools.builtin.volume import volume_set
        with patch("app.tools.builtin.volume._send_volume", new_callable=AsyncMock, return_value=True):
            result = await volume_set(level=90, session=mock_session)
        assert "较大" in result.text

    @pytest.mark.asyncio
    async def test_volume_set_device_not_connected(self, mock_session):
        from app.tools.builtin.volume import volume_set
        mock_session.volume = 30
        with patch("app.tools.builtin.volume._send_volume", new_callable=AsyncMock, return_value=False):
            result = await volume_set(level=50, session=mock_session)
        assert result.type == "error"
        assert "not connected" in result.text.lower() or "Device" in result.text
        assert mock_session.volume == 30  # state unchanged on failure


# ──────────────────────────────────────────────────────────
# SendVolume helper
# ──────────────────────────────────────────────────────────

class TestSendVolume:
    @pytest.mark.asyncio
    async def test_send_volume_no_session(self):
        from app.tools.builtin.volume import _send_volume
        session = MagicMock(spec=[])  # No attributes at all
        result = await _send_volume(session, 50)
        assert result is False

    @pytest.mark.asyncio
    async def test_send_volume_no_connection(self, mock_session):
        from app.tools.builtin.volume import _send_volume
        # The import is inside the function: from ..ws_server import get_active_connection
        with patch("app.ws_server.get_active_connection", return_value=None):
            result = await _send_volume(mock_session, 50)
        assert result is False

    @pytest.mark.asyncio
    async def test_send_volume_success(self, mock_session):
        from app.tools.builtin.volume import _send_volume
        mock_ws = AsyncMock()
        with patch("app.ws_server.get_active_connection", return_value=(mock_ws, mock_session)):
            result = await _send_volume(mock_session, 50)
        assert result is True
        mock_ws.send.assert_called_once()
