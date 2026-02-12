"""Notion integration — voice notes and meeting transcripts to Notion."""
import logging
from datetime import datetime

import httpx

from ..registry import register_tool, ToolResult, ToolParam

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


async def _notion_request(method: str, path: str, token: str, json_body: dict = None) -> dict:
    """Make an authenticated Notion API request."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.request(method, f"{NOTION_API}{path}", headers=headers, json=json_body)
        resp.raise_for_status()
        return resp.json()


async def create_page(token: str, database_id: str, title: str, content: str,
                      properties: dict = None) -> dict:
    """Create a page in a Notion database with text content.

    Returns the created page object.
    """
    # Build rich text blocks from content (Notion max 2000 chars per block)
    children = []
    for i in range(0, len(content), 2000):
        chunk = content[i:i + 2000]
        children.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": chunk}}]
            }
        })

    body = {
        "parent": {"database_id": database_id},
        "properties": {
            "title": {"title": [{"text": {"content": title}}]},
            **(properties or {}),
        },
        "children": children,
    }

    result = await _notion_request("POST", "/pages", token, body)
    logger.info(f"Notion: created page '{title}' in database {database_id[:8]}...")
    return result


async def test_connection(token: str, database_id: str) -> dict:
    """Test Notion connection by querying the database. Returns db info or raises."""
    result = await _notion_request("GET", f"/databases/{database_id}", token)
    db_title = ""
    if result.get("title"):
        db_title = result["title"][0].get("plain_text", "") if result["title"] else ""
    return {"ok": True, "database_title": db_title}


def _get_notion_config(session):
    """Extract Notion token + database_id from session. Returns (token, db_id) or (None, None)."""
    if not session or not session.config:
        return None, None
    token = session.config.notion_token
    db_id = session.config.notion_database_id
    if not token or not db_id:
        return None, None
    return token, db_id


@register_tool(
    "note.save",
    description="Save a voice note to Notion",
    params=[ToolParam("content", description="note content to save", required=True)],
    long_running=True,
    category="notion",
)
async def note_save(content: str, session=None, **kwargs) -> ToolResult:
    token, db_id = _get_notion_config(session)
    if not token:
        return ToolResult(type="tts", text="Notion未配置，请在管理面板设置Notion Token和数据库ID")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = f"语音笔记 {now}"

    try:
        await create_page(token, db_id, title, content)
        # Speak a short confirmation, not the full content
        short = content[:50] + ("..." if len(content) > 50 else "")
        return ToolResult(type="tts", text=f"已记录到Notion：{short}")
    except httpx.HTTPStatusError as e:
        logger.error(f"Notion API error: {e.response.status_code} {e.response.text[:200]}")
        if e.response.status_code == 401:
            return ToolResult(type="tts", text="Notion Token无效，请检查设置")
        return ToolResult(type="error", text="保存到Notion失败，请稍后再试")
    except Exception as e:
        logger.error(f"Notion save error: {e}")
        return ToolResult(type="error", text="保存到Notion失败")


async def push_meeting_to_notion(token: str, database_id: str,
                                  title: str, transcript: str,
                                  duration_s: int = 0,
                                  started_at: datetime = None) -> bool:
    """Push a meeting transcript to Notion. Called from meeting.transcribe.

    Returns True on success, False on failure (non-blocking).
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    page_title = f"{title} — {now_str}"

    # Build content with metadata header
    meta_lines = [f"会议标题: {title}"]
    if started_at:
        meta_lines.append(f"开始时间: {started_at.strftime('%Y-%m-%d %H:%M')}")
    if duration_s > 0:
        mins = duration_s // 60
        secs = duration_s % 60
        meta_lines.append(f"时长: {mins}分{secs}秒")
    meta_lines.append("")
    meta_lines.append("---")
    meta_lines.append("")

    full_content = "\n".join(meta_lines) + transcript

    try:
        await create_page(token, database_id, page_title, full_content)
        logger.info(f"Notion: meeting '{title}' pushed ({len(transcript)} chars)")
        return True
    except Exception as e:
        logger.error(f"Notion: failed to push meeting '{title}': {e}")
        return False
