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


async def create_database(token: str, title: str = "HiTony Meetings") -> dict:
    """Create a new Notion database in the user's workspace.

    Returns the created database object with 'id' field.
    Raises HTTPStatusError if creation fails (e.g., no workspace permission).
    """
    body = {
        "parent": {"type": "workspace", "workspace": True},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": {
            "Name": {"title": {}},  # Title property (required)
            "Date": {"date": {}},   # Meeting date
            "Duration": {"rich_text": {}},  # Meeting duration
            "Status": {  # Meeting status
                "select": {
                    "options": [
                        {"name": "录音中", "color": "red"},
                        {"name": "已结束", "color": "yellow"},
                        {"name": "已转录", "color": "green"},
                    ]
                }
            },
        },
        "is_inline": False,  # Create as full-page database
    }

    result = await _notion_request("POST", "/databases", token, body)
    logger.info(f"Notion: created database '{title}' with id {result.get('id', '')[:8]}...")
    return result


async def test_connection(token: str, database_id: str) -> dict:
    """Test Notion connection by querying the database. Returns db info or raises."""
    result = await _notion_request("GET", f"/databases/{database_id}", token)
    db_title = ""
    if result.get("title"):
        db_title = result["title"][0].get("plain_text", "") if result["title"] else ""
    return {"ok": True, "database_title": db_title}


async def ensure_default_database(token: str, user_id: int = None) -> str:
    """Ensure default HiTony Meetings database exists. Creates if missing.

    Returns database_id (either existing or newly created).
    Raises exception if database creation fails.

    If user_id is provided, updates the user's settings with the new database_id.
    """
    # Try to create the database (will fail if workspace permission is missing)
    try:
        db = await create_database(token, "HiTony Meetings")
        database_id = db.get("id", "")

        if not database_id:
            raise ValueError("Failed to get database ID from Notion response")

        # Update user settings if user_id is provided
        if user_id:
            await _update_user_database_id(user_id, database_id)

        logger.info(f"Notion: ensured default database for user {user_id}, id={database_id[:8]}...")
        return database_id

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            logger.error("Notion: workspace permission required to create database")
            raise ValueError("需要授予Workspace访问权限才能自动创建数据库")
        raise


async def _update_user_database_id(user_id: int, database_id: str):
    """Update user's notion_database_id in the database."""
    from ...database import async_session_factory
    from ...models import UserSettings
    from sqlalchemy import select

    async with async_session_factory() as db:
        result = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
        settings = result.scalar_one_or_none()

        if settings:
            settings.notion_database_id = database_id
            await db.commit()
            logger.info(f"Updated user {user_id} notion_database_id to {database_id[:8]}...")
        else:
            logger.warning(f"No settings found for user {user_id}, cannot update database_id")


def _get_notion_config(session):
    """Extract Notion token + database_id from session. Returns (token, db_id) or (None, None)."""
    if not session or not session.config:
        return None, None
    token = session.config.notion_token
    db_id = session.config.notion_database_id
    if not token or not db_id:
        return None, None
    return token, db_id


async def _get_or_create_database(session) -> tuple:
    """Get Notion config, creating default database if needed.

    Returns (token, database_id) or (None, None) if token is missing.
    """
    if not session or not session.config:
        return None, None

    token = session.config.notion_token
    if not token:
        return None, None

    db_id = session.config.notion_database_id

    # If no database_id, try to create default database
    if not db_id:
        try:
            user_id = session.config.user_id if hasattr(session.config, 'user_id') else None
            db_id = await ensure_default_database(token, user_id)
            # Update session config with new database_id
            session.config.notion_database_id = db_id
        except Exception as e:
            logger.error(f"Failed to create default Notion database: {e}")
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
    # Use auto-create logic
    token, db_id = await _get_or_create_database(session)
    if not token:
        return ToolResult(type="tts", text="Notion未配置，请在管理面板设置Notion Token")
    if not db_id:
        return ToolResult(type="tts", text="无法创建Notion数据库，请确认已授予Workspace权限")

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
                                  summary: str = "",  # 新增参数
                                  duration_s: int = 0,
                                  started_at: datetime = None) -> dict:
    """Push a meeting transcript to Notion. Called from meeting.transcribe.

    Returns dict with success status and URL on success, empty dict on failure (non-blocking).
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    page_title = f"会议记录 — {title}"

    # Build content with metadata header
    meta_lines = [f"**会议ID**: {title}"]
    if started_at:
        meta_lines.append(f"**开始时间**: {started_at.strftime('%Y-%m-%d %H:%M')}")
    if duration_s > 0:
        mins = duration_s // 60
        secs = duration_s % 60
        meta_lines.append(f"**时长**: {mins}分{secs}秒")
    meta_lines.append("")

    # 如果有总结，优先显示总结
    if summary:
        meta_lines.append("## 🤖 AI 总结")
        meta_lines.append("")
        meta_lines.append(summary)
        meta_lines.append("")
        meta_lines.append("---")
        meta_lines.append("")

    meta_lines.append("## 📝 完整转录")
    meta_lines.append("")

    full_content = "\n".join(meta_lines) + transcript

    try:
        result = await create_page(token, database_id, page_title, full_content)
        page_url = result.get("url", "") if result else ""
        logger.info(f"Notion: meeting '{title}' pushed ({len(transcript)} chars, summary={bool(summary)})")
        return {"success": True, "url": page_url}
    except Exception as e:
        logger.error(f"Notion: failed to push meeting '{title}': {e}")
        return {}
