"""LLM planner — classifies user intent into tool calls via OpenAI."""
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
from openai import AsyncOpenAI
from .config import settings
from .session import Session

logger = logging.getLogger(__name__)

_conversations: Dict[str, List[dict]] = {}

TOOL_PROMPT = """You are HiTony, a smart voice assistant. Analyze the user's request and respond in JSON.
Today's date/time: {current_datetime}

Available tools:
{tool_list}

Response format — always valid JSON, pick one:
1. Direct answer (questions, chat, info you know):
   {{"tool": "chat", "args": {{"response": "your answer"}}}}

2. Use a tool:
   {{"tool": "<tool_name>", "args": {{...}}, "reply_hint": "brief status phrase"}}

Music rules:
- Play/listen to music → use tool "youtube.play"
- Chinese triggers: 播放/放/来一首/我想听/放首歌/听歌
- Pause → "player.pause", Stop → "player.stop", Resume → "player.resume"

Reminder rules:
- Use tool "reminder.set" with ISO datetime and message
- Parse date/time relative to today. If no time specified, default to 09:00.

Meeting rules:
- Start recording → "meeting.start"
- End recording → "meeting.end"
- Transcribe → "meeting.transcribe"

Examples:
- "播放周杰伦的歌" → {{"tool": "youtube.play", "args": {{"query": "周杰伦 热门歌曲"}}, "reply_hint": "正在播放周杰伦的歌"}}
- "放首歌" → {{"tool": "youtube.play", "args": {{"query": "热门歌曲"}}, "reply_hint": "正在播放音乐"}}
- "暂停" → {{"tool": "player.pause", "args": {{}}, "reply_hint": "已暂停"}}
- "停止播放" → {{"tool": "player.stop", "args": {{}}, "reply_hint": "已停止"}}
- "提醒我明天下午3点开会" → {{"tool": "reminder.set", "args": {{"datetime_iso": "2026-02-13T15:00:00", "message": "开会", "response": "好的，已设置明天下午3点提醒你开会"}}, "reply_hint": "设置提醒"}}
- "开始会议" → {{"tool": "meeting.start", "args": {{}}, "reply_hint": "开始录音"}}
- "结束会议" → {{"tool": "meeting.end", "args": {{}}, "reply_hint": "录音结束"}}
- "今天天气怎么样" → {{"tool": "chat", "args": {{"response": "抱歉，我目前没有实时天气数据。"}}}}
- "你好" → {{"tool": "chat", "args": {{"response": "你好！有什么可以帮你的吗？"}}}}

IMPORTANT: Always respond with valid JSON only. No markdown, no code blocks. Respond in the same language as the user."""


def reset_conversation(session_id: str):
    if session_id in _conversations:
        del _conversations[session_id]
        logger.info(f"[{session_id}] Conversation history cleared")


def _get_client(session: Optional[Session] = None) -> AsyncOpenAI:
    if session and session.config.openai_api_key:
        return AsyncOpenAI(
            api_key=session.config.openai_api_key,
            base_url=session.config.get("openai_base_url", settings.openai_base_url),
        )
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )


async def plan_intent(text: str, session_id: str, session: Optional[Session] = None) -> dict:
    """Classify user intent into a tool call via LLM."""
    client = _get_client(session)

    if session_id not in _conversations:
        _conversations[session_id] = []

    history = _conversations[session_id]
    history.append({"role": "user", "content": text})

    if len(history) > 20:
        history[:] = history[-20:]

    from .tools import tool_descriptions_for_llm
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S (%A)")
    system_prompt = TOOL_PROMPT.replace("{current_datetime}", now_str)
    system_prompt = system_prompt.replace("{tool_list}", tool_descriptions_for_llm())
    messages = [{"role": "system", "content": system_prompt}] + history

    chat_model = (session.config.get("openai_chat_model", settings.intent_model)
                  if session else settings.intent_model)

    response = await client.chat.completions.create(
        model=chat_model,
        messages=messages,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content.strip()
    logger.info(f"[{session_id}] Intent raw: {raw[:200]}")

    try:
        intent = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"[{session_id}] Intent JSON parse failed, treating as chat")
        intent = {"tool": "chat", "args": {"response": raw}}

    # Backward compat: convert old action format to tool format
    if "action" in intent and "tool" not in intent:
        intent = _migrate_old_format(intent)

    return intent


def _migrate_old_format(intent: dict) -> dict:
    """Convert old action-based format to tool-based format."""
    action = intent.get("action", "chat")
    if action == "chat":
        return {"tool": "chat", "args": {"response": intent.get("response", "")}}
    elif action == "music":
        return {"tool": "youtube.play", "args": {"query": intent.get("query", "热门歌曲")},
                "reply_hint": intent.get("reply_hint", "正在播放音乐")}
    elif action == "music_stop":
        return {"tool": "player.stop", "args": {}, "reply_hint": intent.get("response", "已停止")}
    elif action == "music_pause":
        return {"tool": "player.pause", "args": {}, "reply_hint": intent.get("response", "已暂停")}
    elif action == "remind":
        return {"tool": "reminder.set", "args": {
            "datetime_iso": intent.get("datetime", ""),
            "message": intent.get("message", ""),
            "response": intent.get("response", ""),
        }, "reply_hint": "设置提醒"}
    else:
        return {"tool": "chat", "args": {"response": intent.get("response", "")}}
