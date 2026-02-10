import logging
from openai import AsyncOpenAI
from .config import settings

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url,
)

_conversation_history: list = []

SYSTEM_PROMPT = "You are EchoEar, a friendly voice assistant. Always reply in the same language the user speaks. Keep your answers short and concise, suitable for voice output."


async def call_openclaw(text: str) -> str:
    """Chat with OpenAI GPT model"""
    _conversation_history.append({"role": "user", "content": text})

    # Keep last 10 messages to avoid token overflow
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + _conversation_history[-10:]

    response = await _client.chat.completions.create(
        model=settings.openai_chat_model,
        messages=messages,
        max_tokens=200,
    )

    reply = response.choices[0].message.content.strip()
    _conversation_history.append({"role": "assistant", "content": reply})

    logger.info(f"LLM: '{text}' -> '{reply}'")
    return reply
