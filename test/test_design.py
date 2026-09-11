"""设计域测试：新 DAO / 消息装配 / 世界观投影 / 大纲解析与采纳。"""
import os
import types

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
                                      current_step="core", steps_done=[])
    assert prog["steps_done"] == [] and prog["idea"] == "一个验尸官的故事"
    prog = await design.mark_done("core")
    assert prog["steps_done"] == ["core"] and prog["current_step"] == "world"
    # 四步顺序：内核 → 世界观 → 结构 → 人物（人物压轴）
    assert [s["key"] for s in design.GUIDE_STEPS] == \
        ["core", "world", "structure", "cast"]
    assert design.next_step("structure") == "cast"
    assert design.next_step("cast") is None
    assert design.step_by_key("world")["label"] == "世界观"


async def test_mark_done_never_pulls_progress_back(db_project):
    """回看已完成步骤再标记：只补记完成，进度不被往回拽。"""
    await design.save_progress(idea="", current_step="structure",
                               steps_done=["core", "world"])
    prog = await design.mark_done("core")
    assert "core" in prog["steps_done"]
    assert prog["current_step"] == "structure"      # 保持在进行中的步骤


async def test_guide_focus_injects_finish_rules(db_project):
    """收尾约定：仅 core / world 注入；world 额外要求分节计划。"""
    msgs = await design.build_chat_messages(db_project, "开始", mode="guide",
                                            step="core")
    assert "本步收尾约定" in msgs[-1]["content"]
    assert '"step_done": "core"' in msgs[-1]["content"]
    msgs = await design.build_chat_messages(db_project, "继续", mode="guide",
                                            step="world")
    assert "本步收尾约定" in msgs[-1]["content"]
    assert "sections" in msgs[-1]["content"]
    msgs = await design.build_chat_messages(db_project, "继续", mode="guide",
                                            step="cast")
    assert "本步收尾约定" not in msgs[-1]["content"]   # 人物步先不做收尾


async def test_participation_directive_in_guide_focus(db_project):
    """作者参与程度按 config 档位注入引导聚焦块（默认高频=原行为）。"""
    from core import config
    msgs = await design.build_chat_messages(db_project, "开始", mode="guide",
                                            step="world")
    assert "作者参与程度：高" in msgs[-1]["content"]
    config.set_key("design_participation", "low")
    try:
        msgs = await design.build_chat_messages(db_project, "继续",
                                                mode="guide", step="world")
        assert "作者参与程度：低" in msgs[-1]["content"]
        assert "尽量不提问" in msgs[-1]["content"]
    finally:
        config.set_key("design_participation", "high")
    # 自由对话不注入参与程度指令
    msgs = await design.build_chat_messages(db_project, "随便聊聊")
    assert "作者参与程度" not in msgs[-1]["content"]


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


class _StubStatusBar:
    """状态栏替身：send / _run_chat 收尾会读写状态。"""

    def __init__(self):
        self.state_text = types.SimpleNamespace(value="就绪")
        self.states: list[str] = []

    def set_state(self, state: str) -> None:
        self.states.append(state)
        self.state_text.value = {"busy": "忙碌", "ready": "就绪",
                                 "offline": "离线"}.get(state, state)


class _StubApp:
    """WriterApp 最小替身：仅满足设计界面构造与加载所需接口。"""

    def __init__(self, project):
        self.project = project
        self.current = None
        self.current_model = "test-model"
        self.generating = False
        self.page = None
        self.logs: list[str] = []
        self.status_bar = _StubStatusBar()

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


_STEP_DONE_RAW = (
    "世界观方案正文……\n\n```choice\n"
    '{"question":"是否采纳并进入下一步？","step_done":"world",'
    '"sections":[{"label":"修炼体系","content":"一至九阶"},'
    '{"label":"星轨占卜","content":""}],'
    '"options":['
    '{"id":"adopt","action":"adopt","title":"直接采纳","detail":"写入并推进"},'
    '{"id":"tweak","action":"discuss","title":"还要调整"}]}\n```')


