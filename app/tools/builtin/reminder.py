"""Reminder tool — set reminders via DB."""
import logging
from datetime import datetime

from ..registry import register_tool, ToolResult, ToolParam

logger = logging.getLogger(__name__)


@register_tool(
    "reminder.set",
    description="Set a reminder for a specific date/time",
    params=[
        ToolParam("datetime_iso", description="ISO datetime e.g. 2026-02-15T09:00:00"),
        ToolParam("message", description="reminder text"),
        ToolParam("response", description="confirmation to speak", required=False),
    ],
    category="reminder",
)
async def reminder_set(datetime_iso: str, message: str, response: str = "", session=None, **kwargs) -> ToolResult:
    try:
        remind_at = datetime.fromisoformat(datetime_iso)
    except (ValueError, TypeError):
        return ToolResult(type="tts", text="抱歉，我没有理解那个时间，请再说一次。")

    if remind_at < datetime.now():
        return ToolResult(type="tts", text="那个时间已经过了，请设置一个未来的提醒。")

    try:
        from ...database import async_session_factory
        from ...models import Reminder

        async with async_session_factory() as db:
            reminder = Reminder(
                user_id=session.config.user_id if session and session.config.user_id else None,
                device_id=session.device_id if session else "unknown",
                remind_at=remind_at,
                message=message,
            )
            db.add(reminder)
            await db.commit()
            logger.info(f"Reminder saved: '{message}' at {remind_at}")
    except Exception as e:
        logger.error(f"Failed to save reminder: {e}")
        return ToolResult(type="error", text="保存提醒失败，请稍后再试。")

    return ToolResult(type="tts", text=response or f"好的，已设置提醒：{message}")
