"""设计域测试：新 DAO / 消息装配 / 世界观投影 / 大纲解析与采纳。"""
import os

import flet as ft

from core import db, design, file_manager


# ==================== 数据层：设计域三表 ====================

async def test_project_core_theme_and_worldbuilding_dropped(db_project):
    """project_core 新增 theme、废弃 worldbuilding（无读写调用点）。"""
    proj = await db.get_project()
    assert "theme" in proj
    assert "worldbuilding" not in proj


async def test_design_messages_dao(db_project):
    await db.insert_design_message("user", "你好", mode="guide", step="core")
    await db.insert_design_message("assistant", "你好，我们开始吧")
    hist = await db.list_design_messages()
    assert [m["role"] for m in hist] == ["user", "assistant"]
    assert hist[0]["step"] == "core"
    assert (await db.list_design_messages(limit=1))[0]["role"] == "assistant"
    await db.clear_design_messages()
    assert await db.list_design_messages() == []


async def test_world_sections_dao(db_project):
    await db.upsert_world_section("power", label="力量体系", content="九阶",
                                  order_index=1.0)
    await db.upsert_world_section("power", content="十阶")   # 只改正文
    secs = await db.list_world_sections()
    assert len(secs) == 1
    assert secs[0]["label"] == "力量体系" and secs[0]["content"] == "十阶"
    await db.delete_world_section("power")
    assert await db.list_world_sections() == []


async def test_guide_progress_roundtrip(db_project):
    assert (await design.load_progress())["current_step"] == "core"
    prog = await design.save_progress(idea="一个验尸官的故事",
                                      current_step="cast",
                                      steps_done=["core"])
    assert prog["steps_done"] == ["core"] and prog["idea"] == "一个验尸官的故事"
    prog = await design.mark_done("cast")
    assert "cast" in prog["steps_done"] and prog["current_step"] == "world"
    assert design.next_step("structure") is None
    assert design.step_by_key("world")["label"] == "世界观"


# ==================== context：消息装配（纯函数） ====================

def test_trim_history_keeps_newest_within_budget():
    hist = [{"role": "user", "content": "旧" * 3000},
            {"role": "assistant", "content": "新" * 30}]
    trimmed = design.context.trim_history(hist, budget_tokens=10)
    assert trimmed == [hist[1]]      # 超预算的旧消息被整条丢弃


def test_assemble_messages_structure():
    hist = [{"role": "user", "content": "旧问题"},
            {"role": "assistant", "content": "旧回答"}]
    msgs = design.context.assemble_messages(
        hist, "新诉求", "【类型】玄幻", system_text="SYS", focus="FOCUS")
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assert msgs[1]["content"] == "旧问题"
    assert msgs[2]["role"] == "assistant"
    last = msgs[-1]["content"]
    assert "FOCUS" in last and "玄幻" in last and "新诉求" in last


async def test_build_chat_messages_persists_and_carries_history(db_project):
    await db.update_project(genre="玄幻", premise="少年复仇", theme="自由与代价")
    msgs = await design.build_chat_messages(db_project, "帮我设计主角")
    assert msgs[0]["role"] == "system"
    assert "玄幻" in msgs[-1]["content"]
    assert "少年复仇" in msgs[-1]["content"]
    hist = await design.load_history()
    assert hist[-1]["role"] == "user" and hist[-1]["content"] == "帮我设计主角"
    # 第二轮带上第一轮历史（多轮记忆）
    msgs2 = await design.build_chat_messages(db_project, "继续")
    assert any(m["content"] == "帮我设计主角" for m in msgs2)


async def test_build_chat_messages_guide_focus(db_project):
    msgs = await design.build_chat_messages(db_project, "开始", mode="guide",
                                            step="world")
    assert "世界观" in msgs[-1]["content"]


# ==================== world：分节 + settings.md 投影 ====================

async def test_world_sections_seed_and_projection(db_project):
    sections = await design.load_sections(db_project)
    keys = [s["section_key"] for s in sections]
    assert "power" in keys and "misc" in keys
    await design.save_section(db_project, "power", "灵气分为九阶")
    text = file_manager.read_settings_md(db_project)
    assert "灵气分为九阶" in text and "力量 / 科技体系" in text


