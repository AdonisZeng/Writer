"""落盘草稿命令：AI 产出经作者确认后写入正文与版本库（方案 7-④ / UI 负面清单 4）。

版本指针模型（方案 5.6）：
- chapters/ 永远只保留当前激活版；drafts 表 max(version) 行的 file_path 指向 chapters/
- 新版落盘时，旧当前版内容归档 .writer/drafts/ch_NN_vN.md 并回填其 file_path
- chapters.status = 'drafted'
"""
import asyncio

from core import db, file_manager


async def execute(project: str, chapter: dict, text: str) -> dict:
    """确认写入。返回 {"version": int, "file_path": str}。"""
    word_count = len(text)
    file_path = file_manager.find_chapter_file(
        project, chapter["number"], chapter["title"])

    old_text = file_manager.read_text(file_path)
    new_version = await db.next_draft_version(chapter["id"])
    old_version = new_version - 1

    await asyncio.to_thread(file_manager.write_text, file_path, text)

    # 旧当前版归档并回填指针（若有）
    if old_text.strip() and old_version >= 1:
        archive_path = await asyncio.to_thread(
            file_manager.archive_draft, project,
            chapter["number"], old_version, old_text)
        await db.update_draft_file_path(chapter["id"], old_version,
                                        archive_path)

    await db.insert_draft(chapter["id"], new_version, source="write",
                          file_path=file_path, word_count=word_count)
    await db.update_chapter(chapter["id"], status="drafted",
                            word_count=word_count)
    return {"version": new_version, "file_path": file_path}
