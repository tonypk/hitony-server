"""Tool executor — dispatches tool calls with silence keepalive for long-running tools."""
import asyncio
import logging
import struct
import time
from typing import Optional

import opuslib

from .registry import get_tool, ToolResult

logger = logging.getLogger(__name__)

# Pre-encode 60ms silence Opus packet for keepalive during long-running tools
_encoder = opuslib.Encoder(16000, 1, opuslib.APPLICATION_VOIP)
SILENCE_OPUS = _encoder.encode(b'\x00' * (960 * 2), 960)
SILENCE_BLOB = struct.pack('>H', len(SILENCE_OPUS)) + SILENCE_OPUS


async def execute_tool(tool_name, args, session, ws=None, ws_send_fn=None):
    """Execute a registered tool by name.

    For long_running tools, sends 2s silence keepalive to prevent ESP32 timeout.
    """
    tool = get_tool(tool_name)
    if not tool:
        logger.warning(f"Unknown tool: {tool_name}")
        return ToolResult(type="error", text=f"Unknown tool: {tool_name}")

    # Validate required params
    for param in tool.params:
        if param.required and param.name not in args:
            return ToolResult(
                type="ask_user",
                text=f"请问{param.description or param.name}是什么？",
                data={"missing_param": param.name, "tool": tool_name, "partial_args": args},
            )

    # Inject session
    args["session"] = session

    arg_str = ", ".join(f"{k}={v!r}" for k, v in args.items() if k != "session")
    logger.info(f"Executing tool: {tool_name}({arg_str})")
    t0 = time.monotonic()

    if tool.long_running and ws and ws_send_fn:
        result = await _execute_with_keepalive(tool, args, session, ws, ws_send_fn)
    else:
        try:
            result = await tool.handler(**args)
        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}", exc_info=True)
            result = ToolResult(type="error", text=f"执行失败: {e}")

    elapsed = time.monotonic() - t0
    logger.info(f"Tool {tool_name}: {elapsed:.1f}s -> {result.type}")
    return result


async def _execute_with_keepalive(tool, args, session, ws, ws_send_fn):
    """Run long-running tool with 2s silence keepalive."""
    task = asyncio.create_task(tool.handler(**args))

    while not task.done():
        if session.tts_abort or ws.closed:
            task.cancel()
            return ToolResult(type="silent", text="Cancelled")
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
        except asyncio.TimeoutError:
            await ws_send_fn(ws, SILENCE_BLOB, session, "silence_keepalive")

    try:
        return task.result()
    except Exception as e:
        logger.error(f"Long-running tool {tool.name} failed: {e}", exc_info=True)
        return ToolResult(type="error", text=f"执行失败: {e}")
