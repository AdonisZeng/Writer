"""canon store / rollback 离线测试：写回、快照、回滚事务、自检。"""
import os

import pytest

from core import db, file_manager, paths
from core.canon import store
from core.commands import chapters as cc


A_RESULT = {
    "summary": "主角在遗迹获得传承信物，初遇宿敌。",
    "timeline": [
        {"seq": 1, "location": "青石镇", "summary": "主角进入遗迹",
         "impact": "开启主线"},
        {"seq": 2, "location": "遗迹深处", "summary": "获得传承信物",
         "impact": "金手指落地"},
    ],
    "plot_lines": [
        {"name": "宿敌之约", "status": "active", "description": "宿敌约定再战"},
        {"name": "旧日恩怨", "status": "resolved", "description": "本章正式回收"},
    ],
}

B_RESULT = {
    "characters": [
        {"name": "林风", "is_new_character": False, "location": "遗迹深处",
         "level": "炼气三层", "physical": "轻伤", "mental": "振奋",
         "state": "存活", "items": "传承信物", "knowledge_add": ["遗迹入口"]},
        {"name": "黑衣老者", "is_new_character": True, "location": "暗处",
         "level": "金丹", "physical": "", "mental": "", "state": "存活",
         "items": "", "knowledge_add": []},
        {"name": "凭空路人甲", "is_new_character": False, "location": "",
         "level": "", "physical": "", "mental": "", "state": "",
         "items": "", "knowledge_add": []},
    ],
    "facts": [
        {"category": "世界观", "statement": "传承信物每十年现世一次"},
    ],
}


async def test_writeback_pipeline_a(db_project):
    ch = await cc.create_chapter(db_project, title="第一章")
    stats = await store.writeback_pipeline_a(ch["id"], A_RESULT)
    assert stats["events"] == 2 and stats["plot_lines"] == 2
    assert await db.get_summary(ch["id"]) == A_RESULT["summary"]
    timeline = await db.list_chapter_timeline(ch["id"])
    assert [t["seq"] for t in timeline] == [1, 2]
    plot_lines = {p["name"]: p for p in await db.list_plot_lines()}
    assert plot_lines["旧日恩怨"]["status"] == "resolved"
    assert plot_lines["宿敌之约"]["last_advanced"] == ch["id"]


async def test_plot_line_snapshot_before_writeback(db_project):
    ch1 = await cc.create_chapter(db_project, title="一")
    await db.upsert_plot_line("宿敌之约", status="active",
                              last_advanced="", description="旧状态")
    before = {p["name"]: p for p in await db.list_plot_lines()}
    await store.writeback_pipeline_a(ch1["id"], A_RESULT)
    snaps = await db.get_plot_line_snapshots_for_chapter(ch1["id"])
    assert snaps, "写回波及前应有快照"
    assert snaps[0]["last_advanced"] == before["宿敌之约"]["last_advanced"]


async def test_writeback_pipeline_b_whitelist_guard(db_project):
    ch = await cc.create_chapter(db_project, title="一")
    await db.upsert_character("林风", role="protagonist")
    whitelist = await store.get_entity_whitelist()
    assert "林风" in whitelist and "黑衣老者" not in whitelist

    stats = await store.writeback_pipeline_b(ch["id"], B_RESULT, whitelist)
    assert stats["characters"] == 2 and stats["skipped"] == ["凭空路人甲"]
    assert stats["facts"] == 1

    lin = await db.get_character("林风")
    assert lin["cs_location"] == "遗迹深处"
    assert lin["cs_level"] == "炼气三层"
    assert lin["cs_items"] == "传承信物"
    import json
    assert "遗迹入口" in json.loads(lin["cs_knowledge"])
    # 新角色入库并记录 created_at_chapter_id（回滚清理依据）
    new_char = await db.get_character("黑衣老者")
    assert new_char and new_char["created_at_chapter_id"] == ch["id"]
    # 白名单外且未标记 → 完全不入库
    assert await db.get_character("凭空路人甲") is None
    # 角色状态历史
    states = await db.latest_char_states()
    assert any(s["character"] == "林风" for s in states)


