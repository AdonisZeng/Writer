"""设计域门面：与 AI 协作从「一点想法」逐步打磨成完整作品框架。

对齐 `core/canon/` 的门面风格——UI 只依赖本模块，业务细节下沉到子模块：
- context：多轮消息装配 + 当轮设定快照（build_chat_messages）
- guide：四步引导定义与进度（GUIDE_STEPS / load_progress / mark_done）
- world：结构化世界观分节 + settings.md 投影（load_sections / save_section）
- outline：结构大纲生成（build_outline）

对话历史按项目持久化——每个项目独立 `state.db`，切换项目天然隔离、不串历史。
"""
from core import db
from core.design import (adopt, cast, choices, context, guide, outline, refine,
                         world)
from core.design.choices import (FINISH_TOOL, FINISH_TOOL_NAME, choice_block,
                                 loads_json, remove_choice_blocks,
                                 tool_args_to_choices)
from core.design.guide import (GUIDE_STEPS, PARTICIPATION_LEVELS, STEP_KEYS,
                               next_step, participation_directive,
                               resolve_step_key, step_by_key)

__all__ = [
    "GUIDE_STEPS", "STEP_KEYS", "step_by_key", "next_step", "resolve_step_key",
    "PARTICIPATION_LEVELS", "participation_directive",
    "load_progress", "save_progress", "mark_done", "apply_step_adoption",
    "load_history", "append_message", "clear_history",
    "build_chat_messages", "build_snapshot",
    "load_sections", "save_section", "render_worldbuilding", "sync_settings",
    "build_outline", "parse_outline", "count_chapters", "OUTLINE_SCHEMA",
    "parse_character_snippet",
    "parse_choices", "has_choice_marker", "protocol_issues", "retry_feedback",
    "extract_for_target", "strip_choice_blocks",
    "FINISH_TOOL", "FINISH_TOOL_NAME", "tool_args_to_choices", "choice_block",
    "loads_json", "remove_choice_blocks",
]

parse_character_snippet = cast.parse_character_snippet
parse_choices = choices.parse_choices
has_choice_marker = choices.has_choice_marker
protocol_issues = choices.protocol_issues
retry_feedback = choices.retry_feedback
extract_for_target = refine.extract_for_target


def strip_choice_blocks(text: str) -> str:
    """仅取干净正文（丢弃 choice 块），供历史渲染/摘要使用。"""
    return choices.parse_choices(text)[0]

# ---- 引导进度（转发 guide） ----
load_progress = guide.load_progress
save_progress = guide.save_progress
mark_done = guide.mark_done


# ---- 步收尾采纳（转发 adopt） ----
apply_step_adoption = adopt.apply_step_adoption


# ---- 多轮对话（持久化） ----

async def load_history(limit: int = 0) -> list[dict]:
    """本项目的设计对话历史（按时间正序；limit>0 取最近 limit 条）。"""
    return await db.list_design_messages(limit)


async def append_message(role: str, content: str, *,
                         mode: str = "free", step: str = "") -> None:
    await db.insert_design_message(role, content, mode=mode, step=step)


async def clear_history() -> None:
    await db.clear_design_messages()


async def build_chat_messages(project: str, author_text: str, *,
                              mode: str = "free", step: str = "",
                              finish_tool: bool = False) -> list[dict]:
    return await context.build_chat_messages(project, author_text, mode=mode,
                                             step=step,
                                             finish_tool=finish_tool)


async def build_snapshot(project: str) -> str:
    return await context.build_snapshot(project)


# ---- 结构化世界观 ----
load_sections = world.load_sections
save_section = world.save_section
render_worldbuilding = world.render_worldbuilding
sync_settings = world.sync_settings_file


# ---- 结构大纲 ----
build_outline = outline.build_outline
parse_outline = outline.parse_outline
count_chapters = outline.count_chapters
OUTLINE_SCHEMA = outline.OUTLINE_SCHEMA
