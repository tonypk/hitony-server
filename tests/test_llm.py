"""Tests for app/llm.py — conversation history, intent migration, planning."""
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.llm import (
    reset_conversation,
    load_conversation,
    get_conversation,
    append_user_message,
    append_assistant_message,
    _migrate_old_format,
    _conversations,
    MAX_HISTORY,
)


class TestConversationHistory:
    def setup_method(self):
        """Clear conversation state before each test."""
        _conversations.clear()

    def test_append_user_message(self):
        append_user_message("dev-1", "你好")
        assert len(_conversations["dev-1"]) == 1
        assert _conversations["dev-1"][0] == {"role": "user", "content": "你好"}

    def test_append_assistant_message(self):
        append_assistant_message("dev-1", "你好！")
        assert len(_conversations["dev-1"]) == 1
        assert _conversations["dev-1"][0] == {"role": "assistant", "content": "你好！"}

    def test_append_empty_assistant_ignored(self):
        append_assistant_message("dev-1", "")
        assert "dev-1" not in _conversations

    def test_history_truncation(self):
        for i in range(25):
            append_user_message("dev-1", f"msg-{i}")
        assert len(_conversations["dev-1"]) == MAX_HISTORY

    def test_reset_conversation(self):
        append_user_message("dev-1", "hello")
        reset_conversation("dev-1")
        assert "dev-1" not in _conversations

    def test_reset_nonexistent(self):
        # Should not raise
        reset_conversation("nonexistent")

    def test_load_conversation(self):
        history = [{"role": "user", "content": f"m{i}"} for i in range(30)]
        load_conversation("dev-1", history)
        assert len(_conversations["dev-1"]) == MAX_HISTORY

    def test_get_conversation_empty(self):
        assert get_conversation("dev-1") == []

    def test_get_conversation_with_data(self):
        append_user_message("dev-1", "test")
        result = get_conversation("dev-1")
        assert len(result) == 1


class TestMigrateOldFormat:
    def test_chat(self):
        result = _migrate_old_format({"action": "chat", "response": "你好"})
        assert result["tool"] == "chat"
        assert result["args"]["response"] == "你好"

    def test_music(self):
        result = _migrate_old_format({"action": "music", "query": "周杰伦"})
        assert result["tool"] == "youtube.play"
        assert result["args"]["query"] == "周杰伦"

    def test_music_stop(self):
        result = _migrate_old_format({"action": "music_stop", "response": "停了"})
        assert result["tool"] == "player.stop"

    def test_music_pause(self):
        result = _migrate_old_format({"action": "music_pause"})
        assert result["tool"] == "player.pause"

    def test_remind(self):
        result = _migrate_old_format({
            "action": "remind",
            "datetime": "2026-02-18T15:00:00",
            "message": "开会",
            "response": "好的",
        })
        assert result["tool"] == "reminder.set"
        assert result["args"]["datetime_iso"] == "2026-02-18T15:00:00"
        assert result["args"]["message"] == "开会"

    def test_unknown_action(self):
        result = _migrate_old_format({"action": "unknown", "response": "嗯"})
        assert result["tool"] == "chat"

    def test_music_default_query(self):
        result = _migrate_old_format({"action": "music"})
        assert result["args"]["query"] == "热门歌曲"


class TestPlanIntent:
    def setup_method(self):
        """Clear conversation state before each test."""
        _conversations.clear()

    @pytest.mark.asyncio
    async def test_plan_intent_chat(self):
        """plan_intent should parse LLM JSON response."""

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"tool": "chat", "args": {"response": "你好！"}, "emotion": "happy"}'

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        mock_session = MagicMock()
        mock_session.device_id = "dev-1"
        mock_session.config.openai_api_key = ""
        mock_session.config.get.return_value = "gpt-4o-mini"

        with patch("app.llm._get_client", return_value=mock_client):
            from app.llm import plan_intent
            result = await plan_intent("你好", "sess-1", session=mock_session)

        assert result["tool"] == "chat"
        assert result["args"]["response"] == "你好！"

    @pytest.mark.asyncio
    async def test_plan_intent_tool(self):
        """plan_intent should handle tool calls."""

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"tool": "youtube.play", "args": {"query": "jazz"}, "reply_hint": "播放中", "emotion": "happy"}'

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        mock_session = MagicMock()
        mock_session.device_id = "dev-1"
        mock_session.config.openai_api_key = ""
        mock_session.config.get.return_value = "gpt-4o-mini"

        with patch("app.llm._get_client", return_value=mock_client):
            from app.llm import plan_intent
            result = await plan_intent("放首jazz", "sess-1", session=mock_session)

        assert result["tool"] == "youtube.play"
        assert result["reply_hint"] == "播放中"

    @pytest.mark.asyncio
    async def test_plan_intent_invalid_json(self):
        """Invalid JSON from LLM should fallback to chat."""

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "not valid json"

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        mock_session = MagicMock()
        mock_session.device_id = "dev-1"
        mock_session.config.openai_api_key = ""
        mock_session.config.get.return_value = "gpt-4o-mini"

        with patch("app.llm._get_client", return_value=mock_client):
            from app.llm import plan_intent
            result = await plan_intent("hello", "sess-1", session=mock_session)

        assert result["tool"] == "chat"
