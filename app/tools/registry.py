"""Tool registry — decorator-based tool registration and lookup."""
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolParam:
    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    default: Any = None


@dataclass
class ToolResult:
    type: str  # "tts" | "music" | "ask_user" | "silent" | "error"
    text: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolDef:
    name: str
    description: str
    params: List[ToolParam]
    handler: Callable[..., Awaitable[ToolResult]]
    long_running: bool = False
    category: str = ""


_tools: Dict[str, ToolDef] = {}


def register_tool(
    name: str,
    description: str = "",
    params: Optional[List[ToolParam]] = None,
    long_running: bool = False,
    category: str = "",
):
    """Decorator to register a tool function."""
    def decorator(func):
        tool = ToolDef(
            name=name,
            description=description or func.__doc__ or "",
            params=params or [],
            handler=func,
            long_running=long_running,
            category=category,
        )
        _tools[name] = tool
        logger.info(f"Registered tool: {name}")
        return func
    return decorator


def get_tool(name: str) -> Optional[ToolDef]:
    return _tools.get(name)


def all_tools() -> Dict[str, ToolDef]:
    return dict(_tools)


def tool_descriptions_for_llm() -> str:
    """Generate tool list for LLM system prompt."""
    lines = []
    for name, tool in sorted(_tools.items()):
        params = []
        for p in tool.params:
            req = "required" if p.required else "optional"
            params.append(f"{p.name}({req}): {p.description}")
        params_text = ", ".join(params) if params else "none"
        lines.append(f"- {name}: {tool.description} | params: {params_text}")
    return "\n".join(lines)
