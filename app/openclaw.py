import logging
from openai import AsyncOpenAI
from .config import settings

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url,
)

SYSTEM_PROMPT = (
    "你是EchoEar，一个友好的语音助手。"
    "用户说什么语言，你就必须用什么语言回复。"
    "回答要简短精炼，适合语音播放。\n"
    "You are EchoEar, a friendly voice assistant. "
    "You MUST reply in the SAME language the user speaks. "
    "If the user speaks Chinese, reply in Chinese. If English, reply in English. "
    "Keep answers short and concise for voice output."
)

# Per-session conversation histories keyed by session_id
_sessions: dict[str, list] = {}


def reset_conversation(session_id: str):
    """Reset conversation history for a session"""
    _sessions.pop(session_id, None)
    logger.info(f"Conversation reset for session {session_id}")


async def call_openclaw(text: str, session_id: str = "default") -> str:
    """Chat with OpenAI GPT model"""
    if session_id not in _sessions:
        _sessions[session_id] = []

    history = _sessions[session_id]
    history.append({"role": "user", "content": text})

    # Keep last 10 messages to avoid token overflow
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history[-10:]

    response = await _client.chat.completions.create(
        model=settings.openai_chat_model,
        messages=messages,
        max_tokens=200,
    )

    reply = response.choices[0].message.content.strip()
    history.append({"role": "assistant", "content": reply})

    logger.info(f"LLM [{session_id}]: '{text}' -> '{reply}'")
    return reply