async def test_writeback_self_check(db_project):
    ch = await cc.create_chapter(db_project, title="一")
    await store.writeback_pipeline_a(ch["id"], A_RESULT)
    assert await store.writeback_self_check(ch["id"]) == []
    # 制造悬挂引用 → 自检发现
    await db.insert_chapter({"id": "ghost", "order_index": 99.0,
                             "title": "幽灵"})
    await db.delete_chapter_row("ghost")  # 删章节但手工留时间线
    await db.write(lambda conn: conn.execute(
        "INSERT INTO canon_timeline (chapter_id, seq, summary) "
        "VALUES ('ghost', 1, '孤儿事件')"))
    problems = await store.writeback_self_check(ch["id"])
    assert any(p["table"] == "canon_timeline" for p in problems)


async def test_full_finalize_and_rollback_roundtrip(db_project, monkeypatch):
    """定稿写回 → 逆向回滚 的完整往返一致性。"""
    from core.canon import reviewer as reviewer_mod
    from core.commands import finalize_chapter, rollback

    ch = await cc.create_chapter(db_project, title="定稿章")
    await db.update_chapter(ch["id"], status="drafted", word_count=100)
    await db.upsert_character("林风", role="protagonist")
    # 正文落盘（供定稿 .txt 投影与回滚归档）
    file_manager.write_text(
        file_manager.find_chapter_file(db_project, ch["number"], ch["title"]),
        "本章正文内容。")

    async def fake_semantic(*args, **kwargs):
        return []
    monkeypatch.setattr(reviewer_mod, "semantic_review", fake_semantic)

    async def fake_call(model, messages, **kw):
        # 管线 B 走 call_fn 注入（A 由 extract_a_fn 覆盖）
        return B_RESULT

    result = await finalize_chapter.finalize(
        db_project, ch["id"], "fake-model",
        call_fn=fake_call, extract_a_fn=_fake_a)
    assert result["ok"] is True
    row = await db.get_chapter(ch["id"])
    assert row["status"] == "finalized"
    assert row["notes"] == A_RESULT["summary"]
    assert os.path.exists(
        file_manager.finalized_txt_path(db_project, row["number"],
                                        row["title"]))
    states = await db.latest_char_states()
    assert any(s["character"] == "黑衣老者" for s in states)

    # 回滚（单事务还原）
    rb = await rollback.un_finalize(db_project, ch["id"])
    assert rb["ok"] is True
    row = await db.get_chapter(ch["id"])
    assert row["status"] == "revised"
    assert await db.list_chapter_timeline(ch["id"]) == []
    assert await db.get_summary(ch["id"]) is None
    assert await db.get_character("黑衣老者") is None      # 本章新登场被清理
    lin = await db.get_character("林风")
    assert lin["cs_location"] == ""                        # cs 回退（无前快照→清空）
    assert await db.get_character("凭空路人甲") is None
    facts = await db.list_facts()
    assert not facts                                       # 本章事实被删
    # 本章新登记的两条剧情线随回滚删除（started_at=本章）
    assert await db.list_plot_lines() == []
    assert not os.path.exists(
        file_manager.finalized_txt_path(db_project, row["number"],
                                        row["title"]))
    # 归档存在（回滚前自动存档）
    drafts = await db.list_drafts(ch["id"])
    assert any(d["status"] == "archived" for d in drafts)


async def _fake_a(project, number, title, content, model="", call_fn=None,
                  **kw):
    return A_RESULT


async def test_rollback_refuses_non_latest(db_project):
    from core.commands import rollback
    ch1 = await cc.create_chapter(db_project, title="一")
    ch2 = await cc.create_chapter(db_project, title="二")
    # ch1 定稿（假定最新），ch2 也定稿 → 最新为 ch2；回滚 ch1 应拒绝
    await db.update_chapter(ch1["id"], status="finalized")
    await db.update_chapter(ch2["id"], status="finalized")
    rb = await rollback.un_finalize(db_project, ch1["id"])
    assert rb["ok"] is False
    row = await db.get_chapter(ch1["id"])
    assert row["status"] == "finalized"
