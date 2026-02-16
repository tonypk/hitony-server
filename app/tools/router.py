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


def _strip_punctuation(text: str) -> str:
    """Strip trailing Chinese/English punctuation from text."""
    return text.rstrip("。！？，、；：…—.!?,;:")


def _build_rules():
    global _RULES

    rules = [
        # ── Music playback (with query) ─────────────────
        # Chinese: \s* (no space needed between command and query)
        (r"^(?:帮我|请|能不能|可以)?\s*(?:播放|放|来一首|我想听|放首|听一?(?:首|个)?)\s*(.+)",
         "youtube.play",
         lambda m: {"query": _strip_punctuation(m.group(1).strip())},
         "正在为你播放{query}"),

        (r"^(?:play|put on|listen to)\s+(.+)",
         "youtube.play",
         lambda m: {"query": _strip_punctuation(m.group(1).strip())},
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

        (r"^(?:下一首|切歌|换一首|next song|next|skip)$",
         "player.next", lambda m: {}, "正在切换"),

        # ── Volume controls ──────────────────────────────
        (r"^(?:音量|声音|volume)\s*(?:设为|设置为|调到|set to)?\s*(\d+)(?:%|百分之)?$",
         "volume.set",
         lambda m: {"level": int(m.group(1))},
         "音量已设为{level}%"),

        (r"^(?:音量|声音)?(?:大一?(?:点|些)|增大|提高|调高|加大|louder|volume up|turn up)$",
         "volume.up", lambda m: {}, "音量已增大"),

        (r"^(?:音量|声音)?(?:小一?(?:点|些)|减小|降低|调低|quieter|volume down|turn down)$",
         "volume.down", lambda m: {}, "音量已减小"),

        (r"^(?:静音|mute)$",
         "volume.set",
         lambda m: {"level": 0},
         "已静音"),

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

        # ── Daily briefing ───────────────────────────────
        (r"^(?:今天有什么安排|今天的安排|每日简报|日程|今日简报|daily briefing|what'?s (?:on )?today)$",
         "briefing.daily", lambda m: {}, "正在查询"),

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
        (r"^(?:搜索|搜一下|查一下|帮我查|百度|谷歌|search)\s*(.+)",
         "web.search",
         lambda m: {"query": _strip_punctuation(m.group(1).strip())},
         "正在搜索{query}"),

        # ── Conversation reset ────────────────────────────
        (r"^(?:清空对话|忘掉(?:之前的)?对话|新对话|重新开始|clear\s+(?:conversation|history|chat)|new\s+chat|reset\s+chat)$",
         "conversation.reset", lambda m: {}, "好的，对话已清空"),

        # ── Voice note → Notion ──────────────────────────
        (r"^(?:记一下|记录一下|笔记|帮我记|备忘|note)\s*[,，:：]?\s*(.+)",
         "note.save",
         lambda m: {"content": _strip_punctuation(m.group(1).strip())},
         "正在记录"),

        # ── Reminder management ─────────────────────────
        (r"^(?:查看提醒|我的提醒|有哪些提醒|提醒列表|list\s+reminders?)$",
         "reminder.list", lambda m: {}, "查询提醒中"),

        (r"^(?:取消提醒|删除提醒|cancel\s+reminders?)\s*(.*)$",
         "reminder.cancel",
         lambda m: {"query": _strip_punctuation(m.group(1).strip()) or "all"},
         "取消提醒"),

        # ── Timer cancel ────────────────────────────────
        (r"^(?:取消倒计时|停止倒计时|取消计时|cancel\s+timer)$",
         "timer.cancel", lambda m: {}, "已取消倒计时"),

        # ── Alarm management ─────────────────────────────
        (r"^(?:设置闹钟|设个闹钟|定个闹钟|set\s+alarm)(?:在)?(?:早上|上午|下午|晚上)?(\d{1,2})(?:点|:)(\d{0,2})(?:分)?",
         "alarm.set",
         lambda m: {
             "time": f"{int(m.group(1)):02d}:{int(m.group(2) or 0):02d}",
             "label": "闹钟"
         },
         "闹钟已设置"),

        (r"^(?:查看闹钟|我的闹钟|有哪些闹钟|闹钟列表|list\s+alarms?)$",
         "alarm.list", lambda m: {}, "查询闹钟中"),

        (r"^(?:取消闹钟|删除闹钟|关闭闹钟|cancel\s+alarms?)\s*(.*)$",
         "alarm.cancel",
         lambda m: {"query": _strip_punctuation(m.group(1).strip()) or "all"},
         "取消闹钟"),
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
