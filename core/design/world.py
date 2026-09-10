"""结构化世界观：分节读写 + settings.md 投影渲染与历史导入。

设计取舍：**DB 为编辑源、`settings.md` 为 DB→文件投影**（与 `outline.md` 同模式）。
生成时 `prompt_builder` 仍读 `settings.md`，注入路径零变更；外部编辑器照常可读
最新内容。首次使用时把既有 `settings.md` 正文迁移进「自由补充」分节，避免丢数据。
"""
from __future__ import annotations

import asyncio

from core import db, file_manager

# settings.md 模板里的占位提示：命中即视为「作者尚未动手」，不迁移
_TEMPLATE_SENTINEL = "（描述世界底层法则"


async def load_sections(project: str) -> list[dict]:
    """返回全部分节；首次使用自动播种默认分节并迁移既有 settings.md。"""
    sections = await db.list_world_sections()
    if sections:
        return sections
    legacy = (await asyncio.to_thread(file_manager.read_settings_md, project)
              or "").strip()
    if not legacy or _TEMPLATE_SENTINEL in legacy:
        legacy = ""
    for key, label, order in db.WORLD_SECTION_DEFAULTS:
        await db.upsert_world_section(
            key, label=label,
            content=legacy if key == "misc" else "", order_index=order)
    return await db.list_world_sections()


def render_worldbuilding(sections: list[dict]) -> str:
    """把分节渲染成 settings.md 正文（空节省略）。"""
    parts = []
    for s in sections:
        content = (s.get("content") or "").strip()
        if not content:
            continue
        label = (s.get("label") or s.get("section_key") or "").strip()
        parts.append(f"## {label}\n\n{content}")
    return "\n\n".join(parts)


async def save_section(project: str, section_key: str, content: str, *,
                       label: str = "") -> list[dict]:
    """保存单节并重投影 settings.md，返回最新分节。"""
    await db.upsert_world_section(section_key, label=label,
                                  content=content or "")
    sections = await db.list_world_sections()
    text = render_worldbuilding(sections)
    await asyncio.to_thread(file_manager.write_settings_md, project,
                            text + ("\n" if text else ""))
    return sections


async def sync_settings_file(project: str) -> None:
    """把当前全部分节整体重投影到 settings.md。"""
    sections = await db.list_world_sections()
    text = render_worldbuilding(sections)
    await asyncio.to_thread(file_manager.write_settings_md, project,
                            text + ("\n" if text else ""))