def test_parse_choices_step_done_extensions():
    """收尾块：step_done / option.action / sections 均被保留。"""
    clean, ch = design.parse_choices(_STEP_DONE_RAW)
    assert clean == "世界观方案正文……"
    assert ch["step_done"] == "world"
    assert ch["options"][0]["action"] == "adopt"
    assert ch["options"][1]["action"] == "discuss"
    assert ch["sections"][0] == {"label": "修炼体系", "content": "一至九阶"}
    assert ch["sections"][1]["label"] == "星轨占卜"


def test_parse_choices_unknown_action_dropped():
    """未知 action 值不当作动作执行，静默丢弃。"""
    raw = ('正文\n\n```choice\n'
           '{"question":"选一个？","options":['
           '{"id":"A","title":"甲","action":"explode"},'
           '{"id":"B","title":"乙"}]}\n```')
    _, ch = design.parse_choices(raw)
    assert "action" not in ch["options"][0]
    assert "step_done" not in ch


def test_parse_choices_sloppy_model_output():
    """模型乱写也能识别：action 写进 detail 文字、step_done 自造名称。"""
    raw = ('方案正文\n\n```choice\n'
           '{"step_done":"worldview","question":"是否准备进入下一步？","options":['
           '{"id":"A","title":"直接采纳并锁定世界观",'
           '"detail":"action=\\"adopt\\"：确认此世界观框架"},'
           '{"id":"B","title":"微调力量体系",'
           '"detail":"action=\\"discuss\\"：补充等级表后再定稿"}]}\n```')
    clean, ch = design.parse_choices(raw)
    assert clean == "方案正文"
    assert ch["options"][0]["action"] == "adopt"
    assert ch["options"][1]["action"] == "discuss"
    assert design.resolve_step_key(ch["step_done"]) == "world"


def test_resolve_step_key_normalizes_model_aliases():
    assert design.resolve_step_key("core") == "core"
    assert design.resolve_step_key("worldview") == "world"
    assert design.resolve_step_key("故事内核") == "core"
    assert design.resolve_step_key("结构大纲") == "structure"
    assert design.resolve_step_key("character") == "cast"
    assert design.resolve_step_key("") == ""
    assert design.resolve_step_key("毫无关系") == ""


# ==================== 收尾协议审计（自动打回） ====================

def test_protocol_issues_accepts_compliant_finish():
    _, ch = design.parse_choices(_STEP_DONE_RAW)
    assert design.protocol_issues(_STEP_DONE_RAW, ch, step_key="world") == []


def test_protocol_issues_flags_sloppy_finish():
    """真实模型产物：action 写进 detail、step_done 自造、world 缺 sections。"""
    raw = ('方案正文\n\n```choice\n'
           '{"step_done":"worldview","question":"是否准备进入下一步？","options":['
           '{"id":"A","title":"直接采纳并锁定世界观",'
           '"detail":"action=\\"adopt\\"：确认此世界观框架"},'
           '{"id":"B","title":"微调",'
           '"detail":"action=\\"discuss\\"：补充等级表后再定稿"}]}\n```')
    _, ch = design.parse_choices(raw)
    issues = design.protocol_issues(raw, ch, step_key="world")
    assert any("action" in x for x in issues)
    assert any("step_done" in x and "world" in x for x in issues)
    assert any("sections" in x for x in issues)


def test_protocol_issues_unparsable_and_claim_without_block():
    broken = '正文\n\n```choice\n{这不是 JSON\n```'
    _, ch = design.parse_choices(broken)
    assert ch is None
    assert "无法解析" in design.protocol_issues(
        broken, ch, step_key="core")[0]
    claim = "本方案已经定稿。是否采纳并进入下一步？"
    issues = design.protocol_issues(claim, None, step_key="core")
    assert issues and "没有输出" in issues[0]


def test_protocol_issues_ignores_plain_choice():
    _, ch = design.parse_choices(_CHOICE_RAW)
    assert design.protocol_issues(_CHOICE_RAW, ch, step_key="core") == []


