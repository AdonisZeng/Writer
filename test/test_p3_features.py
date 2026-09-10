"""P3 离线测试：后处理 compression/style 步骤、敏感词预检、导出、节拍。"""
import os

from core import db, file_manager, paths, sensitivity
from core.commands import chapters as cc
from core.commands import export_book, finalize_chapter

# 后处理管线 A / B 注入点：测试必须零网络（真实 extract 调用 = 最高思考档，极慢）
_A_RESULT = {"summary": "第30章要点：主角获得传承信物。",
             "timeline": [], "plot_lines": []}


async def _fake_extract_a(project, number, title, content, model="",
                          call_fn=None, **kw):
    return _A_RESULT


async def _fake_extract_b(model, messages, **kw):
    return {"characters": [], "facts": []}


async def test_compression_and_style_steps(db_project):
    """第 30 章（5 的倍数）定稿触发 compression + style_analysis。"""
    from core.canon import store
    for i in range(1, 31):
        ch = await cc.create_chapter(db_project, title=f"章{i}")
        if i == 1:
            await db.update_chapter(ch["id"], notes="旧章关键伏笔：主角身世之谜")
        if i % 5 == 0:
            await db.update_chapter(ch["id"], status="finalized")
            file_manager.write_text(
                file_manager.find_chapter_file(db_project, i, f"章{i}"),
                f"第{i}章的正文样例文本。")

    async def fake_text(model, messages, **kw):
        assert len(messages) == 2
        return "压缩归档内容" if "压缩" in messages[1]["content"] \
            else "文风指令：多用短句。"

    chapter = (await db.list_chapters())[29]  # 第30章
    # 管线 A / B 也必须注入 fake，否则会真的调用本机模型（extract = 最高思考档）
    r = await finalize_chapter.run_post_process(
        db_project, chapter, "正文", "fake", call_fn=_fake_extract_b,
        extract_a_fn=_fake_extract_a, text_call_fn=fake_text)
    assert r["ok"] is True
    steps = r["steps"]
    assert steps["compression"]["ok"]
    assert steps["compression"]["data"]["archived_chapters"] == 1
    assert steps["style_analysis"]["ok"]
    proj = await db.get_project()
    assert proj["writing_style"] == "文风指令：多用短句。"
    archives = await db.list_archive_summaries(limit=2)
    assert archives and "压缩归档内容" in archives[0]["summary"]
    # 归档进入 Canon 上下文
    ctx = await store.get_canon_bundle(chapter)
    assert ctx["archives"]


async def test_compression_skips_non_multiple_of_5(db_project):
    ch = await cc.create_chapter(db_project, title="第2章")
    r = await finalize_chapter.run_post_process(
        db_project, ch, "正文", "fake", call_fn=_fake_extract_b,
        extract_a_fn=_fake_extract_a, text_call_fn=lambda *a, **k: None)
    # 非 5 的倍数：压缩与文风自学习均跳过（不调用 text_call_fn）
    assert r["steps"]["compression"]["data"]["skipped"] is True
    assert r["steps"]["style_analysis"]["data"]["skipped"] is True


# ==================== 敏感词预检 ====================

def test_sensitivity_check_counts():
    issues = sensitivity.check("他说了禁语A。又说了禁语A。", ["禁语A"],
                               chapter_id="ch1")
    assert len(issues) == 1
    assert issues[0].severity == "info"
    assert "2 次" in issues[0].message


def test_sensitivity_load_words_from_project_file(novel_root):
    from core.canon import validator  # noqa: F401
    path = sensitivity.create_project_template("_test_project")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# 注释行\n违禁词甲\n违禁词乙\n违禁词甲\n")
    words = sensitivity.load_words("_test_project")
    assert words == ["违禁词甲", "违禁词乙"]


async def test_gate_includes_sensitivity(db_project, monkeypatch):
    from core import config
    path = sensitivity.create_project_template(db_project)
    with open(path, "w", encoding="utf-8") as f:
        f.write("违禁词\n")
    monkeypatch.setitem(config.CONFIG, "project", db_project)
    ch = await cc.create_chapter(db_project, title="敏感章")
    gate = await canon_gate_run(db_project, ch, "正文包含违禁词一次")
    hits = [i for i in gate["issues"] if i.code == "sensitive_word"]
    assert len(hits) == 1 and hits[0].severity == "info"


async def canon_gate_run(project, ch, content):
    from core import canon
    return await canon.run_gate(project, ch, content, semantic=False)


# ==================== 导出 ====================

async def test_export_book(db_project):
    c1 = await cc.create_chapter(db_project, title="开篇")
    await db.update_project(title="测试之书")
    file_manager.write_text(
        file_manager.find_chapter_file(db_project, 1, "开篇"), "正文甲。")
    result = await export_book.export_book(db_project)
    assert result["ok"] and result["chapters"] == 1
    assert os.path.exists(result["path"])
    content = open(result["path"], encoding="utf-8").read()
    assert "《测试之书》" in content and "第1章 开篇" in content
    assert "正文甲。" in content
    # 仅定稿导出：无定稿章 → 拒绝
    empty = await export_book.export_book(db_project, only_finalized=True)
    assert empty["ok"] is False
