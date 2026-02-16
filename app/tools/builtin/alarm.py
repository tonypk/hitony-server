"""Alarm tools — daily recurring alarms at specific times."""
import logging
from datetime import datetime, timedelta
import re

from ..registry import register_tool, ToolResult, ToolParam

logger = logging.getLogger(__name__)


@register_tool(
    "alarm.set",
    description="Set a daily alarm at a specific time",
    params=[
        ToolParam("time", description="Time in HH:MM format (24-hour), e.g. '07:30' or '19:00'"),
        ToolParam("label", description="Alarm label/description", required=False),
        ToolParam("response", description="Confirmation to speak", required=False),
    ],
    category="alarm",
)
async def alarm_set(time: str, label: str = "", response: str = "", session=None, **kwargs) -> ToolResult:
    """Set a daily alarm at specified time."""
    # Parse and validate time format
    time_match = re.match(r"^(\d{1,2}):(\d{2})$", time.strip())
    if not time_match:
        return ToolResult(type="tts", text="抱歉，时间格式不对，请使用HH:MM格式，比如07:30")

    hour, minute = int(time_match.group(1)), int(time_match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return ToolResult(type="tts", text="抱歉，时间格式不对，小时应该在0到23之间，分钟在0到59之间")

    # Calculate first occurrence (today or tomorrow)
    now = datetime.now()
    alarm_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if alarm_time <= now:
        alarm_time += timedelta(days=1)

    # Generate alarm message
    alarm_label = label.strip() if label else "闹钟"
    message = f"[闹钟] {alarm_label}"

    try:
        from ...database import async_session_factory
        from ...models import Reminder

        async with async_session_factory() as db:
            reminder = Reminder(
                user_id=session.config.user_id if session and session.config.user_id else None,
                device_id=session.device_id if session else "unknown",
                remind_at=alarm_time,
                message=message,
                is_recurring=1,
                recurrence_rule=f"{hour:02d}:{minute:02d}",
            )
            db.add(reminder)
            await db.commit()
            logger.info(f"Alarm set: {time} - {alarm_label}")
    except Exception as e:
        logger.error(f"Failed to set alarm: {e}")
        return ToolResult(type="error", text="设置闹钟失败，请稍后再试")

    time_str = f"{hour:02d}:{minute:02d}"
    return ToolResult(type="tts", text=response or f"好的，已设置每天{time_str}的闹钟：{alarm_label}")


@register_tool(
    "alarm.list",
    description="List all active alarms",
    params=[],
    category="alarm",
)
async def alarm_list(session=None, **kwargs) -> ToolResult:
    """List all active alarms for current device."""
    if not session:
        return ToolResult(type="error", text="无法查询闹钟")

    try:
        from ...database import async_session_factory
        from ...models import Reminder
        from sqlalchemy import select

        device_id = session.device_id
        async with async_session_factory() as db:
            # Find all recurring reminders that are alarms (message starts with "[闹钟]")
            result = await db.execute(
                select(Reminder)
                .where(
                    Reminder.device_id == device_id,
                    Reminder.is_recurring == 1,
                    Reminder.delivered == 0,
                    Reminder.message.like("[闹钟]%")
                )
                .order_by(Reminder.recurrence_rule)
                .limit(20)
            )
            alarms = result.scalars().all()

        if not alarms:
            return ToolResult(type="tts", text="你目前没有设置闹钟")

        lines = []
        for i, alarm in enumerate(alarms, 1):
            time_str = alarm.recurrence_rule  # Already in HH:MM format
            label = alarm.message.replace("[闹钟] ", "")
            lines.append(f"第{i}个，{time_str}，{label}")

        text = f"你有{len(alarms)}个闹钟。" + "。".join(lines) + "。"
        return ToolResult(type="tts", text=text)

    except Exception as e:
        logger.error(f"Failed to list alarms: {e}")
        return ToolResult(type="error", text="查询闹钟失败")


@register_tool(
    "alarm.cancel",
    description="Cancel an alarm by time or label, or cancel all alarms",
    params=[
        ToolParam("query", description="Time (HH:MM) or label keyword to match, or 'all' to cancel all", required=False),
    ],
    category="alarm",
)
async def alarm_cancel(query: str = "all", session=None, **kwargs) -> ToolResult:
    """Cancel alarms matching the query."""
    if not session:
        return ToolResult(type="error", text="无法取消闹钟")

    try:
        from ...database import async_session_factory
        from ...models import Reminder
        from sqlalchemy import select, delete

        device_id = session.device_id
        async with async_session_factory() as db:
            if query.lower() == "all" or not query:
                # Cancel all alarms
                result = await db.execute(
                    delete(Reminder)
                    .where(
                        Reminder.device_id == device_id,
                        Reminder.is_recurring == 1,
                        Reminder.message.like("[闹钟]%")
                    )
                )
                count = result.rowcount
                await db.commit()
                if count == 0:
                    return ToolResult(type="tts", text="没有需要取消的闹钟")
                return ToolResult(type="tts", text=f"已取消全部{count}个闹钟")
            else:
                # Cancel by time or keyword
                # First try time format (HH:MM)
                time_match = re.match(r"^(\d{1,2}):(\d{2})$", query.strip())
                if time_match:
                    hour, minute = int(time_match.group(1)), int(time_match.group(2))
                    time_str = f"{hour:02d}:{minute:02d}"
                    result = await db.execute(
                        select(Reminder)
                        .where(
                            Reminder.device_id == device_id,
                            Reminder.is_recurring == 1,
                            Reminder.message.like("[闹钟]%"),
                            Reminder.recurrence_rule == time_str
                        )
                    )
                else:
                    # Try keyword match in label
                    result = await db.execute(
                        select(Reminder)
                        .where(
                            Reminder.device_id == device_id,
                            Reminder.is_recurring == 1,
                            Reminder.message.like(f"%{query}%")
                        )
                    )

                matches = result.scalars().all()
                if not matches:
                    return ToolResult(type="tts", text=f"没有找到匹配「{query}」的闹钟")

                for alarm in matches:
                    await db.delete(alarm)
                await db.commit()
                return ToolResult(type="tts", text=f"已取消{len(matches)}个闹钟")

    except Exception as e:
        logger.error(f"Failed to cancel alarm: {e}")
        return ToolResult(type="error", text="取消闹钟失败")