def test_retry_feedback_contains_key_requirements():
    fb = design.retry_feedback("world", ["缺少 sections 分节计划"])
    assert '"step_done": "world"' in fb
    assert "sections" in fb and "action" in fb
    assert "缺少 sections 分节计划" in fb


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


# ==================== 步收尾采纳（adopt） ====================

async def test_apply_step_adoption_core_fields(db_project):
    """故事内核：按 adopt_targets 逐字段提炼写入（DI 注入，零网络）。"""
    async def fake_call(model, messages):
        return "提炼结果"

    res = await design.apply_step_adoption(
        db_project, "core", "讨论正文", model="m", call_fn=fake_call)
    assert res["kind"] == "fields"
    assert {x["target"] for x in res["written"]} == \
        {"premise", "theme", "synopsis", "genre"}
    assert res["skipped"] == []
    proj = await db.get_project()
    assert proj["premise"] == "提炼结果" and proj["theme"] == "提炼结果"


async def test_apply_step_adoption_core_skips_empty(db_project):
    """提炼为空的字段被跳过（记入 skipped），绝不写空值。"""
    async def fake_call(model, messages):
        joined = "\n".join(m["content"] for m in messages)
        return "" if "故事梗概" in joined else "有内容"

    res = await design.apply_step_adoption(
        db_project, "core", "讨论正文", model="m", call_fn=fake_call)
    assert [x["target"] for x in res["skipped"]] == ["synopsis"]
    proj = await db.get_project()
    assert proj["premise"] == "有内容" and not proj["synopsis"]


async def test_apply_step_adoption_world_sections(db_project):
    """世界观：已有分节覆盖写入，新分节自动创建，空内容跳过。"""
    sections = [{"label": "力量 / 科技体系", "content": "九阶灵力"},
                {"label": "星轨占卜", "content": "星轨决定命运"},
                {"label": "空节", "content": ""}]
    res = await design.apply_step_adoption(db_project, "world", "正文",
                                           sections=sections)
    actions = {x["label"]: x["action"] for x in res["written"]}
    assert actions["力量 / 科技体系"] == "updated"
    assert actions["星轨占卜"] == "created"
    assert any(x["label"] == "空节" for x in res["skipped"])
    secs = await design.load_sections(db_project)
    power = next(s for s in secs if s["section_key"] == "power")
    assert power["content"] == "九阶灵力"            # 覆盖写入
    assert any(s["label"] == "星轨占卜" for s in secs)
    text = file_manager.read_settings_md(db_project)
    assert "九阶灵力" in text and "星轨占卜" in text


async def test_apply_step_adoption_world_fallback(db_project):
    """未给出分节计划时的兜底：提炼后追加进目标分节。"""
    async def fake_call(model, messages):
        return "提炼后的世界观"

    res = await design.apply_step_adoption(
        db_project, "world", "讨论正文", model="m",
        section_key="rules", call_fn=fake_call)
    assert res["written"][0]["section_key"] == "rules"
    assert res["written"][0].get("fallback")
    secs = await design.load_sections(db_project)
    rules = next(s for s in secs if s["section_key"] == "rules")
    assert "提炼后的世界观" in rules["content"]


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


async def test_reload_sloppy_choice_card_actionable(db_project):
    """历史里的乱写收尾卡：仍能识别 step/action，未完成时不被锁定。"""
    raw = ('世界观方案\n\n```choice\n'
           '{"step_done":"worldview","question":"是否进入下一步？","options":['
           '{"id":"A","title":"直接采纳并锁定世界观",'
           '"detail":"action=\\"adopt\\"：确认框架"},'
           '{"id":"B","title":"继续调整",'
           '"detail":"action=\\"discuss\\"：再改改"}]}\n```')
    await db.insert_design_message("assistant", raw, mode="guide", step="world")
    from ui.views.design import DesignView
    view = DesignView(_StubApp(db_project))
    await view.load()
    assert view.chat._step_done_locked(raw, "world") is False   # 未完成可点
    _, ch = design.parse_choices(raw)
    assert ch["options"][0]["action"] == "adopt"


