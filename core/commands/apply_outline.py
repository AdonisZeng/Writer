"""采纳结构大纲：把 AI 产出的分卷/章节细纲批量写入 `chapters` 表。

性能：批量插入后**一次性** renumber + sync_outline，避免逐章调用
`chapters.create_chapter` 触发的 O(n²) 重排与重命名。默认追加在现有章节之后；
`replace=True` 时先清空现有章节（含其 Canon 数据与正文文件）。
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

from core import db, file_manager
from core.commands import chapters as cc


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


async def _clear_existing(project: str) -> int:
    """清空现有章节（DB 行 + 正文文件），返回删除数量。"""
    chapters = await db.list_chapters()
    for ch in chapters:
        await db.delete_chapter_row(ch["id"])
        path = file_manager.chapter_path(project, ch["number"], ch["title"])
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    return len(chapters)


async def apply_outline(project: str, volumes: list[dict], *,
                        replace: bool = False) -> dict:
    """批量写入大纲为章节。

    返回 {"created": 章数, "removed": 清空数, "titles": [...]}。
    """
    removed = await _clear_existing(project) if replace else 0

    existing = await db.list_chapters()
    order = existing[-1]["order_index"] if existing else 0.0
    titles: list[str] = []
    for v in volumes or []:
        for c in v.get("chapters", []) or []:
            order += 1.0
            title = (c.get("title") or "未命名").strip() or "未命名"
            await db.insert_chapter({
                "id": _new_id(), "order_index": order, "number": 0,
                "title": title,
                "role": (c.get("role") or "").strip(),
                "purpose": (c.get("purpose") or "").strip(),
                "key_events": (c.get("key_events") or "").strip(),
                "characters": json.dumps(c.get("characters") or [],
                                         ensure_ascii=False),
                "status": "outlined", "word_count": 0,
            })
            titles.append(title)

    # 一次性重排展示序号并同步重命名既有 .md
    await cc._renumber_and_rename(project)
    # 为新章节落空文件，保持 DB 与 chapters/ 一一对应
    for ch in await db.list_chapters():
        path = file_manager.find_chapter_file(project, ch["number"],
                                              ch["title"])
        if not file_manager.file_mtime(path):
            await asyncio.to_thread(file_manager.write_text, path, "")
    await cc.sync_outline(project)
    return {"created": len(titles), "removed": removed, "titles": titles}
