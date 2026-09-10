"""设计界面（方案 6.2 写作工作台的姊妹视图）：与 AI 协作设计小说框架。

三大板块 + 常驻 AI 协作台：
- 📖 故事大纲：类型 / 一句话前提 / 梗概 / 篇幅 / 文风 / 全局指导（project_core）
- 🌍 世界观：settings.md 全文编辑（生成时作为 Tier 1 全局设定注入）
- 👥 人物：角色卡增删改（characters 表，生成时作为 Tier 2 半静态注入）
- 🤖 AI 协作台：把当前设定作为上下文与 AI 头脑风暴；产出经作者「采纳」才落盘
  （与写作界面同一纪律：AI 绝不擅自修改作者数据）。

底部状态栏切换写作 / 设计；控件常驻，切板块仅 visible 切换（保滚动/输入）。
"""
import asyncio
import json
import time
from typing import Optional

import flet as ft

from core import ai_service, config, db, file_manager
from ui import theme
from ui.components.resizer import VResizer

FLUSH_INTERVAL_S = 0.08          # 流式刷新 80ms 缓冲（与编辑器一致）

_ROLE_TEXT = {"protagonist": "主角", "antagonist": "反派",
              "supporting": "配角"}

DESIGN_SYSTEM = (
    "你是一位资深的小说策划与设定顾问，与作者协作设计作品的整体框架"
    "（故事前提、类型定位、世界观、人物弧光、主线大纲）。\n"
    "工作方式：\n"
    "1. 先理解作者已有设定与本轮诉求，给出具体、可落地、有创造力且自洽的建议；\n"
    "2. 输出结构清晰（可用小标题与要点），聚焦作者当前正在设计的板块，"
    "信息密度高、篇幅克制；\n"
    "3. 绝不与已有设定冲突；如需引入新假设，明确标注「（待定）」供作者取舍；\n"
    "4. 只做设计层的策划文本，不写章节正文，不复述作者原话，不空洞客套。\n"
    "作者会在你的产出中挑选片段「采纳」进设定库，因此请让每段建议都能独立成立。"
)

_QUICK_PROMPTS = {
    "story": [
        "帮我把一句话前提打磨得更抓人，给 3 个方向",
        "基于现有设定，梳理主线三幕结构与关键转折",
        "为这个故事设计一个出人意料但自洽的结局",
    ],
    "world": [
        "扩充世界观的力量/科技体系，并给出代价与限制",
        "梳理主要势力格局与它们的核心矛盾",
        "补充世界的地理、时代与日常生活质感",
    ],
    "cast": [
        "为当前主角设计一个立体的对手（反派）",
        "检查主要角色的动机是否清晰、弧光是否完整",
        "设计一个能推动主线的配角及其秘密",
    ],
}


