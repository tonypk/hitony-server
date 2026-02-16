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


@register_tool(
    "reminder.list",
    description="List pending reminders for the current device",
    params=[],
    category="reminder",
)
async def reminder_list(session=None, **kwargs) -> ToolResult:
    if not session:
        return ToolResult(type="error", text="无法查询提醒。")

    try:
        from ...database import async_session_factory
        from ...models import Reminder
        from sqlalchemy import select

        device_id = session.device_id
        async with async_session_factory() as db:
            result = await db.execute(
                select(Reminder)
                .where(Reminder.device_id == device_id, Reminder.delivered == 0,
                       Reminder.remind_at > datetime.now())
                .order_by(Reminder.remind_at)
                .limit(10)
            )
            reminders = result.scalars().all()

        if not reminders:
            return ToolResult(type="tts", text="你目前没有待处理的提醒。")

        lines = []
        for i, r in enumerate(reminders, 1):
            time_str = r.remind_at.strftime("%m月%d日 %H:%M")
            lines.append(f"第{i}个，{time_str}，{r.message}")

        text = f"你有{len(reminders)}个提醒。" + "。".join(lines) + "。"
        return ToolResult(type="tts", text=text)

    except Exception as e:
        logger.error(f"Failed to list reminders: {e}")
        return ToolResult(type="error", text="查询提醒失败，请稍后再试。")


@register_tool(
    "reminder.cancel",
    description="Cancel a reminder by keyword match or cancel all",
    params=[
        ToolParam("query", description="keyword to match reminder message, or 'all' to cancel all", required=False),
    ],
    category="reminder",
)
async def reminder_cancel(query: str = "all", session=None, **kwargs) -> ToolResult:
    if not session:
        return ToolResult(type="error", text="无法取消提醒。")

    try:
        from ...database import async_session_factory
        from ...models import Reminder
        from sqlalchemy import select, delete

        device_id = session.device_id
        async with async_session_factory() as db:
            if query.lower() == "all" or not query:
                # Cancel all pending reminders
                result = await db.execute(
                    delete(Reminder)
                    .where(Reminder.device_id == device_id, Reminder.delivered == 0,
                           Reminder.remind_at > datetime.now())
                )
                count = result.rowcount
                await db.commit()
                if count == 0:
                    return ToolResult(type="tts", text="没有需要取消的提醒。")
                return ToolResult(type="tts", text=f"已取消全部{count}个提醒。")
            else:
                # Cancel by keyword match
                result = await db.execute(
                    select(Reminder)
                    .where(Reminder.device_id == device_id, Reminder.delivered == 0,
                           Reminder.remind_at > datetime.now(),
                           Reminder.message.contains(query))
                )
                matches = result.scalars().all()
                if not matches:
                    return ToolResult(type="tts", text=f"没有找到包含「{query}」的提醒。")
                for r in matches:
                    await db.delete(r)
                await db.commit()
                return ToolResult(type="tts", text=f"已取消{len(matches)}个相关提醒。")

    except Exception as e:
        logger.error(f"Failed to cancel reminder: {e}")
        return ToolResult(type="error", text="取消提醒失败，请稍后再试。")