async def test_world_imports_legacy_settings(db_project):
    file_manager.write_settings_md(db_project, "# 我的世界\n旧设定内容在此")
    sections = await design.load_sections(db_project)
    misc = next(s for s in sections if s["section_key"] == "misc")
    assert "旧设定内容在此" in misc["content"]


# ==================== cast：AI 文本 → 角色卡字段 ====================

def test_parse_character_snippet():
    text = ("姓名：林潇\n定位：主角\n别名：小潇、剑痴\n"
            "性格：坚韧冷静\n能力：御剑术\n秘密：其实是前朝遗孤")
    p = design.parse_character_snippet(text)
    assert p["name"] == "林潇"
    assert p["role"] == "protagonist"
    assert "小潇" in p["aliases"] and "剑痴" in p["aliases"]
    assert p["personality"] == "坚韧冷静"
    assert p["abilities"] == "御剑术"
    assert any("遗孤" in k for k in p["knowledge"])


def test_parse_character_snippet_fallback():
    p = design.parse_character_snippet("一个沉默寡言的赏金猎人，随身带着旧枪。")
    assert p["name"] == "新角色"
    assert "赏金猎人" in p["background"]


# ==================== outline：解析 + 采纳建章 ====================

def test_parse_outline_normalizes():
    raw = {"volumes": [
        {"title": "卷一", "summary": "开篇", "chapters": [
            {"title": "第1章", "role": "开篇", "characters": "林潇、赵长老"},
            {"title": "", "purpose": "空标题应被丢弃"},
        ]},
        {"title": "", "chapters": []},   # 无章节的卷被丢弃
    ]}
    out = design.parse_outline(raw)
    assert design.count_chapters(out) == 1
    ch = out["volumes"][0]["chapters"][0]
    assert ch["characters"] == ["林潇", "赵长老"]


async def test_apply_outline_creates_chapters(db_project):
    from core.commands import apply_outline as ao
    volumes = [{"title": "卷一", "chapters": [
        {"title": "初入宗门", "role": "开篇", "purpose": "建立主角",
         "key_events": "觉醒", "characters": ["林潇"]},
        {"title": "试炼", "role": "升级", "purpose": "引入冲突",
         "key_events": "比试", "characters": []},
    ]}]
    res = await ao.apply_outline(db_project, volumes)
    assert res["created"] == 2 and res["removed"] == 0
    chapters = await db.list_chapters()
    assert [c["number"] for c in chapters] == [1, 2]
    assert chapters[0]["title"] == "初入宗门"
    assert "初入宗门" in file_manager.read_outline_md(db_project)
    assert os.path.exists(
        file_manager.find_chapter_file(db_project, 1, "初入宗门"))


async def test_apply_outline_replace_clears_existing(db_project):
    from core.commands import apply_outline as ao
    first = [{"title": "卷一", "chapters": [{"title": f"旧{i}"}
                                           for i in range(3)]}]
    await ao.apply_outline(db_project, first)
    assert len(await db.list_chapters()) == 3
    second = [{"title": "新卷", "chapters": [{"title": "新章"}]}]
    res = await ao.apply_outline(db_project, second, replace=True)
    assert res["removed"] == 3
    chapters = await db.list_chapters()
    assert [c["title"] for c in chapters] == ["新章"]
    assert [c["number"] for c in chapters] == [1]


class _StubApp:
    """WriterApp 最小替身：仅满足设计界面构造与加载所需接口。"""

    def __init__(self, project):
        self.project = project
        self.current = None
        self.current_model = "test-model"
        self.generating = False
        self.page = None
        self.logs: list[str] = []

    def append_log(self, msg: str) -> None:
        self.logs.append(msg)

    async def check_connection(self, *, notify: bool = False) -> bool:
        return True

    async def reload_chapters(self, e=None) -> None:
        pass

    async def refresh_side_panels(self, chapter) -> None:
        pass


