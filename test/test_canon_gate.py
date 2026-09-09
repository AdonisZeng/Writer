"""reviewer / gate / 后处理管线 离线测试（fake call_fn 注入，零网络）。"""
import pytest

from core import canon, db
from core.canon import reviewer
from core.commands import chapters as cc
from core.commands import finalize_chapter


def _fake_json(result):
    async def _call(model, messages, **kwargs):
        # 校验消息结构：System 与 User 两段；任务差异后置在 User
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        return result
    return _call


async def test_reviewer_parses_issues(db_project):
    from core import prompt_builder as pb
    await db.update_project(writing_style="冷峻")
    content = "第一段内容。\n林风使用了九转雷决。\n第三段。"
    result = await reviewer.semantic_review(
        db_project, "ch1", content, "【Canon 资料】", "【视角约束】",
        model="fake", call_fn=_fake_json({"issues": [
            {"type": "knowledge_violation",
             "quote": "九转雷决", "paragraph": 2,
             "message": "林风未习得《九转雷决》（违背第12章设定）"},
            {"type": "timeline_regression", "quote": "", "paragraph": 99,
             "message": "时间线问题"},
        ]}))
    assert len(result) == 2
    assert result[0].severity == "warning"
    assert result[0].paragraph == 2
    assert result[0].quote == "九转雷决"
    assert result[1].paragraph == 0  # 越界段落号被钳到章节级
    assert "知识越权" == result[0].code


async def test_reviewer_empty_issues(db_project):
    result = await reviewer.semantic_review(
        db_project, "ch1", "正文", "", "", model="fake",
        call_fn=_fake_json({"issues": []}))
    assert result == []


async def test_reviewer_system_matches_draft_system(db_project):
    """跨任务缓存纪律：审查与创作的 System 前缀字节级一致（方案 5.3）。"""
    from core import prompt_builder as pb
    await db.update_project(writing_style="冷峻克制",
                            global_guidance="禁忌A")
    msgs = await reviewer.build_review_messages(db_project, "c", "p", "正文")
    system_text = await pb.build_system_text(db_project)
    assert msgs[0]["content"] == system_text
    assert "本轮任务类型" in msgs[1]["content"]  # 差异只出现在 User


async def test_gate_block_on_word_limit(db_project):
    ch = await cc.create_chapter(db_project, title="超长章")
    gate = await canon.run_gate(
        db_project, {"id": ch["id"], "number": 1, "status": "drafted",
                     "word_count": 0, "title": "超长章"},
        "字" * 9001, semantic=True)
    assert gate["verdict"] == "BLOCK"
    assert any(i.code == "word_limit_exceeded" for i in gate["issues"])


async def test_gate_scoped_not_blocking_on_other_chapters(db_project):
    """其他章节的元数据错误不阻断本章 Gate（进诊断面板而非闸门）。"""
    ch = await cc.create_chapter(db_project, title="正常章")
    ghost = await cc.create_chapter(db_project, title="幽灵章")
    await db.update_chapter(ghost["id"], status="weird")  # 别章状态非法
    gate = await canon.run_gate(
        db_project, {"id": ch["id"], "number": 1, "status": "drafted",
                     "word_count": 10, "title": "正常章"},
        "正文", semantic=False)
    assert gate["verdict"] == "PASS"
    # 但全项目扫描能看到它
    scan = await canon.scan_project_issues()
    assert any(i.code == "status_invalid" and i.chapter_id == ghost["id"]
               for i in scan)


async def test_post_process_steps_persist_and_retry(db_project):
    ch = await cc.create_chapter(db_project, title="后处理章")
    await db.upsert_character("林风", role="protagonist")

    attempts = {"a": 0}

    async def flaky_a(project, number, title, content, model="", call_fn=None,
                      **kw):
        attempts["a"] += 1
        if attempts["a"] == 1:
            raise RuntimeError("第一次抽取失败")
        return {"summary": "要点", "timeline": [], "plot_lines": []}

    # 第一次：critical 步骤失败 → 整体失败且中止
    r1 = await finalize_chapter.run_post_process(
        db_project, ch, "正文", "fake", extract_a_fn=flaky_a)
    assert r1["ok"] is False
    assert "chapter_notes" in r1["steps"] and not r1["steps"]["chapter_notes"]["ok"]
    rec = await db.get_pp_step(ch["id"], "chapter_notes")
    assert rec["ok"] == 0 and rec["attempts"] == 1

    # 第二次 only_failed：A 成功，canon_writeback 复用结果，B 用 fake call
    async def fake_call(model, messages, **kw):
        return {"characters": [], "facts": []}
    r2 = await finalize_chapter.run_post_process(
        db_project, ch, "正文", "fake", only_failed=True,
        extract_a_fn=flaky_a, call_fn=fake_call)
    assert r2["ok"] is True
    rec = await db.get_pp_step(ch["id"], "chapter_notes")
    assert rec["ok"] == 1 and rec["attempts"] == 2  # 重试计数累加
    row = await db.get_chapter(ch["id"])
    assert row["notes"] == "要点"


async def test_gate_finalize_block_keeps_status(db_project):
    ch = await cc.create_chapter(db_project, title="超限章")
    await db.update_chapter(ch["id"], status="drafted", word_count=99999)
    result = await finalize_chapter.finalize(db_project, ch["id"], "fake")
    assert result["verdict"] == "BLOCK"
    row = await db.get_chapter(ch["id"])
    assert row["status"] == "drafted"
