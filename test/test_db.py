"""db.py 测试：单写者队列串行化、DAO、并发写正确性。"""
import asyncio

import pytest

from core import db


async def test_ensure_project_row_idempotent(db_project):
    await db.ensure_project_row(title="另一个标题")  # 已存在 → 不覆盖
    proj = await db.get_project()
    assert proj["title"] == "测试项目"


async def test_update_project_roundtrip(db_project):
    await db.update_project(writing_style="冷峻", genre="仙侠",
                            words_per_chapter=2500)
    proj = await db.get_project()
    assert proj["writing_style"] == "冷峻"
    assert proj["genre"] == "仙侠"
    assert proj["words_per_chapter"] == 2500
    assert proj["updated_at"]  # 自动更新时间戳


async def test_chapters_crud_and_renumber(db_project):
    for i in range(3):
        await db.insert_chapter({"id": f"ch{i}", "order_index": float(i + 1),
                                 "title": f"章{i}"})
    chapters = await db.list_chapters()
    assert [c["id"] for c in chapters] == ["ch0", "ch1", "ch2"]

    await db.renumber_chapters(["ch2", "ch0", "ch1"])  # 乱序重排
    chapters = await db.list_chapters()  # 按 order_index 排列，id 序不变
    assert [c["id"] for c in chapters] == ["ch0", "ch1", "ch2"]
    assert [c["number"] for c in chapters] == [2, 3, 1]

    await db.update_chapter("ch0", title="改名", status="drafted")
    ch = await db.get_chapter("ch0")
    assert ch["title"] == "改名" and ch["status"] == "drafted"

    await db.delete_chapter_row("ch0")
    assert await db.get_chapter("ch0") is None


async def test_delete_chapter_cascades(db_project):
    await db.insert_chapter({"id": "chX", "order_index": 1.0, "title": "X"})
    await db.insert_draft("chX", 1, "write", "path.md", 100)
    await db.insert_llm_call(provider="local", model="m", purpose="draft")
    await db.delete_chapter_row("chX")
    assert await db.list_drafts("chX") == []


async def test_draft_version_chain(db_project):
    await db.insert_chapter({"id": "chA", "order_index": 1.0, "title": "A"})
    assert await db.next_draft_version("chA") == 1
    await db.insert_draft("chA", 1, "write", "p1.md", 10)
    assert await db.next_draft_version("chA") == 2
    await db.insert_draft("chA", 2, "write", "p2.md", 20)
    drafts = await db.list_drafts("chA")
    assert [d["version"] for d in drafts] == [2, 1]  # 降序

    await db.update_draft_file_path("chA", 1, "archived.md")
    drafts = await db.list_drafts("chA")
    assert drafts[1]["file_path"] == "archived.md"


async def test_character_upsert_and_delete(db_project):
    """角色卡 upsert / 查询 / 删除（设计界面人物管理依赖）。"""
    await db.upsert_character("林潇", role="protagonist",
                              personality="坚韧", abilities="剑术")
    got = await db.get_character("林潇")
    assert got["role"] == "protagonist" and got["personality"] == "坚韧"
    assert any(c["name"] == "林潇" for c in await db.list_characters())
    # upsert 幂等更新
    await db.upsert_character("林潇", personality="冷静")
    assert (await db.get_character("林潇"))["personality"] == "冷静"
    # 删除
    await db.delete_character("林潇")
    assert await db.get_character("林潇") is None


async def test_concurrent_writes_serialized(db_project):
    """单写者队列正确性：50 个并发写全部落地且无异常。"""
    await db.insert_chapter({"id": "chC", "order_index": 1.0, "title": "C"})

    async def bump(i):
        await db.update_chapter("chC", word_count=i)

    await asyncio.gather(*(bump(i) for i in range(50)))
    ch = await db.get_chapter("chC")
    assert ch["word_count"] == 49


async def test_llm_calls_and_usage(db_project):
    for i in range(3):
        await db.insert_llm_call(provider="local", model="qwen",
                                 purpose="draft", prompt_tokens=100,
                                 completion_tokens=200 + i)
    usage = await db.session_usage()
    assert usage["prompt_tokens"] == 300
    assert usage["completion_tokens"] == 603  # 200+200+200, 200+201+202


async def test_rejects_write_before_open():
    # 未 open 时直接调用应断言失败而非静默错写
    if db._queue is not None:  # 上个夹具残留（理论上不会）
        await db.close()
    with pytest.raises(AssertionError):
        await db.write(lambda conn: conn.execute("SELECT 1"))