async def test_chat_thought_block_streams_then_collapses(db_project):
    """思考块：思考流式期默认展开，正文出现后自动折叠、仍可点开回看。"""
    from ui.views.design import DesignView
    chat = DesignView(_StubApp(db_project)).chat
    _, bubble = chat._add_msg("ai", "正在思考…")
    chat._attach_thought_block(bubble)
    assert bubble.content.controls[1] is chat._thought_head
    chat._on_thought("我在梳理动机链…")
    assert chat._thought_head.visible and chat._thought_open
    assert chat._thought_text.value == "我在梳理动机链…"
    chat._on_chunk("正式回答开始")
    assert not chat._thought_open and chat._thought_head.visible
    chat._toggle_thought()
    assert chat._thought_open          # 作者可手动展开回看


async def test_guide_select_step_persists(db_project):
    """点步骤条切换 = 真落库（不再是只在内存里改的假切换）。"""
    from ui.views.design import DesignView
    view = DesignView(_StubApp(db_project))
    await view.load()
    await view.guide_on_select_step("cast")
    assert (await design.load_progress())["current_step"] == "cast"


async def test_chat_adopt_step_world_end_to_end(db_project):
    """协作台「直接采纳」：分节写入 + 进度推进 + 回执卡。"""
    from ui.views.design import DesignView
    view = DesignView(_StubApp(db_project))
    await view.load()
    await view.guide_on_select_step("world")
    before = len(view.chat.chat_view.controls)
    await view.chat._adopt_step(
        "world", "世界观完整方案",
        [{"label": "星轨占卜", "content": "星轨决定命运"}])
    prog = await design.load_progress()
    assert "world" in prog["steps_done"]
    assert prog["current_step"] == "structure"
    assert any(s["label"] == "星轨占卜"
               for s in await design.load_sections(db_project))
    # 回执卡替换了空状态欢迎卡（对话流里有反馈）
    assert view.chat._welcome not in view.chat.chat_view.controls
    assert len(view.chat.chat_view.controls) >= max(1, before)
    assert any("已采纳" in msg for msg in view.app.logs)


async def test_chat_auto_retries_sloppy_finish(db_project, monkeypatch):
    """收尾块不规范 → 自动打回重试一次；只落库修正后的规范版本。"""
    from ui.components import design_chat as dc
    from ui.views.design import DesignView

    bad = ('内核方案……\n\n```choice\n'
           '{"question":"是否采纳？","options":['
           '{"id":"A","title":"采纳","detail":"action=\\"adopt\\"：进入下一步"},'
           '{"id":"B","title":"调整","detail":"action=\\"discuss\\"：再改"}]}\n```')
    good = ('内核方案……\n\n```choice\n'
            '{"step_done":"core","question":"是否采纳？","options":['
            '{"id":"A","action":"adopt","title":"采纳"},'
            '{"id":"B","action":"discuss","title":"调整"}]}\n```')
    calls: list[list[dict]] = []
    tools_seen: list = []

    class _Stats:
        def __init__(self, text):
            self.text = text

    async def fake_stream(model, messages, **kwargs):
        calls.append(messages)
        tools_seen.append(kwargs.get("tools"))
        return _Stats(bad if len(calls) == 1 else good)

    monkeypatch.setattr(dc.ai_service, "call_llm_stream", fake_stream)
    view = DesignView(_StubApp(db_project))
    await view.load()
    view.chat.set_mode("guide")
    view.chat.set_guide_step("core")
    await view.chat.send(preset="开始")
    pending = list(view.chat._pending_tools)    # 本轮的工具决策（重试须复用）
    task = view.chat._chat_task
    assert task is not None
    await task                                  # 首轮 + 打回重试都跑完
    assert len(calls) == 2                      # 发生了打回
    assert tools_seen == [pending, pending]     # 提示与挂载保持一致
    assert view.chat._pending_tools == []       # 会话结束即清空
    assert "格式打回" in calls[1][-1]["content"]  # 重试消息带纠错反馈
    hist = await design.load_history()
    assert hist[-1]["role"] == "assistant"
    assert '"step_done":"core"' in hist[-1]["content"]    # 只落库规范版本
    assert all("格式打回" not in (m["content"] or "")
               for m in hist)                   # 打回消息不入历史
    assert any("重新输出" in x for x in view.app.logs)


