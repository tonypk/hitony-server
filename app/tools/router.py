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
        # "帮我播放..." / "请播放..." prefix support
        (r"^(?:帮我|请|能不能|可以)?\s*(?:播放|放|来一首|我想听|放首|听一?(?:首|个)?)\s+(.+)",
         "youtube.play",
         lambda m: {"query": m.group(1).strip()},
         "正在为你播放{query}"),

        (r"^(?:play|put on|listen to)\s+(.+)",
         "youtube.play",
         lambda m: {"query": m.group(1).strip()},
         "Playing {query}"),

        # ── Generic music (no specific query) ────────────
        (r"^(?:帮我|请)?\s*(?:放首歌|放个歌|放音乐|播放音乐|来首歌|听歌|播放|play(?:\s+some)?\s+music)$",
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

        # ── Timer ─────────────────────────────────────────
        (r"^(?:倒计时|计时)\s*(\d+)\s*(?:分钟|分)$",
         "timer.set",
         lambda m: {"seconds": str(int(m.group(1)) * 60), "label": f"{m.group(1)}分钟倒计时"},
         "{label}已开始"),

        (r"^(?:倒计时|计时)\s*(\d+)\s*秒$",
         "timer.set",
         lambda m: {"seconds": m.group(1), "label": f"{m.group(1)}秒倒计时"},
         "{label}已开始"),

        (r"^(\d+)\s*(?:分钟|分)(?:后|之后)?(?:提醒我|叫我|告诉我)(.*)$",
         "timer.set",
         lambda m: {"seconds": str(int(m.group(1)) * 60),
                     "label": m.group(2).strip() or f"{m.group(1)}分钟倒计时"},
         "好的，{label}"),

        # ── Weather ───────────────────────────────────────
        (r"^(?:今天|明天|后天|.{2,4}的)?天气(?:怎么样|如何|预报)?$",
         "weather.query",
         lambda m: {"query": m.group(0)},
         "正在查询天气"),

        (r"^(?:what'?s the |how'?s the )?weather",
         "weather.query",
         lambda m: {"query": m.group(0)},
         "Checking weather"),

        # ── Meeting ──────────────────────────────────────
        (r"^(?:开始(?:会议|录音|记录)|start\s+(?:meeting|recording))(?:\s+(.+))?$",
         "meeting.start",
         lambda m: {"title": (m.group(1) or "").strip()},
         "开始录音"),

        (r"^(?:结束(?:会议|录音|记录)|end\s+(?:meeting|recording)|stop\s+recording)$",
         "meeting.end", lambda m: {}, "会议已结束"),

        (r"^(?:转录|转写|会议(?:记录|内容)|transcribe)$",
         "meeting.transcribe", lambda m: {}, "正在转录"),

        # ── Web search ────────────────────────────────────
        (r"^(?:搜索|搜一下|查一下|帮我查|百度|谷歌|search)\s+(.+)",
         "web.search",
         lambda m: {"query": m.group(1).strip()},
         "正在搜索{query}"),
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
            # Filter out empty query values
            if "query" in args and not args["query"]:
                continue
            try:
                hint = hint_template.format(**args) if args else hint_template
            except KeyError:
                hint = hint_template
            logger.info(f"Router matched: '{text}' -> {tool_name}({args})")
            return RouteMatch(tool=tool_name, args=args, reply_hint=hint)
    return None


_build_rules()