async def test_design_view_headless_construct_load_and_adopt(db_project):
    """设计界面可无头构造 + 加载 + 板块切换 + 采纳落库（防引用回归）。"""
    from ui.views.design import DesignView
    app = _StubApp(db_project)
    view = DesignView(app)
    await view.load()
    assert len(view._section_panels) == 5
    assert len(view.world_sections) >= 5          # 默认分节已播种
    view.select_section("outline")
    view.select_section("world")
    assert view.section == "world"

    await view.apply_adoption("premise", "验尸官能听见亡者遗言")
    assert (await db.get_project())["premise"] == "验尸官能听见亡者遗言"

    await view.apply_adoption("world", "灵气分为九阶", section_key="power")
    assert "灵气分为九阶" in file_manager.read_settings_md(db_project)

    # 再采纳一次（追加）应保留上一段
    await view.apply_adoption("world", "第九阶可窥天机", section_key="power")
    text = file_manager.read_settings_md(db_project)
    assert "灵气分为九阶" in text and "第九阶可窥天机" in text


async def test_apply_outline_appends_after_existing(db_project):
    from core.commands import apply_outline as ao
    await ao.apply_outline(db_project, [{"title": "卷一",
                                         "chapters": [{"title": "A"}]}])
    await ao.apply_outline(db_project, [{"title": "卷二",
                                         "chapters": [{"title": "B"}]}])
    chapters = await db.list_chapters()
    assert [c["title"] for c in chapters] == ["A", "B"]
    assert [c["number"] for c in chapters] == [1, 2]


# ==================== choice 结构化选项协议 ====================

_CHOICE_RAW = (
    '这是分析正文。\n\n```choice\n'
    '{"question":"主角动机偏哪种？","options":['
    '{"id":"A","title":"复仇驱动","detail":"为家族雪恨"},'
    '{"id":"B","title":"守诺驱动","detail":"为旧日之约"}]}\n```')


def test_parse_choices_valid():
    clean, choices = design.parse_choices(_CHOICE_RAW)
    assert clean == "这是分析正文。"
    assert choices["question"] == "主角动机偏哪种？"
    assert [o["id"] for o in choices["options"]] == ["A", "B"]
    assert choices["options"][1]["detail"] == "为旧日之约"
    assert design.has_choice_marker(_CHOICE_RAW)


def test_parse_choices_invalid_returns_raw():
    raw = "正文\n\n```choice\n这不是 JSON\n```"
    clean, choices = design.parse_choices(raw)
    assert choices is None
    assert clean == raw          # 解析失败：原样返回，不吞内容


def test_parse_choices_absent():
    clean, choices = design.parse_choices("普通回复，没有选项块")
    assert choices is None and clean == "普通回复，没有选项块"


# ==================== 采纳提炼（DI，禁网） ====================

async def test_extract_for_target_uses_di_and_focuses_field():
    captured = {}

    async def fake_call(model, messages):
        captured["messages"] = messages
        return "  提炼后的主题立意  "

    out = await design.extract_for_target(
        "_proj", "theme", "AI 的长篇讨论原文", current="旧主题",
        model="m", call_fn=fake_call)
    assert out == "提炼后的主题立意"
    joined = "\n".join(m["content"] for m in captured["messages"])
    assert "主题立意" in joined
    assert "AI 的长篇讨论原文" in joined and "旧主题" in joined


async def test_build_chat_messages_includes_protocol(db_project):
    msgs = await design.build_chat_messages(db_project, "开始设计")
    assert "choice" in msgs[0]["content"]


# ==================== 设计界面布局 / 协作台交互（无头） ====================

def test_design_panels_stretch_and_fill(db_project):
    from ui.views.design import DesignView
    view = DesignView(_StubApp(db_project))
    for panel in (view.story_panel, view.cast_panel,
                  view.world_panel, view.outline_panel):
        assert panel.content.horizontal_alignment == \
            ft.CrossAxisAlignment.STRETCH
        assert (panel.left, panel.top, panel.right, panel.bottom) == (0, 0, 0, 0)
    assert view.guide.horizontal_alignment == ft.CrossAxisAlignment.STRETCH


async def test_chat_preselect_target_by_section(db_project):
    from ui.views.design import DesignView
    view = DesignView(_StubApp(db_project))
    view.chat.set_section("cast")
    assert view.chat.accept_target.value == "character"
    view.chat.set_section("world")
    assert view.chat.accept_target.value == "world"
    view.chat.set_section("story")
    assert view.chat.accept_target.value == "premise"


async def test_chat_reload_renders_choice_without_error(db_project):
    await db.insert_design_message("assistant", _CHOICE_RAW)
    from ui.views.design import DesignView
    view = DesignView(_StubApp(db_project))
    await view.load()
    assert len(view.chat.chat_view.controls) >= 1
