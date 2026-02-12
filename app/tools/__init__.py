"""Tool system — registry, router, executor."""
from .registry import register_tool, get_tool, all_tools, tool_descriptions_for_llm, ToolResult, ToolParam
from .router import route as route_intent
from .executor import execute_tool

# Auto-import builtin tools to trigger @register_tool decorators
from .builtin import *  # noqa