def test_participation_levels_complete():
    from core.design import PARTICIPATION_LEVELS, participation_directive
    assert set(PARTICIPATION_LEVELS) == {"high", "medium", "low"}
    for v in PARTICIPATION_LEVELS.values():
        assert v.get("label") and v.get("directive")
    assert "作者参与程度：高" in participation_directive("high")
    assert participation_directive("unknown") == \
        participation_directive("high")     # 非法档位回退默认


# ==================== JSON 宽容修复（loads_json） ====================

def test_loads_json_tolerant_repairs():
    from core.design import choices as ch
    assert ch.loads_json('{"a":1,}') == {"a": 1}              # 尾逗号
    assert ch.loads_json('{"a":"第一行\n第二行"}') == \
        {"a": "第一行\n第二行"}                                # 字符串内裸换行
    assert ch.loads_json('{“a”:“价值”}') == {"a": "价值"}      # 中文引号边界
    # 中文引号在合法 JSON 内是内容：plain 解析优先，内容不被破坏
    assert ch.loads_json('{"a":"他说“你好”"}') == {"a": "他说“你好”"}
    assert ch.loads_json("{'a':1}") == {"a": 1}    # 单引号边界（最后手段）
    assert ch.loads_json("不是 JSON") is None


def test_parse_choices_sloppy_json_still_parses():
    """宽容修复作用于 choice 块：中文引号 / 尾逗号也能出卡。"""
    raw = ('正文\n\n```choice\n'
           '{“question”:“选哪个？”,“options”:[{“id”:“A”,“title”:“甲”},'
           '{“id”:“B”,“title”:“乙”,}]}\n```')
    clean, ch = design.parse_choices(raw)
    assert clean == "正文"
    assert ch and ch["options"][0]["title"] == "甲"


# ==================== 收尾检查尾注（尾部锚定） ====================

def test_assemble_messages_appends_tail():
    msgs = design.context.assemble_messages(
        [], "诉求", "快照", system_text="SYS", tail="【尾注】")
    assert msgs[-1]["content"].endswith("【尾注】")
    msgs = design.context.assemble_messages(
        [], "诉求", "快照", system_text="SYS")
    assert msgs[-1]["content"].endswith("诉求")


async def test_finish_tail_note_anchored_to_last_message(db_project):
    """收尾自查尾注钉在最后一条 user 消息末尾（recency），仅 core/world 注入。"""
    msgs = await design.build_chat_messages(db_project, "推进", mode="guide",
                                            step="core")
    last = msgs[-1]["content"]
    assert "收尾自查" in last
    assert last.index("收尾自查") > last.index("作者本轮诉求")   # 尾部锚定
    msgs = await design.build_chat_messages(db_project, "继续", mode="guide",
                                            step="world")
    assert "sections" in msgs[-1]["content"]
    msgs = await design.build_chat_messages(db_project, "继续", mode="guide",
                                            step="cast")
    assert "收尾自查" not in msgs[-1]["content"]
    msgs = await design.build_chat_messages(db_project, "随便聊聊")
    assert "收尾自查" not in msgs[-1]["content"]


async def test_build_chat_messages_tool_hint(db_project):
    """挂载收尾工具时 System 追加工具说明；未挂载不出现。"""
    msgs = await design.build_chat_messages(db_project, "开始",
                                            finish_tool=True)
    assert "submit_step_finish" in msgs[0]["content"]
    msgs = await design.build_chat_messages(db_project, "开始")
    assert "submit_step_finish" not in msgs[0]["content"]


# ==================== 收尾工具（Function Calling）归一 ====================

