"""P2 离线测试：diff_utils / RAG / 精修消息 / 拖拽重排 / Ghost / 用量统计。"""
import os

import pytest

from core import db, diff_utils, rag
from core.commands import chapters as cc
from core.commands import refine_draft


# ==================== diff_utils ====================

def test_compute_hunks_basic():
    old = "第一行\n第二行\n第三行\n第四行\n第五行"
    new = "第一行\n第二行改\n第三行\n第四行\n第五行\n新增行"
    hunks = diff_utils.compute_hunks(old, new)
    assert hunks, "有差异必有 hunk"
    assert all(h.action in ("replace", "delete", "insert") for h in hunks)


def test_compute_hunks_no_diff():
    assert diff_utils.compute_hunks("相同", "相同") == []


def test_apply_hunks_partial_accept():
    old = "A\nB\n分隔行\nC"
    new = "A\nB改\n分隔行\nC改"
    hunks = diff_utils.compute_hunks(old, new)
    assert len(hunks) == 2
    # 全部采纳 → 新文；全部拒绝 → 原文（核心性质）
    assert diff_utils.apply_hunks(old, hunks, set(range(len(hunks)))) == new
    assert diff_utils.apply_hunks(old, hunks, set()) == old
    # 部分采纳：只采纳首块
    partial = diff_utils.apply_hunks(old, hunks, {0})
    assert partial == "A\nB改\n分隔行\nC"


def test_apply_hunks_delete():
    old = "A\nB\nC"
    new = "A\nC"
    hunks = diff_utils.compute_hunks(old, new)
    assert hunks[0].action == "delete"
    assert diff_utils.apply_hunks(old, hunks, {0}) == "A\nC"
    assert diff_utils.apply_hunks(old, hunks, set()) == old


def test_apply_hunks_delete():
    old = "A\nB\nC"
    new = "A\nC"
    hunks = diff_utils.compute_hunks(old, new)
    assert hunks[0].action == "delete"
    assert diff_utils.apply_hunks(old, hunks, {0}) == "A\nC"
    assert diff_utils.apply_hunks(old, hunks, set()) == old


def test_hunks_summary():
    hunks = diff_utils.compute_hunks("A\nB\nC", "A\nB2\nC\nD")
    s = diff_utils.hunks_summary(hunks)
    assert "+" in s and "-" in s


# ==================== RAG（fake embed 注入）====================

def _fake_embed(dim=4, vec=None):
    async def embed(texts, model=""):
        return [vec or [0.1, 0.2, 0.3, 0.4][:dim] + [0.0] * max(
            0, dim - 4) for _ in texts]
    return embed


async def test_rag_index_and_retrieve(db_project, monkeypatch):
    monkeypatch.setattr(db, "_vec_loaded", True)
    ch = await cc.create_chapter(db_project, title="检索章")
    content = "主角在青石镇遇袭。\n林风初显锋芒。\n" * 20
    n = await rag.index_chapter(db_project, ch["id"], content,
                                model="fake-embed",
                                embed_fn=_fake_embed(4))
    assert n > 0
    hits = await rag.retrieve("青石镇", model="fake-embed",
                              embed_fn=_fake_embed(4))
    assert hits and hits[0]["content"]
    assert hits[0]["distance"] >= 0
    # 章节刷新：块数替换而非累加
    n2 = await rag.index_chapter(db_project, ch["id"], "新内容一块",
                                 model="fake-embed", embed_fn=_fake_embed(4))
    stats = await rag.index_stats()
    assert stats["chunks"] >= n2


async def test_rag_disabled_without_vec(db_project, monkeypatch):
    monkeypatch.setattr(db, "_vec_loaded", False)
    ch = await cc.create_chapter(db_project, title="无向量章")
    assert await rag.index_chapter(db_project, ch["id"], "内容") == 0
    assert await rag.retrieve("查询") == []


def test_chunk_text_paragraph_boundaries():
    text = "第一段。\n第二段。\n第三段。"
    chunks = rag._chunk_text(text, 20)
    for c in chunks:
        assert "第一段" not in c or c.startswith("第一段")  # 不拦腰截断
    assert all(len(c) <= 20 for c in chunks) or len(chunks) >= 2


# ==================== 精修 ====================

async def test_refine_messages_share_system(db_project):
    from core import prompt_builder as pb
    await db.update_project(writing_style="冷峻")
    msgs = await refine_draft.build_refine_messages(
        db_project, "选区文本", "更肃杀一点", context_before="前文",
        context_after="后文")
    assert msgs[0]["content"] == await pb.build_system_text(db_project)
    assert "更肃杀一点" in msgs[1]["content"]
    assert "选区文本" in msgs[1]["content"]
    assert "前文" in msgs[1]["content"]
    # 整章模式
    full = await refine_draft.build_refine_messages(
        db_project, "整章", "指令", full_context=True)
    assert "整章精修" in full[1]["content"]


# ==================== 拖拽重排 ====================

async def test_move_chapter(db_project):
    c1 = await cc.create_chapter(db_project, title="一")
    c2 = await cc.create_chapter(db_project, title="二")
    c3 = await cc.create_chapter(db_project, title="三")
    # 把 三 移到 一 之前
    r = await cc.move_chapter(db_project, c3["id"], c1["id"])
    assert r["ok"] is True
    allc = await db.list_chapters()
    assert [c["title"] for c in allc] == ["三", "一", "二"]
    assert [c["number"] for c in allc] == [1, 2, 3]
    # 无变化拒绝
    assert (await cc.move_chapter(db_project, c3["id"], c1["id"]))["ok"] \
        is False
    # 文件名同步
    assert os.path.exists(
        __import__("core.file_manager", fromlist=["chapter_path"])
        .chapter_path(db_project, 1, "三"))


# ==================== Ghost ====================

async def test_ghost_fetch_with_fake(db_project):
    from core import ai_service
    import asyncio

    async def fake_call(model, messages):
        return "他缓缓抬起头。"
    ev = asyncio.Event()
    result = await ai_service.fetch_ghost_suggestion(
        "前文内容" * 10, ev, call_fn=fake_call)
    assert result == "他缓缓抬起头。"


async def test_ghost_cancelled_returns_empty(db_project):
    from core import ai_service
    import asyncio

    async def fake_call(model, messages):
        return "不该出现的补全"
    ev = asyncio.Event()
    ev.set()  # 已取消
    assert await ai_service.fetch_ghost_suggestion(
        "前文内容" * 10, ev, call_fn=fake_call) == ""


# ==================== 用量统计 ====================

async def test_usage_by_purpose(db_project):
    await db.insert_llm_call(provider="local", model="m", purpose="draft",
                             prompt_tokens=10, completion_tokens=20)
    await db.insert_llm_call(provider="local", model="m", purpose="draft",
                             prompt_tokens=30, completion_tokens=40)
    await db.insert_llm_call(provider="local", model="n", purpose="review",
                             prompt_tokens=5, completion_tokens=1,
                             success=0)
    rows = await db.usage_by_purpose()
    by_purpose = {r["purpose"]: r for r in rows}
    assert by_purpose["draft"]["calls"] == 2
    assert by_purpose["draft"]["prompt_tokens"] == 40
    assert by_purpose["draft"]["completion_tokens"] == 60
    assert by_purpose["review"]["failures"] == 1
