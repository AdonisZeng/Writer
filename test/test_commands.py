"""章节命令 + 落盘草稿命令测试（DB ↔ 文件一致性核心不变式）。"""
import asyncio
import os

from core import db, file_manager, paths
from core.commands import chapters as cc
from core.commands import save_draft


async def test_create_and_numbering(db_project):
    c1 = await cc.create_chapter(db_project, title="开局")
    c2 = await cc.create_chapter(db_project, title="传承")
    c3 = await cc.create_chapter(db_project, title="冲突")
    assert (c1["number"], c2["number"], c3["number"]) == (1, 2, 3)
    for c in (c1, c2, c3):
        assert os.path.exists(
            file_manager.find_chapter_file(db_project, c["number"],
                                           c["title"]))


async def test_insert_between_renumbers_and_renames(db_project):
    c1 = await cc.create_chapter(db_project, title="开局")
    c2 = await cc.create_chapter(db_project, title="传承")
    c3 = await cc.create_chapter(db_project, title="冲突")
    ins = await cc.create_chapter(db_project, title="插章", after_id=c2["id"])

    allc = await db.list_chapters()
    assert [c["number"] for c in allc] == [1, 2, 3, 4]
    assert allc[2]["title"] == "插章"
    # 文件重命名同步：旧「第3章 冲突.md」应已不存在
    assert os.path.exists(
        file_manager.chapter_path(db_project, 3, "插章"))
    assert not os.path.exists(
        os.path.join(paths.chapters_dir(db_project), "第3章 冲突.md"))
    # 插章 order_index 取中值
    assert c1["order_index"] < c2["order_index"] < ins["order_index"] \
        < c3["order_index"]


async def test_rename_chapter_syncs_file(db_project):
    ch = await cc.create_chapter(db_project, title="旧名")
    await cc.rename_chapter(db_project, ch["id"], "新名")
    row = await db.get_chapter(ch["id"])
    assert row["title"] == "新名"
    assert os.path.exists(file_manager.chapter_path(db_project, 1, "新名"))
    assert not os.path.exists(file_manager.chapter_path(db_project, 1, "旧名"))


async def test_update_blueprint_title_goes_through_rename(db_project):
    ch = await cc.create_chapter(db_project, title="甲", role="开篇")
    await cc.update_blueprint(db_project, ch["id"], title="乙",
                              purpose="新目的")
    row = await db.get_chapter(ch["id"])
    assert row["title"] == "乙" and row["purpose"] == "新目的"
    assert os.path.exists(file_manager.chapter_path(db_project, 1, "乙"))


async def test_delete_chapter_renumbers(db_project):
    c1 = await cc.create_chapter(db_project, title="一")
    c2 = await cc.create_chapter(db_project, title="二")
    await cc.create_chapter(db_project, title="三")
    await cc.delete_chapter(db_project, c2["id"])
    allc = await db.list_chapters()
    assert [c["number"] for c in allc] == [1, 2]
    assert not os.path.exists(file_manager.chapter_path(db_project, 2, "二"))


async def test_parse_characters():
    assert cc.parse_characters("林风、赵长老， 黑衣人") == \
        '["林风", "赵长老", "黑衣人"]'
    assert cc.parse_characters("") == "[]"


async def test_save_draft_version_chain(db_project):
    ch = await cc.create_chapter(db_project, title="落盘章")

    r1 = await save_draft.execute(db_project, ch, "第一版")
    r2 = await save_draft.execute(db_project, ch, "第二版")
    assert r1["version"] == 1 and r2["version"] == 2

    drafts = await db.list_drafts(ch["id"])
    assert drafts[0]["version"] == 2
    # v1 归档、v2 指向当前正文
    assert "drafts" in drafts[1]["file_path"].replace("\\", "/")
    assert os.path.basename(drafts[0]["file_path"]) == "第1章 落盘章.md"
    # 归档内容与当前文件内容正确
    assert file_manager.read_text(drafts[1]["file_path"]) == "第一版"
    assert file_manager.read_text(drafts[0]["file_path"]) == "第二版"

    row = await db.get_chapter(ch["id"])
    assert row["status"] == "drafted" and row["word_count"] == 3


async def test_save_draft_no_archive_when_first_version(db_project):
    ch = await cc.create_chapter(db_project, title="首版章")
    await save_draft.execute(db_project, ch, "内容")
    drafts = await db.list_drafts(ch["id"])
    assert len(drafts) == 1
    assert "drafts" not in drafts[0]["file_path"].replace("\\", "/")