def test_tool_args_to_choices_normalizes():
    args = {
        "step_done": "world",
        "options": [
            {"action": "adopt", "title": "直接采纳，进入下一步",
             "detail": "写入设定库并推进引导"},
            {"action": "discuss", "title": "还要调整"},
        ],
        "sections": [{"label": "力量体系", "content": "九阶灵力"},
                     {"label": "势力格局", "content": "三足鼎立"}],
    }
    ch = design.tool_args_to_choices(args)
    assert ch["step_done"] == "world"
    assert ch["options"][0]["action"] == "adopt"
    assert ch["sections"][0] == {"label": "力量体系", "content": "九阶灵力"}
    assert "采纳" in ch["question"]          # question 缺省合成


def test_tool_args_to_choices_synthesizes_default_options():
    """选项缺失 / 全部无 action：合成标准「采纳 / 调整」两项。"""
    ch = design.tool_args_to_choices({"step_done": "core"})
    assert [o["action"] for o in ch["options"]] == ["adopt", "discuss"]
    ch = design.tool_args_to_choices(
        {"step_done": "core", "options": [{"title": "就这样"}]})
    assert [o["action"] for o in ch["options"]] == ["adopt", "discuss"]


def test_tool_args_to_choices_single_option_still_parsable():
    """只给 1 个有效选项：必须补齐到 2 项，否则整块被 parse_choices 丢弃。"""
    ch = design.tool_args_to_choices(
        {"step_done": "world",
         "options": [{"action": "adopt", "title": "直接采纳"}],
         "sections": [{"label": "力量体系", "content": "九阶灵力"}]})
    assert len(ch["options"]) >= 2
    assert {o["action"] for o in ch["options"]} == {"adopt", "discuss"}
    merged = f"方案正文\n\n{design.choice_block(ch)}"
    clean, parsed = design.parse_choices(merged)
    assert clean == "方案正文" and parsed is not None
    assert parsed["step_done"] == "world"
    assert parsed["sections"][0]["label"] == "力量体系"
    assert design.protocol_issues(merged, parsed, step_key="world") == []


def test_tool_args_to_choices_sniffs_action_text():
    """action 没写成字段但出现在文字里：文本嗅探兜底。"""
    ch = design.tool_args_to_choices(
        {"step_done": "core",
         "options": [{"title": "采纳此方案", "detail": "action=adopt：确认"},
                     {"title": "再改改", "detail": "action=discuss：补充"}]})
    assert [o["action"] for o in ch["options"]] == ["adopt", "discuss"]


def test_tool_args_to_choices_step_key_fallback():
    """step_done 乱写时按调用方提供的当前步骤兜底。"""
    ch = design.tool_args_to_choices(
        {"step_done": "胡写的",
         "options": [{"action": "adopt", "title": "a"},
                     {"action": "discuss", "title": "b"}]},
        step_key="core")
    assert ch["step_done"] == "core"


def test_tool_args_to_choices_rejects_invalid():
    assert design.tool_args_to_choices(None) is None
    assert design.tool_args_to_choices("x") is None
    assert design.tool_args_to_choices({}) is None
    assert design.tool_args_to_choices({"step_done": "毫无关系"}) is None


def test_choice_block_roundtrip():
    block = design.choice_block({
        "question": "q", "step_done": "core",
        "options": [{"id": "adopt", "action": "adopt", "title": "采纳"},
                    {"id": "tweak", "action": "discuss", "title": "调整"}]})
    text, ch = design.parse_choices(f"正文\n\n{block}\n")
    assert text == "正文"
    assert ch["step_done"] == "core" and ch["options"][0]["action"] == "adopt"


def test_remove_choice_blocks():
    text = design.remove_choice_blocks(
        "正文A\n\n```choice\n{\"a\":1}```\n\n正文B")
    assert "正文A" in text and "正文B" in text and "choice" not in text


