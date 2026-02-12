"""Web search tool — search + LLM summarize for voice output."""
import logging
import os
from typing import Optional

import httpx

from ..registry import register_tool, ToolResult, ToolParam

logger = logging.getLogger(__name__)

# Tavily API: 1000 free searches/month, great for AI summarization
# Sign up at https://tavily.com
_TAVILY_KEY = os.getenv("TAVILY_API_KEY", "")


@register_tool(
    "web.search",
    description="Search the web and summarize results",
    params=[
        ToolParam("query", description="search query text"),
    ],
    long_running=True,
    category="info",
)
async def web_search(query: str, session=None, **kwargs) -> ToolResult:
    if not _TAVILY_KEY:
        # Fallback: let LLM answer from its knowledge
        return ToolResult(
            type="tts",
            text=f"抱歉，搜索服务还没有配置。我根据已有知识回答：关于「{query}」，请稍后配置TAVILY_API_KEY来启用搜索。",
        )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": _TAVILY_KEY,
                    "query": query,
                    "search_depth": "basic",
                    "include_answer": True,
                    "max_results": 3,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"Tavily search error: {e}")
        return ToolResult(type="error", text="搜索失败，请稍后再试。")

    # Tavily returns a direct AI answer
    answer = data.get("answer", "")
    if answer:
        # Trim for voice output (keep under ~200 chars for natural speech)
        if len(answer) > 300:
            answer = answer[:297] + "..."
        return ToolResult(type="tts", text=answer)

    # Fallback: summarize from results
    results = data.get("results", [])
    if not results:
        return ToolResult(type="tts", text=f"没有找到关于「{query}」的搜索结果。")

    # Build a brief summary from top results
    snippets = []
    for r in results[:3]:
        title = r.get("title", "")
        content = r.get("content", "")
        if content:
            snippets.append(f"{title}：{content[:100]}")

    summary = "。".join(snippets)
    if len(summary) > 300:
        summary = summary[:297] + "..."

    return ToolResult(type="tts", text=f"关于{query}，搜索结果如下：{summary}")
