"""章节管理命令：新建/插入/删除/重命名（补充命令，方案四数据模型 + 5.6 文件纪律）。

核心不变式：id（nanoid）恒定，order_index 支撑任意插章（取中值），
number 仅展示用——插删后重排受影响区段并同步重命名 .md。
"""
import asyncio
import json
import uuid

from core import db, file_manager, paths


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


async def create_chapter(project: str, *, title: str, after_id: str = "",
                         role: str = "", purpose: str = "",
                         key_events: str = "", characters: str = "[]") -> dict:
    """新建章节；after_id 非空时插入其后（order_index 取中值）。返回新章节。"""
    chapters = await db.list_chapters()
    if after_id:
        idx = next((i for i, c in enumerate(chapters) if c["id"] == after_id),
                   len(chapters) - 1)
        prev = chapters[idx] if chapters else None
        nxt = chapters[idx + 1] if idx + 1 < len(chapters) else None
        if prev and nxt:
            order = (prev["order_index"] + nxt["order_index"]) / 2
        else:
            order = prev["order_index"] + 1.0
    else:
        order = (chapters[-1]["order_index"] + 1.0) if chapters else 1.0

    chapter = {
        "id": _new_id(), "order_index": order, "number": 0,
        "title": title, "role": role, "purpose": purpose,
        "key_events": key_events, "characters": characters,
        "status": "outlined", "word_count": 0,
    }
    await db.insert_chapter(chapter)
    await _renumber_and_rename(project)
    new_ch = await db.get_chapter(chapter["id"])
    # 空文件落盘，保持 DB 与 chapters/ 一一对应
    path = file_manager.find_chapter_file(project, new_ch["number"],
                                          new_ch["title"])
    if not file_manager.file_mtime(path):
        await asyncio.to_thread(file_manager.write_text, path, "")
    await sync_outline(project)
    return new_ch


async def delete_chapter(project: str, chapter_id: str) -> None:
    chapter = await db.get_chapter(chapter_id)
    await db.delete_chapter_row(chapter_id)
    await _renumber_and_rename(project)
    # 删除对应 .md（按旧 number+title 定位）
    if chapter:
        path = file_manager.chapter_path(project, chapter["number"],
                                         chapter["title"])
        import os
        if os.path.exists(path):
            await asyncio.to_thread(os.remove, path)
    await sync_outline(project)


async def rename_chapter(project: str, chapter_id: str, new_title: str) -> None:
    chapter = await db.get_chapter(chapter_id)
    if not chapter:
        return
    await db.update_chapter(chapter_id, title=new_title)
    file_manager.rename_chapter_file(project, chapter["number"],
                                     chapter["title"],
                                     chapter["number"], new_title)
    await sync_outline(project)


async def update_blueprint(project: str, chapter_id: str, **fields) -> None:
    """细纲面板保存：title 之外的字段直接更新；title 走 rename_chapter。"""
    title = fields.pop("title", None)
    if fields:
        await db.update_chapter(chapter_id, **fields)
    if title is not None:
        await rename_chapter(project, chapter_id, title)
    else:
        await sync_outline(project)


async def move_chapter(project: str, drag_id: str, target_id: str) -> dict:
    """拖拽重排（P2）：把 drag 章移动到 target 章之前（order_index 取中值）。"""
    if drag_id == target_id:
        return {"ok": False, "message": "不能移动到自身"}
    chapters = await db.list_chapters()
    ids = [c["id"] for c in chapters]
    if drag_id not in ids or target_id not in ids:
        return {"ok": False, "message": "章节不存在"}
    idx_d, idx_t = ids.index(drag_id), ids.index(target_id)
    if idx_t == idx_d or idx_t == idx_d + 1:
        return {"ok": False, "message": "位置未变化"}
    target = chapters[idx_t]
    prev = chapters[idx_t - 1] if idx_t > 0 else None
    if prev and prev["id"] == drag_id:
        prev = chapters[idx_t - 2] if idx_t >= 2 else None
    if prev and prev["id"] != drag_id:
        new_order = (prev["order_index"] + target["order_index"]) / 2
    else:
        new_order = target["order_index"] - 1.0
    await db.update_chapter(drag_id, order_index=new_order)
    await _renumber_and_rename(project)
    await sync_outline(project)
    return {"ok": True, "message": f"已移动到第{target['number']}章之前"}


async def _renumber_and_rename(project: str) -> None:
    """重排展示序号并同步重命名 .md（number 是文件名派生物）。"""
    chapters = await db.list_chapters()
    old = {c["id"]: (c["number"], c["title"]) for c in chapters}
    await db.renumber_chapters([c["id"] for c in chapters])
    for i, c in enumerate(chapters, start=1):
        old_number, old_title = old[c["id"]]
        if old_number != i or old_title != c["title"]:
            file_manager.rename_chapter_file(project, old_number, old_title,
                                             i, c["title"])


async def sync_outline(project: str) -> None:
    chapters = await db.list_chapters()
    await asyncio.to_thread(file_manager.sync_outline_md, project, chapters)


async def set_status(chapter_id: str, status: str) -> None:
    await db.update_chapter(chapter_id, status=status)


def parse_characters(text: str) -> str:
    """UI 输入（顿号/逗号分隔）→ JSON 数组字符串。"""
    items = [s.strip() for s in
             (text or "").replace("，", ",").replace("、", ",").split(",")
             if s.strip()]
    return json.dumps(items, ensure_ascii=False)
