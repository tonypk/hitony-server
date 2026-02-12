"""Conversation management — reset/clear conversation history."""
import logging

from ..registry import register_tool, ToolResult

logger = logging.getLogger(__name__)


@register_tool(
    "conversation.reset",
    description="Clear conversation history and start fresh",
    params=[],
)
async def conversation_reset(session=None, **kwargs) -> ToolResult:
    if session:
        from app.llm import reset_conversation
        reset_conversation(session.device_id)

        # Also clear in DB
        try:
            from sqlalchemy import select
            from app.database import async_session_factory
            from app.models import Device
            async with async_session_factory() as db:
                result = await db.execute(
                    select(Device).where(Device.device_id == session.device_id)
                )
                device = result.scalar_one_or_none()
                if device:
                    device.conversation_json = "[]"
                    await db.commit()
        except Exception as e:
            logger.error(f"Failed to clear conversation in DB: {e}")

    return ToolResult(type="tts", text="好的，对话已清空，让我们重新开始吧")