class DesignView(ft.Container):
    """设计工作台：左板块导航 / 中编辑画布 / 右 AI 协作台。"""

    SECTIONS = [("story", "故事大纲"), ("world", "世界观"),
                ("cast", "人物")]
    SECTION_ICONS = {"story": ft.Icons.MENU_BOOK, "world": ft.Icons.PUBLIC,
                     "cast": ft.Icons.GROUPS}

    def __init__(self, app):
        self.app = app
        self.section = "story"

        # AI 协作台运行时状态
        self._chat_task: Optional[asyncio.Task] = None
        self._cancel_event: Optional[asyncio.Event] = None
        self._stream_acc = ""
        self._stream_ctrl: Optional[ft.Text] = None
        self._last_flush = 0.0

        self._build_story()
        self._build_world()
        self._build_cast()
        self._build_nav()
        self._build_ai_panel()

        self._section_panels = {"story": self.story_panel,
                                "world": self.world_panel,
                                "cast": self.cast_panel}

        center = ft.Container(
            content=ft.Stack(controls=list(self._section_panels.values()),
                             expand=True),
            expand=True,
        )
        # ---- 可拖拽分隔条（拖动调整导航栏 / AI 协作台宽度）----
        self.nav_resizer = VResizer(
            get_width=lambda: self.nav.width,
            set_width=self._set_nav_width,
            direction=1, min_width=150, max_width=340,
            on_drag_end=self._save_layout_widths)
        self.ai_resizer = VResizer(
            get_width=lambda: self.ai_panel.width,
            set_width=self._set_ai_width,
            direction=-1, min_width=280, max_width=640,
            on_drag_end=self._save_layout_widths)
        content = ft.Row(
            controls=[
                self.nav,
                self.nav_resizer,
                center,
                self.ai_resizer,
                self.ai_panel,
            ],
            spacing=0, expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        super().__init__(content=content, expand=True, visible=False)
        self._apply_section("story")

    # ==================== 左栏：板块导航 ====================

    def _build_nav(self) -> None:
        self._nav_btns: dict[str, ft.Container] = {}
        items = []
        for key, label in self.SECTIONS:
            btn = ft.Container(
                content=ft.Row([
                    ft.Icon(self.SECTION_ICONS[key], size=theme.ICON_INLINE),
                    ft.Text(label, size=theme.SIZE_MD),
                ], spacing=theme.SPACE_SM),
                padding=ft.Padding(theme.SPACE_MD, theme.SPACE_SM,
                                   theme.SPACE_MD, theme.SPACE_SM),
                border_radius=theme.RADIUS_SM, ink=True,
                animate_opacity=theme.ANIM_FAST,
                on_click=lambda e, k=key: self.select_section(k),
            )
            self._nav_btns[key] = btn
            items.append(btn)
        self.nav = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("设计", size=theme.SIZE_BRAND,
                            weight=theme.W_BOLD, color=theme.TEXT),
                    ft.Text("与 AI 协作打磨作品框架", size=theme.SIZE_XS,
                            color=theme.TEXT_MUTED),
                    ft.Divider(height=18, color=theme.BORDER_COLOR),
                    *items,
                    ft.Container(expand=True),
                    ft.Text("设定即时注入后续正文生成", size=theme.SIZE_XXS,
                            color=theme.TEXT_FAINT),
                ],
                spacing=theme.SPACE_XS, expand=True,
            ),
            width=int(config.get("ui_design_nav_width", 196)),
            padding=ft.Padding(theme.SPACE_MD, theme.SPACE_LG,
                               theme.SPACE_SM, theme.SPACE_MD),
            bgcolor=theme.PANEL_BG,
        )

    def select_section(self, key: str) -> None:
        self._apply_section(key)
        if self.page:
            self.update()

    def _apply_section(self, key: str) -> None:
        """仅切换板块可见性与导航高亮（构造期 self.page 尚不可用）。"""
        self.section = key
        for k, panel in self._section_panels.items():
            panel.visible = (k == key)
        for k, btn in self._nav_btns.items():
            active = (k == key)
            btn.bgcolor = theme.ACCENT_SOFT if active else None
            icon, label = btn.content.controls
            icon.color = theme.ON_ACCENT_SOFT if active else theme.TEXT_MUTED
            label.color = theme.ON_ACCENT_SOFT if active else theme.TEXT_MUTED
            label.weight = theme.W_SEMIBOLD if active else theme.W_REGULAR
        self._focus_text.value = f"当前板块：{dict(self.SECTIONS).get(key, '')}"
        self._build_quick_prompts()

    # ==================== 中栏：故事大纲 ====================

    def _build_story(self) -> None:
        self.f_genre = ft.TextField(label="类型 / 题材", dense=True,
                                    hint_text="如：东方玄幻 / 科幻悬疑 / 都市言情")
        self.f_premise = ft.TextField(
            label="一句话前提（故事核）", multiline=True, min_lines=2,
            max_lines=3, shift_enter=True,
            hint_text="用一句话讲清：主角 + 目标 + 最大阻碍")
        self.f_synopsis = ft.TextField(
            label="故事梗概", multiline=True, min_lines=6, max_lines=14,
            shift_enter=True,
            hint_text="主线走向、关键转折、结局设想……")
        self.f_total = ft.TextField(label="预计总章数", dense=True, expand=True,
                                    keyboard_type=ft.KeyboardType.NUMBER)
        self.f_wpc = ft.TextField(label="每章字数", dense=True, expand=True,
                                  keyboard_type=ft.KeyboardType.NUMBER)
        self.f_style = ft.TextField(
            label="文风", multiline=True, min_lines=2, max_lines=4,
            shift_enter=True, hint_text="叙事视角、语言质感、节奏偏好……")
        self.f_guidance = ft.TextField(
            label="全局指导 / 禁忌", multiline=True, min_lines=2, max_lines=6,
            shift_enter=True,
            hint_text="始终遵守或绝对回避的内容（注入每一次生成）")
        self.story_hint = ft.Text("", size=theme.SIZE_XS,
                                  color=theme.semantic_color("success"))
        self.story_panel = ft.Container(
            content=ft.Column(
                controls=[
                    theme.section_header(ft.Icons.MENU_BOOK, "故事大纲"),
                    self.f_genre,
                    self.f_premise,
                    self.f_synopsis,
                    ft.Row([self.f_total, self.f_wpc],
                           spacing=theme.SPACE_MD),
                    self.f_style,
                    self.f_guidance,
                    ft.Row([
                        ft.FilledButton("保存故事设定", icon=ft.Icons.SAVE,
                                        on_click=self.save_story),
                        self.story_hint,
                    ], spacing=theme.SPACE_MD),
                ],
                spacing=theme.SPACE_MD, scroll=ft.ScrollMode.AUTO, expand=True,
            ),
            expand=True, padding=ft.Padding(theme.SPACE_XS, theme.SPACE_MD,
                                            theme.SPACE_LG, theme.SPACE_MD),
        )

    async def save_story(self, e=None) -> None:
        fields = {
            "genre": (self.f_genre.value or "").strip(),
            "premise": (self.f_premise.value or "").strip(),
            "synopsis": (self.f_synopsis.value or "").strip(),
            "writing_style": (self.f_style.value or "").strip(),
            "global_guidance": (self.f_guidance.value or "").strip(),
        }
        try:
            fields["total_chapters"] = max(
                1, int((self.f_total.value or "100").strip() or "100"))
        except ValueError:
            pass
        try:
            fields["words_per_chapter"] = max(
                100, int((self.f_wpc.value or "3000").strip() or "3000"))
        except ValueError:
            pass
        await db.update_project(**fields)
        self._flash(self.story_hint, "✓ 故事设定已保存")
        self.app.append_log("✓ 故事设定已保存（大纲 / 文风 / 全局指导）")

    # ==================== 中栏：世界观 ====================

    def _build_world(self) -> None:
        self.f_world = ft.TextField(
            label="世界观与角色卡（settings.md）", multiline=True,
            min_lines=18, expand=True, shift_enter=True,
            text_size=theme.SIZE_MD,
            hint_text="世界底层法则、力量 / 科技体系、势力格局、地理与时代背景……",
        )
        self.world_hint = ft.Text("", size=theme.SIZE_XS,
                                  color=theme.semantic_color("success"))
        self.world_panel = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row([
                        theme.section_header(ft.Icons.PUBLIC, "世界观设定"),
                        ft.Container(expand=True),
                        self.world_hint,
                        ft.FilledButton("保存世界观", icon=ft.Icons.SAVE,
                                        on_click=self.save_world),
                    ], spacing=theme.SPACE_MD),
                    ft.Text("本内容即 settings.md，作为 Tier 1 全局设定注入"
                            "每一次 AI 生成；可用 Markdown 自由组织。",
                            size=theme.SIZE_XS, color=theme.TEXT_MUTED),
                    self.f_world,
                ],
                spacing=theme.SPACE_MD, expand=True,
            ),
            expand=True, padding=ft.Padding(theme.SPACE_XS, theme.SPACE_MD,
                                            theme.SPACE_LG, theme.SPACE_MD),
        )

    async def save_world(self, e=None) -> None:
        await asyncio.to_thread(file_manager.write_settings_md,
                                self.app.project, self.f_world.value or "")
        self._flash(self.world_hint, "✓ 已保存到 settings.md")
        self.app.append_log("✓ 世界观设定已保存（settings.md，Tier 1 注入源）")

    # ==================== 中栏：人物 ====================

    def _build_cast(self) -> None:
        self.cast_list = ft.ListView(expand=True, spacing=theme.SPACE_SM,
                                     padding=ft.Padding(0, theme.SPACE_XS,
                                                        theme.SPACE_XS,
                                                        theme.SPACE_XS))
        new_btn = ft.FilledButton("新建角色", icon=ft.Icons.PERSON_ADD_ALT,
                                  on_click=lambda e:
                                  self.show_character_dialog(None))
        self.cast_panel = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row([
                        theme.section_header(ft.Icons.GROUPS, "人物角色"),
                        ft.Container(expand=True),
                        new_btn,
                    ], spacing=theme.SPACE_SM),
                    ft.Text("角色卡作为 Tier 2 半静态设定注入生成；定稿管线也会"
                            "自动登记新登场角色。", size=theme.SIZE_XS,
                            color=theme.TEXT_MUTED),
                    self.cast_list,
                ],
                spacing=theme.SPACE_MD, expand=True,
            ),
            expand=True, padding=ft.Padding(theme.SPACE_XS, theme.SPACE_MD,
                                            theme.SPACE_LG, theme.SPACE_MD),
        )

    async def refresh_cast(self) -> None:
        chars = await db.list_characters()
        if not chars:
            self.cast_list.controls = [theme.empty_state(
                ft.Icons.PERSON_ADD_ALT, "还没有角色卡",
                "点右上角「新建角色」，或先在 AI 协作台头脑风暴再登记")]
        else:
            self.cast_list.controls = [self._cast_tile(c) for c in chars]
        if self.page:
            self.cast_list.update()

    def _cast_tile(self, c: dict) -> ft.Container:
        role = _ROLE_TEXT.get(c.get("role", ""), "配角")
        desc = (c.get("personality") or c.get("background")
                or "（暂无描述）")
        return theme.tile_card(
            ft.Row([
                ft.Column([
                    ft.Row([
                        ft.Text(c["name"], size=theme.SIZE_MD,
                                weight=theme.W_SEMIBOLD, color=theme.TEXT),
                        theme.chip(role),
                    ], spacing=theme.SPACE_SM),
                    ft.Text(desc, size=theme.SIZE_XS, color=theme.TEXT_MUTED,
                            max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                ], spacing=theme.SPACE_XXS, expand=True, tight=True),
                ft.IconButton(icon=ft.Icons.EDIT, icon_size=theme.ICON_INLINE,
                              icon_color=theme.TEXT_MUTED,
                              tooltip="编辑角色卡",
                              on_click=lambda e, n=c["name"]:
                              asyncio.create_task(self.edit_character(n))),
                ft.IconButton(icon=ft.Icons.DELETE_OUTLINE,
                              icon_size=theme.ICON_INLINE,
                              icon_color=theme.semantic_color("danger"),
                              tooltip="删除角色",
                              on_click=lambda e, n=c["name"]:
                              self.confirm_delete_character(n)),
            ], spacing=theme.SPACE_XS),
            padding=theme.SPACE_MD, radius=theme.RADIUS_SM)

    async def edit_character(self, name: str) -> None:
        char = await db.get_character(name)
        self.show_character_dialog(char)

    def show_character_dialog(self, char: Optional[dict]) -> None:
        editing = char is not None
        char = char or {}

        def _join(key: str) -> str:
            try:
                return "、".join(json.loads(char.get(key) or "[]"))
            except (json.JSONDecodeError, TypeError):
                return ""

        name = ft.TextField(label="姓名（唯一）", value=char.get("name", ""),
                            autofocus=not editing, disabled=editing)
        aliases = ft.TextField(label="别名 / 代称（顿号分隔）",
                               value=_join("aliases"))
        role_dd = ft.Dropdown(
            text="定位", value=char.get("role", "supporting") or "supporting",
            options=[ft.DropdownOption(key="protagonist", text="主角"),
                     ft.DropdownOption(key="antagonist", text="反派"),
                     ft.DropdownOption(key="supporting", text="配角")])
        personality = ft.TextField(label="性格", multiline=True, min_lines=2,
                                   max_lines=4, value=char.get(
                                       "personality", ""))
        background = ft.TextField(label="背景", multiline=True, min_lines=2,
                                  max_lines=4, value=char.get("background", ""))
        abilities = ft.TextField(label="能力 / 特长", multiline=True,
                                 min_lines=2, max_lines=4,
                                 value=char.get("abilities", ""))
        knowledge = ft.TextField(label="已知核心秘密（每行一条）",
                                 multiline=True, min_lines=2, max_lines=5,
                                 value="\n".join(
                                     self._parse_list(char.get(
                                         "cs_knowledge"))))

        async def confirm(ev=None):
            n = (name.value or "").strip()
            if not n:
                name.error_text = "姓名不能为空"
                self.page.update()
                return
            alias_list = [s.strip() for s in
                          (aliases.value or "").replace("，", "、").split("、")
                          if s.strip()]
            know_list = [s.strip() for s in (knowledge.value or "").split("\n")
                         if s.strip()]
            await db.upsert_character(
                n, aliases=json.dumps(alias_list, ensure_ascii=False),
                role=role_dd.value or "supporting",
                personality=(personality.value or "").strip(),
                background=(background.value or "").strip(),
                abilities=(abilities.value or "").strip(),
                cs_knowledge=json.dumps(know_list, ensure_ascii=False))
            self.page.pop_dialog()
            self.app.append_log(f"✓ 已保存角色：{n}")
            await self.refresh_cast()
            if self.app.current:
                await self.app.refresh_side_panels(self.app.current)

        async def cancel(ev=None):
            self.page.pop_dialog()

        self.page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("编辑角色卡" if editing else "新建角色",
                          size=theme.SIZE_LG, weight=theme.W_SEMIBOLD),
            content=ft.Column([name, aliases, role_dd, personality, background,
                               abilities, knowledge],
                              spacing=theme.SPACE_MD, tight=True, width=420,
                              scroll=ft.ScrollMode.AUTO),
            actions=[ft.TextButton("取消", on_click=cancel),
                     ft.FilledButton("保存", on_click=confirm)],
        ))

    def confirm_delete_character(self, name: str) -> None:
        async def confirm(ev=None):
            self.page.pop_dialog()
            await db.delete_character(name)
            self.app.append_log(f"✓ 已删除角色：{name}")
            await self.refresh_cast()

        async def cancel(ev=None):
            self.page.pop_dialog()

        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("删除角色"),
            content=ft.Text(f"确定删除角色「{name}」吗？\n该角色卡将从设定库移除"
                            f"（不影响已写正文）。"),
            actions=[ft.TextButton("取消", on_click=cancel),
                     ft.FilledButton("删除", on_click=confirm,
                                     color="#FFFFFF",
                                     bgcolor=theme.semantic_color("danger"))],
        ))

    @staticmethod
    def _parse_list(raw) -> list:
        try:
            data = json.loads(raw or "[]")
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    # ==================== 右栏：AI 协作台 ====================

    def _build_ai_panel(self) -> None:
        self.focus_label = theme.chip("当前板块：故事大纲", active=True)
        self._focus_text = self.focus_label.content
        self.chat_view = ft.ListView(expand=True, spacing=theme.SPACE_MD,
                                     auto_scroll=True,
                                     padding=ft.Padding(theme.SPACE_XXS,
                                                        theme.SPACE_SM,
                                                        theme.SPACE_SM,
                                                        theme.SPACE_SM))
        self._welcome = theme.empty_state(
            ft.Icons.AUTO_AWESOME, "与 AI 协作设计作品框架",
            "当前板块的设定会作为上下文；产出经「采纳」才写入设定库",
            compact=False)
        self.chat_view.controls.append(self._welcome)
        self.accept_target = ft.Dropdown(
            text="采纳目标", value="synopsis", dense=True, expand=True,
            options=[
                ft.DropdownOption(key="synopsis", text="故事梗概（替换）"),
                ft.DropdownOption(key="premise", text="一句话前提（替换）"),
                ft.DropdownOption(key="world_append", text="世界观（追加）"),
                ft.DropdownOption(key="world_replace", text="世界观（替换）"),
            ])
        self.chat_input = ft.TextField(
            hint_text="描述你的设计诉求…（Enter 发送 / Shift+Enter 换行）",
            multiline=True, min_lines=1, max_lines=5, shift_enter=True,
            dense=True, expand=True,
            on_submit=lambda e: asyncio.create_task(self.send_message()))
        self.send_btn = ft.FilledButton("发送", icon=ft.Icons.SEND,
                                        on_click=lambda e:
                                        asyncio.create_task(self.send_message()))
        self.stop_btn = ft.OutlinedButton("停止", icon=ft.Icons.STOP,
                                          visible=False,
                                          on_click=self.stop_chat)
        self.quick_row = ft.Row(wrap=True, spacing=theme.SPACE_XS,
                                run_spacing=theme.SPACE_XS)
        self.ai_panel = ft.Container(
            content=ft.Column(
                controls=[
                    theme.section_header(ft.Icons.AUTO_AWESOME, "AI 协作台",
                                         accent=True, trailing=self.stop_btn),
                    self.focus_label,
                    self.quick_row,
                    theme.divider(),
                    self.chat_view,
                    theme.divider(),
                    ft.Row([ft.Text("采纳到", size=theme.SIZE_XS,
                                    color=theme.TEXT_MUTED),
                            self.accept_target], spacing=theme.SPACE_SM),
                    ft.Row([self.chat_input, self.send_btn],
                           spacing=theme.SPACE_SM,
                           vertical_alignment=ft.CrossAxisAlignment.END),
                ],
                spacing=theme.SPACE_SM, expand=True,
            ),
            width=int(config.get("ui_design_ai_width", 400)),
            padding=ft.Padding(theme.SPACE_MD, theme.SPACE_MD,
                               theme.SPACE_LG, theme.SPACE_MD),
            bgcolor=theme.PANEL_BG,
        )

    def _build_quick_prompts(self) -> None:
        prompts = _QUICK_PROMPTS.get(self.section, [])
        self.quick_row.controls = [
            theme.chip(p, on_click=lambda e, t=p: self._fill_input(t),
                       tooltip="填入输入框，可再编辑")
            for p in prompts]

    def _fill_input(self, text: str) -> None:
        self.chat_input.value = text
        if self.page:
            self.chat_input.update()
            self.chat_input.focus()

    def _add_msg(self, role: str, text: str = "") -> tuple[ft.Text,
                                                           ft.Container]:
        is_user = role == "user"
        if self._welcome in self.chat_view.controls:
            self.chat_view.controls.remove(self._welcome)
        txt = ft.Text(text, size=theme.SIZE_SM, selectable=True,
                      color=theme.TEXT)
        box = ft.Container(
            content=ft.Column([
                ft.Text("你" if is_user else "AI 顾问", size=theme.SIZE_XXS,
                        weight=theme.W_SEMIBOLD,
                        color=(theme.ON_ACCENT_SOFT if is_user
                               else theme.TEXT_MUTED)),
                txt,
            ], spacing=theme.SPACE_XS, tight=True),
            bgcolor=(theme.ACCENT_SOFT if is_user
                     else ft.Colors.with_opacity(0.05, ft.Colors.ON_SURFACE)),
            border_radius=theme.RADIUS_MD, padding=theme.SPACE_MD,
        )
        self.chat_view.controls.append(box)
        return txt, box

    def _add_actions(self, box: ft.Container, text: str) -> None:
        box.content.controls.append(ft.Row([
            ft.TextButton("采纳", icon=ft.Icons.DOWNLOAD_DONE,
                          on_click=lambda e, t=text: self.accept_text(t)),
            ft.TextButton("复制", icon=ft.Icons.COPY_ALL,
                          on_click=lambda e, t=text: self.copy_text(t)),
        ], spacing=theme.SPACE_XS))

    async def send_message(self, preset: Optional[str] = None) -> None:
        text = (preset if preset is not None
                else (self.chat_input.value or "")).strip()
        if not text:
            return
        if self.app.generating:
            self.app.append_log("⚠️ 正文生成中，请先停止再使用设计协作")
            return
        if self.is_chatting():
            return
        if not self.app.current_model:
            self.app.append_log("⚠️ 未选择模型：请到设置页选择模型")
            if self.page:
                self.page.show_dialog(ft.SnackBar(
                    ft.Text("请先在设置页选择模型")))
            return
        self.chat_input.value = ""
        self._add_msg("user", text)
        ai_txt, ai_box = self._add_msg("ai", "正在思考…")
        self._stream_ctrl = ai_txt
        self._stream_acc = ""
        self._last_flush = 0.0
        self.send_btn.disabled = True
        self.stop_btn.visible = True
        self.app.status_bar.set_state("busy")
        if self.page:
            self.page.update()
        messages = await self._build_messages(text)
        self._cancel_event = asyncio.Event()
        self._chat_task = asyncio.create_task(
            self._run_chat(messages, ai_txt, ai_box))

    async def _run_chat(self, messages: list[dict], ai_txt: ft.Text,
                        ai_box: ft.Container) -> None:
        try:
            stats = await ai_service.call_llm_stream(
                self.app.current_model, messages,
                on_chunk=self._on_chunk, purpose="design",
                temperature=0.8, cancel_event=self._cancel_event)
            final = stats.text or self._stream_acc
            ai_txt.value = final or "（无内容返回）"
            if final:
                self._add_actions(ai_box, final)
            self.app.append_log("✓ AI 设计建议已生成：可「采纳」写入设定库")
        except ai_service.GenerationCancelled as c:
            ai_txt.value = c.partial or "（已停止）"
            if c.partial:
                self._add_actions(ai_box, c.partial)
            self.app.append_log("■ 设计协作已停止（半成品保留）")
        except Exception as ex:
            ai_txt.value = f"✗ 生成失败：{ex}"
            self.app.status_bar.set_state("offline")
            self.app.append_log(f"✗ 设计协作失败：{ex}")
        finally:
            self.send_btn.disabled = False
            self.stop_btn.visible = False
            self._stream_ctrl = None
            self._chat_task = None
            if self.app.status_bar.state_text.value != "离线":
                self.app.status_bar.set_state("ready")
            if self.page:
                self.page.update()

    def _on_chunk(self, piece: str) -> None:
        self._stream_acc += piece
        now = time.time()
        if now - self._last_flush < FLUSH_INTERVAL_S:
            return
        self._last_flush = now
        if self._stream_ctrl is not None:
            self._stream_ctrl.value = self._stream_acc
            if self.page:
                try:
                    self.chat_view.update()
                except Exception:
                    pass

    def stop_chat(self, e=None) -> None:
        if self._cancel_event:
            self._cancel_event.set()

    def is_chatting(self) -> bool:
        return self._chat_task is not None and not self._chat_task.done()

    async def _build_messages(self, author_text: str) -> list[dict]:
        chars = await db.list_characters()
        char_lines = "\n".join(
            f"- {c['name']}（{_ROLE_TEXT.get(c.get('role', ''), '配角')}）："
            f"{(c.get('personality') or c.get('background') or '')[:60]}"
            for c in chars[:20]) or "（暂无角色）"
        genre = (self.f_genre.value or "").strip() or "（未定）"
        premise = (self.f_premise.value or "").strip() or "（未定）"
        synopsis = (self.f_synopsis.value or "").strip() or "（未定）"
        world = (self.f_world.value or "").strip() or "（未定）"
        section_label = dict(self.SECTIONS).get(self.section, "")
        context = (
            f"【作者当前聚焦板块】{section_label}\n"
            f"【类型 / 题材】{genre}\n"
            f"【一句话前提】{premise}\n"
            f"【故事梗概】{synopsis[:900]}\n"
            f"【世界观设定（settings.md）】\n{world[:1600]}\n"
            f"【主要角色】\n{char_lines}\n"
        )
        user = (f"以下是作品当前的设定快照：\n{context}\n"
                f"【作者本轮诉求】\n{author_text}\n\n"
                f"请针对「{section_label}」给出具体、可落地的设计建议。")
        return [{"role": "system", "content": DESIGN_SYSTEM},
                {"role": "user", "content": user}]

    # ---- 采纳 / 复制 ----

    def accept_text(self, text: str) -> None:
        target = self.accept_target.value or "synopsis"
        if target == "synopsis":
            self.f_synopsis.value = text.strip()
            asyncio.create_task(self._persist_story(
                {"synopsis": text.strip()}, "故事梗概"))
        elif target == "premise":
            self.f_premise.value = text.strip()
            asyncio.create_task(self._persist_story(
                {"premise": text.strip()}, "一句话前提"))
        elif target == "world_append":
            cur = (self.f_world.value or "").rstrip()
            merged = f"{cur}\n\n{text.strip()}\n" if cur else f"{text.strip()}\n"
            self.f_world.value = merged
            asyncio.create_task(self._persist_world("世界观（追加）"))
        elif target == "world_replace":
            self.f_world.value = text.strip() + "\n"
            asyncio.create_task(self._persist_world("世界观（替换）"))
        self._update_fields()

    async def _persist_story(self, fields: dict, label: str) -> None:
        await db.update_project(**fields)
        self.app.append_log(f"✓ 已采纳 AI 建议到「{label}」并保存")

    async def _persist_world(self, label: str) -> None:
        await asyncio.to_thread(file_manager.write_settings_md,
                                self.app.project, self.f_world.value or "")
        self.app.append_log(f"✓ 已采纳 AI 建议到「{label}」并写入 settings.md")

    def copy_text(self, text: str) -> None:
        if self.page:
            self.page.set_clipboard(text)
            self.app.append_log("✓ 已复制 AI 建议到剪贴板")

    def _update_fields(self) -> None:
        if self.page:
            for f in (self.f_synopsis, self.f_premise, self.f_world):
                f.update()

    # ==================== 数据装载 ====================

    async def load(self) -> None:
        """进入设计界面 / 切换项目时拉取最新设定填充各字段。"""
        if not self.app.project:
            return
        proj = await db.get_project() or {}
        self.f_genre.value = proj.get("genre", "") or ""
        self.f_premise.value = proj.get("premise", "") or ""
        self.f_synopsis.value = proj.get("synopsis", "") or ""
        self.f_total.value = str(proj.get("total_chapters", 100) or 100)
        self.f_wpc.value = str(proj.get("words_per_chapter", 3000) or 3000)
        self.f_style.value = proj.get("writing_style", "") or ""
        self.f_guidance.value = proj.get("global_guidance", "") or ""
        self.f_world.value = await asyncio.to_thread(
            file_manager.read_settings_md, self.app.project)
        await self.refresh_cast()
        if self.page:
            self.update()

    async def save_current(self) -> None:
        """设计界面 Ctrl+S：保存当前板块（人物走对话框即时保存）。"""
        if self.section == "story":
            await self.save_story()
        elif self.section == "world":
            await self.save_world()
        else:
            self.app.append_log("ℹ️ 人物请在角色卡对话框内保存")

    # ==================== 小工具 ====================

    def _set_nav_width(self, w: float) -> None:
        self.nav.width = w
        if self.page:
            try:
                self.nav.update()
            except Exception:
                pass

    def _set_ai_width(self, w: float) -> None:
        self.ai_panel.width = w
        if self.page:
            try:
                self.ai_panel.update()
            except Exception:
                pass

    def _save_layout_widths(self) -> None:
        """拖拽结束后把设计界面分栏宽度写回 config.json（跨会话记忆）。"""
        config.set_key("ui_design_nav_width", int(self.nav.width or 0))
        config.set_key("ui_design_ai_width", int(self.ai_panel.width or 0))
        config.save_config()

    def _flash(self, ctrl: ft.Text, msg: str) -> None:
        ctrl.value = msg
        ctrl.color = theme.semantic_color("success")
        if self.page:
            ctrl.update()
