"""逆向回滚 Un-finalize（方案 5.5，P1）。

- 仅允许回滚**最新定稿章**（中间章回滚会让后续 Canon delta 变悬挂引用）
- 回滚动作（单事务）：状态 finalized → revised；
  删除该章 canon_timeline / canon_summaries / canon_char_state / canon_facts；
  characters.cs_* 回退至上一章快照；伏笔自 canon_plot_line_snapshots 还原；
  删除 created_at_chapter_id = 本章 的角色主表记录
- 正文内容保留不删（修改基础）；回滚前自动存档当前版本到 .writer/drafts/
- 删除定稿 .txt 投影
"""
import asyncio

from core import db, file_manager
from core.canon import store


async def un_finalize(project: str, chapter_id: str, *,
                      operator_id: str = "") -> dict:
    """回滚指定章（必须是最新定稿章）。返回 {"ok", "message", "stats"}。"""
    latest = await finalize_chapter_latest()
    if latest is None or latest["id"] != chapter_id:
        return {"ok": False,
                "message": "仅允许回滚最新定稿章（中间章回滚会造成悬挂引用）",
                "stats": None}
    chapter = await db.get_chapter(chapter_id)

    # 回滚前自动存档当前正文版本
    path = file_manager.find_chapter_file(project, chapter["number"],
                                          chapter["title"])
    text = await asyncio.to_thread(file_manager.read_text, path)
    if text.strip():
        version = await db.next_draft_version(chapter_id)
        await asyncio.to_thread(file_manager.archive_draft, project,
                                chapter["number"], version, text)
        await db.insert_draft(chapter_id, version, source="write",
                              file_path=file_manager.finalized_txt_path(
                                  project, chapter["number"],
                                  chapter["title"]),
                              word_count=len(text), status="archived")

    stats = await store.revert_chapter_canon(chapter_id)
    await db.update_chapter(chapter_id, status="revised")
    await asyncio.to_thread(file_manager.remove_finalized_txt, project,
                            chapter["number"], chapter["title"])
    from core.commands import chapters
    await chapters.sync_outline(project)
    return {"ok": True, "message": "已回滚至 revised", "stats": stats}


async def finalize_chapter_latest():
    from core.commands.finalize_chapter import get_latest_finalized
    return await get_latest_finalized()
