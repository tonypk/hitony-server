"""LLM planner — classifies user intent into tool calls via OpenAI."""
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
from openai import AsyncOpenAI
from .config import settings
from .session import Session

logger = logging.getLogger(__name__)

_conversations: Dict[str, List[dict]] = {}  # keyed by device_id

MAX_HISTORY = 20

TOOL_PROMPT = """You are HiTony, a smart voice assistant. Analyze the user's request and respond in JSON.
Today's date/time: {current_datetime}

Available tools:
{tool_list}

Response format — always valid JSON, pick one:
1. Direct answer (questions, chat, info you know):
   {{"tool": "chat", "args": {{"response": "your answer"}}, "emotion": "happy"}}

2. Use a tool:
   {{"tool": "<tool_name>", "args": {{...}}, "reply_hint": "brief status phrase", "emotion": "happy"}}

Emotion field (REQUIRED): Controls the device's eye expression. Choose one:
  neutral, happy, sad, angry, surprised, thinking, confused, love, shy, wink
  Pick the emotion that best matches the tone of your response or the user's situation.

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

Weather rules:
- Weather queries → use "weather.query"
- Triggers: 天气/weather

Timer rules:
- Countdown timer → use "timer.set" with seconds (as string) and optional label
- Convert minutes to seconds (e.g. 5分钟 → seconds="300")
- Triggers: 倒计时/计时/X分钟后提醒我/X分钟后叫我

Search rules:
- Web search → use "web.search" with query
- Use for questions needing real-time or factual data you don't know
- Triggers: 搜索/搜一下/查一下/search

Conversation rules:
- Clear/reset conversation → use "conversation.reset"
- Triggers: 清空对话/忘掉对话/新对话/重新开始/clear chat

Examples:
- "播放周杰伦的歌" → {{"tool": "youtube.play", "args": {{"query": "周杰伦 热门歌曲"}}, "reply_hint": "正在播放周杰伦的歌", "emotion": "happy"}}
- "放首歌" → {{"tool": "youtube.play", "args": {{"query": "热门歌曲"}}, "reply_hint": "正在播放音乐", "emotion": "happy"}}
- "暂停" → {{"tool": "player.pause", "args": {{}}, "reply_hint": "已暂停", "emotion": "neutral"}}
- "停止播放" → {{"tool": "player.stop", "args": {{}}, "reply_hint": "已停止", "emotion": "neutral"}}
- "提醒我明天下午3点开会" → {{"tool": "reminder.set", "args": {{"datetime_iso": "2026-02-13T15:00:00", "message": "开会", "response": "好的，已设置明天下午3点提醒你开会"}}, "reply_hint": "设置提醒", "emotion": "happy"}}
- "开始会议" → {{"tool": "meeting.start", "args": {{}}, "reply_hint": "开始录音", "emotion": "neutral"}}
- "结束会议" → {{"tool": "meeting.end", "args": {{}}, "reply_hint": "录音结束", "emotion": "happy"}}
- "今天天气怎么样" → {{"tool": "weather.query", "args": {{"query": "今天天气"}}, "reply_hint": "正在查询天气", "emotion": "thinking"}}
- "倒计时5分钟" → {{"tool": "timer.set", "args": {{"seconds": "300", "label": "5分钟倒计时"}}, "reply_hint": "5分钟倒计时已开始", "emotion": "happy"}}
- "搜一下最新的iPhone价格" → {{"tool": "web.search", "args": {{"query": "最新iPhone价格"}}, "reply_hint": "正在搜索", "emotion": "thinking"}}
- "你好" → {{"tool": "chat", "args": {{"response": "你好！有什么可以帮你的吗？"}}, "emotion": "happy"}}
- "我今天好累" → {{"tool": "chat", "args": {{"response": "辛苦了，要不要听首轻松的歌放松一下？"}}, "emotion": "sad"}}
- "你真棒" → {{"tool": "chat", "args": {{"response": "谢谢夸奖！"}}, "emotion": "love"}}

IMPORTANT: Always respond with valid JSON only. No markdown, no code blocks. Respond in the same language as the user."""


def reset_conversation(device_id: str):
    """Clear conversation history for a device."""
    if device_id in _conversations:
        del _conversations[device_id]
        logger.info(f"[{device_id}] Conversation history cleared")


def load_conversation(device_id: str, history: List[dict]):
    """Load conversation history from DB (called on WS connect)."""
    _conversations[device_id] = history[-MAX_HISTORY:]
    logger.info(f"[{device_id}] Loaded {len(_conversations[device_id])} history messages")


def get_conversation(device_id: str) -> List[dict]:
    """Get current conversation history (for saving to DB on disconnect)."""
    return _conversations.get(device_id, [])


def append_user_message(device_id: str, text: str):
    """Append a user message to conversation history (for router-matched paths)."""
    if device_id not in _conversations:
        _conversations[device_id] = []
    _conversations[device_id].append({"role": "user", "content": text})
    if len(_conversations[device_id]) > MAX_HISTORY:
        _conversations[device_id][:] = _conversations[device_id][-MAX_HISTORY:]


def append_assistant_message(device_id: str, text: str):
    """Append an assistant response to conversation history."""
    if not text:
        return
    if device_id not in _conversations:
        _conversations[device_id] = []
    _conversations[device_id].append({"role": "assistant", "content": text})
    if len(_conversations[device_id]) > MAX_HISTORY:
        _conversations[device_id][:] = _conversations[device_id][-MAX_HISTORY:]


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
    device_id = session.device_id if session else session_id

    if device_id not in _conversations:
        _conversations[device_id] = []

    history = _conversations[device_id]
    history.append({"role": "user", "content": text})

    if len(history) > MAX_HISTORY:
        history[:] = history[-MAX_HISTORY:]

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
    logger.info(f"[{device_id}] Intent raw: {raw[:200]}")

    try:
        intent = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"[{device_id}] Intent JSON parse failed, treating as chat")
        intent = {"tool": "chat", "args": {"response": raw}}

    # Backward compat: convert old action format to tool format
    if "action" in intent and "tool" not in intent:
        intent = _migrate_old_format(intent)

    # Capture assistant response in conversation history
    if intent.get("tool") == "chat":
        resp = intent.get("args", {}).get("response", "")
        if resp:
            history.append({"role": "assistant", "content": resp})
    else:
        hint = intent.get("reply_hint", "")
        if hint:
            history.append({"role": "assistant", "content": hint})

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
