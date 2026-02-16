"""Meeting status notifications via WebSocket."""
import json
import logging

logger = logging.getLogger(__name__)


async def notify_meeting_status(session, status: str, **extra):
    """
    发送会议状态通知到设备.

    Args:
        session: Session对象
        status: 状态 - "recording" | "ended" | "transcribing" | "completed"
        **extra: 额外数据（如 duration_s, notion_url 等）
    """
    # 导入放在函数内部避免循环依赖
    from .ws_server import get_active_connection
    from .pipeline import ws_send_safe

    # 获取设备的活动连接
    conn = get_active_connection(session.device_id)
    if not conn:
        logger.warning(f"No active connection for device {session.device_id}")
        return

    ws, _ = conn

    message = {
        "type": "meeting_status",
        "status": status,
        **extra
    }

    try:
        await ws_send_safe(ws, json.dumps(message), session, f"meeting_status_{status}")
        logger.info(f"Meeting status notification sent: {status} to {session.device_id}")
    except Exception as e:
        logger.error(f"Failed to send meeting status: {e}")