async def test_chat_merges_tool_call_finish(db_project):
    """工具收尾 → choice 块归一：残缺文本块被丢弃，审计通过。"""
    from ui.views.design import DesignView
    view = DesignView(_StubApp(db_project))
    await view.load()
    chat = view.chat
    chat.set_mode("guide")
    chat.set_guide_step("world")
    tool_calls = ({"id": "c1", "name": design.FINISH_TOOL_NAME,
                   "arguments": '{"step_done":"world","options":['
                                '{"action":"adopt","title":"直接采纳"},'
                                '{"action":"discuss","title":"调整"}],'
                                '"sections":[{"label":"力量体系",'
                                '"content":"九阶灵力"}]}'},)
    merged = chat._merge_tool_finish('方案正文\n\n```choice\n{坏掉的 JSON',
                                     tool_calls)
    assert "方案正文" in merged and "坏掉的 JSON" not in merged
    clean, ch = design.parse_choices(merged)
    assert clean == "方案正文"
    assert ch["step_done"] == "world"
    assert ch["sections"][0]["label"] == "力量体系"
    assert design.protocol_issues(merged, ch, step_key="world") == []
    assert any("Function Calling" in x for x in view.app.logs)
    # 无工具调用 / 自由对话：原样返回
    assert chat._merge_tool_finish("普通回复", ()) == "普通回复"
    chat.set_mode("free")
    assert chat._merge_tool_finish("自由对话", tool_calls) == "自由对话"


async def test_call_llm_stream_tools_fallback_and_aggregation(
        db_project, monkeypatch):
    """流式工具调用：增量聚合 + 后端拒绝时自动剥离重试并拉黑记忆。"""
    from core import ai_service
    ai_service._tools_unsupported.clear()

    class _Delta:
        def __init__(self, content=None, tool_calls=None):
            self.content = content
            self.reasoning_content = None
            self.tool_calls = tool_calls

    class _Chunk:
        def __init__(self, delta):
            self.usage = None
            self.choices = [types.SimpleNamespace(delta=delta)]

    calls: list[dict] = []

    class _Completions:
        @staticmethod
        async def create(**kwargs):
            calls.append(kwargs)
            if kwargs.get("tools"):
                raise RuntimeError("'tools' is not supported by this model")

            async def gen():
                yield _Chunk(_Delta(content="正文"))
                yield _Chunk(_Delta(tool_calls=[types.SimpleNamespace(
                    index=0, id="c1", function=types.SimpleNamespace(
                        name="submit_step_finish",
                        arguments='{"step_done":"core",'))]))
                yield _Chunk(_Delta(tool_calls=[types.SimpleNamespace(
                    index=0, id=None, function=types.SimpleNamespace(
                        name=None, arguments='"options":[]}'))]))
            return gen()

    stub = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=_Completions()))
    monkeypatch.setattr(ai_service, "_get_client", lambda: stub)

    chunks: list[str] = []
    stats = await ai_service.call_llm_stream(
        "m1", [{"role": "user", "content": "hi"}],
        on_chunk=chunks.append, purpose="design",
        tools=[{"type": "function", "function": {"name": "x"}}])
    assert "".join(chunks) == "正文" and stats.text == "正文"
    assert len(stats.tool_calls) == 1
    assert stats.tool_calls[0]["name"] == "submit_step_finish"
    assert stats.tool_calls[0]["arguments"] == \
        '{"step_done":"core","options":[]}'
    assert len(calls) == 2                       # 首次带工具被拒 → 剥离重试
    assert "tools" not in calls[1]
    assert not ai_service.tools_supported("m1")  # 拉黑记忆生效
    ai_service._tools_unsupported.clear()


def test_tools_err_re_only_matches_tool_errors():
    """工具报错识别必须精准：普通错误不得拉黑模型（拉黑后本进程不可恢复）。"""
    from core import ai_service
    for msg in ("Connection reset by peer",
                "Internal server error",
                "Request timed out.",
                "Expecting value: line 1 column 1 (char 0)",
                "model 'qwen' failed to load"):
        assert not ai_service._TOOLS_ERR_RE.search(msg), msg
    for msg in ("'tools' is not supported by this model",
                "Invalid 'tools[0].function': unexpected field",
                "unknown field 'tools'",
                "function calling is unsupported by this backend",
                "does not support tools"):
        assert ai_service._TOOLS_ERR_RE.search(msg), msg
