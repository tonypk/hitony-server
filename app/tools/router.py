"""Rule-based intent router — fast regex matching for common commands.
Falls through to LLM planner for anything it can't match.
"""
import re
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RouteMatch:
    tool: str
    args: Dict[str, Any]
    reply_hint: str = ""


_RULES: List[Tuple[re.Pattern, str, callable, str]] = []


def _build_rules():
    global _RULES

    rules = [
        # ── Music playback (with query) ─────────────────
        (r"^(?:播放|放|来一首|我想听|放首|听一?(?:首|个)?)\s*(.+)",
         "youtube.play",
         lambda m: {"query": m.group(1).strip()},
         "正在为你播放{query}"),

        (r"^(?:play|put on|listen to)\s+(.+)",
         "youtube.play",
         lambda m: {"query": m.group(1).strip()},
         "Playing {query}"),

        # ── Generic music (no specific query) ────────────
        (r"^(?:放首歌|放个歌|放音乐|播放音乐|来首歌|听歌|play(?:\s+some)?\s+music)$",
         "youtube.play",
         lambda m: {"query": "热门歌曲"},
         "正在播放音乐"),

        # ── Player controls ──────────────────────────────
        (r"^(?:暂停|暂停播放|pause)$",
         "player.pause", lambda m: {}, "已暂停"),

        (r"^(?:继续|继续播放|恢复播放|resume|continue)$",
         "player.resume", lambda m: {}, "继续播放"),

        (r"^(?:停止|停止播放|停|别放了|别播了|stop)$",
         "player.stop", lambda m: {}, "已停止"),

        # ── Meeting ──────────────────────────────────────
        (r"^(?:开始(?:会议|录音|记录)|start\s+(?:meeting|recording))(?:\s+(.+))?$",
         "meeting.start",
         lambda m: {"title": (m.group(1) or "").strip()},
         "开始录音"),

        (r"^(?:结束(?:会议|录音|记录)|end\s+(?:meeting|recording)|stop\s+recording)$",
         "meeting.end", lambda m: {}, "会议已结束"),

        (r"^(?:转录|转写|会议(?:记录|内容)|transcribe)$",
         "meeting.transcribe", lambda m: {}, "正在转录"),
    ]

    _RULES.clear()
    for pattern, tool, extractor, hint in rules:
        _RULES.append((re.compile(pattern, re.IGNORECASE), tool, extractor, hint))


def route(text: str) -> Optional[RouteMatch]:
    """Match text against rule patterns. Returns RouteMatch or None."""
    text = text.strip()
    for regex, tool_name, extractor, hint_template in _RULES:
        match = regex.match(text)
        if match:
            args = extractor(match)
            try:
                hint = hint_template.format(**args) if args else hint_template
            except KeyError:
                hint = hint_template
            logger.info(f"Router matched: '{text}' -> {tool_name}({args})")
            return RouteMatch(tool=tool_name, args=args, reply_hint=hint)
    return None


_build_rules()
