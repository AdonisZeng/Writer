"""prompt_builder.py 测试：渲染、模板三级覆盖、Token 预算、时间线块状截断、装配。"""
import os

from core import db, file_manager, prompt_builder as pb, paths


# ==================== PromptBuilder.render ====================

def test_render_replaces_and_cleans():
    b = pb.PromptBuilder("Hello {{name}}，{{missing}}！\n\n\n\nEnd")
    out = b.render(name="世界")
    assert "世界" in out and "{{" not in out
    assert "\n\n\n" not in out


def test_resolve_token_budget():
    assert pb.resolve_token_budget({"context_limit": 32768}) == 28672
    assert pb.resolve_token_budget({}) == 4096            # 缺省 8192-4096
    assert pb.resolve_token_budget({"context_limit": 100}) == 4096  # 下限兜底


# ==================== 模板三级覆盖 ====================

def test_load_template_builtin_default():
    assert "协作引擎" in pb.load_template("system.md")


def test_load_template_user_and_project_override(novel_root):
    os.makedirs(paths.USER_PROMPTS_DIR, exist_ok=True)
    with open(os.path.join(paths.USER_PROMPTS_DIR, "system.md"), "w",
              encoding="utf-8") as f:
        f.write("用户级覆盖")
    assert pb.load_template("system.md") == "用户级覆盖"

    proj_prompts = paths.project_prompts_dir("_test_project")
    os.makedirs(proj_prompts, exist_ok=True)
    with open(os.path.join(proj_prompts, "system.md"), "w",
              encoding="utf-8") as f:
        f.write("项目级覆盖")
    assert pb.load_template("system.md", "_test_project") == "项目级覆盖"
    # 无项目上下文时仍是用户级
    assert pb.load_template("system.md") == "用户级覆盖"


def test_release_builtin_prompts_copies_once(novel_root):
    pb.release_builtin_prompts()
    first = os.path.getmtime(os.path.join(paths.USER_PROMPTS_DIR, "system.md"))
    with open(os.path.join(paths.USER_PROMPTS_DIR, "system.md"), "w",
              encoding="utf-8") as f:
        f.write("用户已修改")
    pb.release_builtin_prompts()  # 二次释放不覆盖用户修改
    assert "用户已修改" in open(os.path.join(paths.USER_PROMPTS_DIR,
                                            "system.md"), encoding="utf-8").read()
    assert first  # 首次复制确实发生过


# ==================== build_notes_timeline ====================

def _make_chapters(n: int) -> list[dict]:
    return [{"id": f"ch{i}", "order_index": float(i + 1), "number": i + 1,
             "title": f"标题{i}", "notes": f"要点{i}。" * 20}
            for i in range(n)]


def test_timeline_excludes_current_and_far_become_headers():
    chapters = _make_chapters(30)
    current = {"id": "ch30", "order_index": 31.0}
    text = pb.build_notes_timeline(chapters, current, full_window=25,
                                   max_chars=100000)
    assert "要点4" not in text       # 远期仅标题（第5章外无正文要点）
    assert "第5章 标题4" in text     # 但标题行保留
    assert "要点29" in text          # 近章完整收录
    assert "标题29" in text


def test_timeline_truncation_is_block_level():
    chapters = _make_chapters(10)
    current = {"id": "ch10", "order_index": 11.0}
    text = pb.build_notes_timeline(chapters, current, full_window=25,
                                   max_chars=400)
    blocks = [b for b in text.split("\n\n") if b]
    assert len(blocks) >= 1
    for block in blocks:            # 每个块都是完整的「标题+要点」结构
        assert block.startswith("第")
    # 保留的是最近的章节，最先丢弃的是最旧的
    assert "标题9" in text
    assert "标题0" not in text or blocks


# ==================== build_draft_messages ====================

async def test_build_draft_messages_assembly(db_project):
    from core.commands import chapters as cc
    await db.update_project(writing_style="冷峻克制",
                            global_guidance="禁止网络用语")
    file_manager.write_settings_md(db_project, "# 世界观\n灵气复苏。\n")
    prev = await cc.create_chapter(db_project, title="前一章")
    await db.update_chapter(prev["id"], notes="主角觉醒金手指")
    new_ch = await cc.create_chapter(db_project, title="新篇章")
    await db.update_chapter(new_ch["id"], role="转折", purpose="推动主线",
                            key_events="秘境开启",
                            characters='["林风","赵长老"]')
    new_ch = await db.get_chapter(new_ch["id"])  # 与真实应用一致：更新后重取

    built = await pb.build_draft_messages(db_project, new_ch, "要紧张")
    msgs = built["messages"]
    assert msgs[0]["role"] == "system" and msgs[1]["role"] == "user"
    # Tier1：文风/禁忌/世界观进 System
    assert "冷峻克制" in msgs[0]["content"]
    assert "禁止网络用语" in msgs[0]["content"]
    assert "灵气复苏" in msgs[0]["content"]
    # Tier3：时间线要点 / 细纲 / 指导进 User
    assert "主角觉醒金手指" in msgs[1]["content"]
    assert "新篇章" in msgs[1]["content"]
    assert "要紧张" in msgs[1]["content"]
    assert "林风" in msgs[1]["content"]
    assert "{{" not in msgs[0]["content"] + msgs[1]["content"]
    assert built["budget"] > 0 and built["warnings"] == []


async def test_build_draft_messages_budget_compression(db_project, monkeypatch):
    """超预算 → 历史要点自动压缩 + 警告。"""
    from core import config
    monkeypatch.setitem(config.CONFIG, "context_limit", 1024)  # 预算压到下限 4096
    file_manager.write_settings_md(db_project, "设定" * 8000)  # Tier1 撑爆预算
    from core.commands import chapters as cc
    new_ch = await cc.create_chapter(db_project, title="超长章")

    built = await pb.build_draft_messages(db_project, new_ch, "")
    assert built["warnings"], "超预算应有压缩警告"
    assert built["tiers"]["total"] < pb.estimate_tokens("设定" * 8000) + 500


async def test_system_text_includes_story_core(db_project):
    """设计界面产出的定位字段（类型/前提/主题/梗概）应注入 Tier 1 System。"""
    await db.update_project(genre="仙侠", premise="少年复仇",
                            theme="自由与代价", synopsis="主角一路成长",
                            writing_style="冷峻克制")
    file_manager.write_settings_md(db_project, "灵气复苏。")
    sys_text = await pb.build_system_text(db_project)
    for kw in ("仙侠", "少年复仇", "自由与代价", "主角一路成长",
               "冷峻克制", "灵气复苏"):
        assert kw in sys_text
    assert "{{" not in sys_text


async def test_canon_stub_empty(db_project):
    from core.canon import build_canon_context, run_gate
    assert await build_canon_context("ch1") == ""
    # semantic=False：只验证确定性层（语义审查需 LLM，属离线禁区）
    gate = await run_gate(
        "_test_project",
        {"id": "ch1", "number": 1, "status": "outlined", "word_count": 0,
         "title": "t"},
        "内容", semantic=False)
    assert gate["verdict"] == "PASS"
