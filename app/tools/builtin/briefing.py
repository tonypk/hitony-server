"""Daily briefing tool — summarize weather, reminders, and schedule."""
import logging
from datetime import datetime, timedelta
import httpx

from ..registry import register_tool, ToolResult

logger = logging.getLogger(__name__)


@register_tool(
    "briefing.daily",
    description="Get daily briefing (weather, reminders, alarms)",
    params=[],
    long_running=True,
    category="info",
)
async def briefing_daily(session=None, **kwargs) -> ToolResult:
    """Generate a daily briefing with weather, reminders, and schedule."""
    if not session:
        return ToolResult(type="error", text="无法生成简报")

    # Current date/time
    now = datetime.now()
    date_str = now.strftime("%m月%d日，星期%w").replace("星期0", "星期日").replace("星期1", "星期一").replace("星期2", "星期二").replace("星期3", "星期三").replace("星期4", "星期四").replace("星期5", "星期五").replace("星期6", "星期六")

    briefing_parts = [f"今天是{date_str}。"]

    # 1. Weather
    weather_text = await _get_weather_brief(session)
    if weather_text:
        briefing_parts.append(weather_text)

    # 2. Today's reminders (non-alarm)
    reminder_text = await _get_today_reminders(session)
    if reminder_text:
        briefing_parts.append(reminder_text)

    # 3. Active alarms
    alarm_text = await _get_active_alarms(session)
    if alarm_text:
        briefing_parts.append(alarm_text)

    # Combine
    full_text = "。".join(briefing_parts) + "。"
    return ToolResult(type="tts", text=full_text)


async def _get_weather_brief(session) -> str:
    """Get brief weather summary."""
    from ...config import settings
    import os

    api_key = (session.config.weather_api_key if session.config.weather_api_key else
               os.getenv("OPENWEATHERMAP_API_KEY", ""))
    if not api_key:
        return ""

    city = (session.config.weather_city if session.config.weather_city else
            os.getenv("WEATHER_DEFAULT_CITY", "Singapore"))

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params={
                    "q": city,
                    "appid": api_key,
                    "units": "metric",
                    "lang": "zh_cn",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        desc = data.get("weather", [{}])[0].get("description", "")
        temp = int(data.get("main", {}).get("temp", 0))
        temp_max = int(data.get("main", {}).get("temp_max", 0))
        temp_min = int(data.get("main", {}).get("temp_min", 0))

        return f"今天天气{desc}，气温{temp_min}到{temp_max}度"
    except Exception as e:
        logger.warning(f"Weather brief failed: {e}")
        return ""


async def _get_today_reminders(session) -> str:
    """Get today's non-alarm reminders."""
    try:
        from ...database import async_session_factory
        from ...models import Reminder
        from sqlalchemy import select

        device_id = session.device_id
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)

        async with async_session_factory() as db:
            result = await db.execute(
                select(Reminder)
                .where(
                    Reminder.device_id == device_id,
                    Reminder.delivered == 0,
                    Reminder.remind_at >= today_start,
                    Reminder.remind_at < today_end,
                    ~Reminder.message.like("[闹钟]%")  # Exclude alarms
                )
                .order_by(Reminder.remind_at)
                .limit(5)
            )
            reminders = result.scalars().all()

        if not reminders:
            return ""

        if len(reminders) == 1:
            r = reminders[0]
            time_str = r.remind_at.strftime("%H:%M")
            return f"今天{time_str}有一个提醒：{r.message}"
        else:
            items = []
            for r in reminders:
                time_str = r.remind_at.strftime("%H:%M")
                items.append(f"{time_str}{r.message}")
            return f"今天有{len(reminders)}个提醒，分别是：" + "、".join(items)

    except Exception as e:
        logger.warning(f"Reminder brief failed: {e}")
        return ""


async def _get_active_alarms(session) -> str:
    """Get active alarms."""
    try:
        from ...database import async_session_factory
        from ...models import Reminder
        from sqlalchemy import select

        device_id = session.device_id

        async with async_session_factory() as db:
            result = await db.execute(
                select(Reminder)
                .where(
                    Reminder.device_id == device_id,
                    Reminder.is_recurring == 1,
                    Reminder.delivered == 0,
                    Reminder.message.like("[闹钟]%")
                )
                .order_by(Reminder.recurrence_rule)
                .limit(5)
            )
            alarms = result.scalars().all()

        if not alarms:
            return ""

        if len(alarms) == 1:
            alarm = alarms[0]
            time_str = alarm.recurrence_rule  # HH:MM format
            label = alarm.message.replace("[闹钟] ", "")
            return f"你设置了{time_str}的闹钟"
        else:
            times = [a.recurrence_rule for a in alarms]
            return f"你设置了{len(alarms)}个闹钟，分别是" + "、".join(times)

    except Exception as e:
        logger.warning(f"Alarm brief failed: {e}")
        return ""
