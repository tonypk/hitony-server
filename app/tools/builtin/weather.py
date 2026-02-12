"""Weather tool — query current weather via OpenWeatherMap API."""
import logging
import os

import httpx

from ..registry import register_tool, ToolResult, ToolParam

logger = logging.getLogger(__name__)

# Free tier: 1000 calls/day, no credit card required
# Sign up at https://openweathermap.org/api
_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY", "")
_DEFAULT_CITY = os.getenv("WEATHER_DEFAULT_CITY", "Singapore")


@register_tool(
    "weather.query",
    description="Query current weather and forecast for a city",
    params=[
        ToolParam("query", description="weather query text (e.g. '今天天气' or 'weather in Tokyo')", required=False),
        ToolParam("city", description="city name", required=False),
    ],
    long_running=True,
    category="info",
)
async def weather_query(query: str = "", city: str = "", session=None, **kwargs) -> ToolResult:
    # Per-user key > env var
    api_key = (session.config.weather_api_key if session and session.config.weather_api_key else _API_KEY)
    if not api_key:
        return ToolResult(type="tts", text="抱歉，天气服务还没有配置。请在管理后台设置天气API密钥。")

    # Determine city: explicit arg > per-user default > env default
    default_city = (session.config.weather_city if session and session.config.weather_city else _DEFAULT_CITY)
    target_city = city or default_city

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params={
                    "q": target_city,
                    "appid": api_key,
                    "units": "metric",
                    "lang": "zh_cn",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return ToolResult(type="tts", text=f"找不到城市{target_city}的天气信息。")
        logger.error(f"Weather API error: {e}")
        return ToolResult(type="error", text="天气查询失败，请稍后再试。")
    except Exception as e:
        logger.error(f"Weather API error: {e}")
        return ToolResult(type="error", text="天气查询失败，请稍后再试。")

    desc = data.get("weather", [{}])[0].get("description", "")
    temp = data.get("main", {}).get("temp", "?")
    feels = data.get("main", {}).get("feels_like", "?")
    humidity = data.get("main", {}).get("humidity", "?")
    wind = data.get("wind", {}).get("speed", "?")
    city_name = data.get("name", target_city)

    text = (
        f"{city_name}现在{desc}，"
        f"温度{temp}度，体感{feels}度，"
        f"湿度{humidity}%，风速{wind}米每秒。"
    )
    return ToolResult(type="tts", text=text)
