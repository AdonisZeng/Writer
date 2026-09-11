"""设计界面：与 AI 协作，从一点想法逐步打磨成完整作品框架。

五板块 + 常驻 AI 协作台：
- 🧭 起步引导：一句话想法 → 内核/世界观/结构/人物四步推进（进度可续做，
  「作者参与程度」三档调节 AI 提问频率）
- 📖 故事内核：类型/前提/主题立意/梗概/篇幅/文风/全局指导（project_core）
- 👥 人物：角色卡增删改（characters 表），AI 建议可预填对话框后确认入库
- 🌍 世界观：结构化分节编辑（DB 为编辑源）→ 投影 settings.md（生成时 Tier 1 注入）
- 🗂 结构大纲：AI 生成分卷/章节 → 批量写入 chapters（与写作界面打通）

纪律：AI 产出先预览、作者确认后才落库；控件常驻，仅 visible 切换（保滚动/输入）。
"""
import asyncio
import json
import uuid
from typing import Optional

import flet as ft

from core import config, db, design
from core.commands import apply_outline as apply_outline_cmd
from ui import theme
from ui.components.design_chat import DesignChat
from ui.components.design_guide import DesignGuide
from ui.components.resizer import VResizer

_ROLE_TEXT = {"protagonist": "主角", "antagonist": "反派",
              "supporting": "配角"}
_ADOPT_LABELS = {"premise": "一句话前提", "theme": "主题立意",
                 "synopsis": "故事梗概", "genre": "类型 / 题材"}


class DesignView(ft.Container):
    """设计工作台：左板块导航 / 中编辑画布 / 右 AI 协作台。"""

    SECTIONS = [("guide", "起步引导"), ("story", "故事内核"),
                ("world", "世界观"), ("outline", "结构大纲"),
                ("cast", "人物")]
    SECTION_ICONS = {"guide": ft.Icons.LIGHTBULB, "story": ft.Icons.MENU_BOOK,
                     "cast": ft.Icons.GROUPS, "world": ft.Icons.PUBLIC,
                     "outline": ft.Icons.ACCOUNT_TREE}

    def __init__(self, app):
        self.app = app
        self.section = "guide"
        self.guide_progress = {"idea": "", "current_step": "core",
                               "steps_done": []}
        self.world_sections: list[dict] = []
        self._world_fields: dict[str, ft.TextField] = {}
        self._outline = {"volumes": []}

        self._build_guide()
        self._build_story()
        self._build_cast()
        self._build_world()
        self._build_outline()
        self._build_nav()
        self._build_ai_panel()

        self._section_panels = {"guide": self.guide_panel,
                                "story": self.story_panel,
                                "cast": self.cast_panel,
                                "world": self.world_panel,
                                "outline": self.outline_panel}
        center = ft.Container(
            content=ft.Stack(controls=list(self._section_panels.values()),
                             expand=True),
            expand=True)

        self.nav_resizer = VResizer(
            get_width=lambda: self.nav.width,
            set_width=self._set_nav_width,
            direction=1, min_width=150, max_width=340,
            on_drag_end=self._save_layout_widths)
        self.ai_resizer = VResizer(
            get_width=lambda: self.ai_panel.width,
            set_width=self._set_ai_width,
            direction=-1, min_width=300, max_width=680,
            on_drag_end=self._save_layout_widths)
        content = ft.Row(
            controls=[self.nav, self.nav_resizer, center,
                      self.ai_resizer, self.ai_panel],
            spacing=0, expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH)
        super().__init__(content=content, expand=True, visible=False)
        self._apply_section("guide")

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
                    ft.Text("设定会注入后续正文生成", size=theme.SIZE_XXS,
                            color=theme.TEXT_FAINT),
                ],
                spacing=theme.SPACE_XS, expand=True),
            width=int(config.get("ui_design_nav_width", 196)),
            padding=ft.Padding(theme.SPACE_MD, theme.SPACE_LG,
                               theme.SPACE_SM, theme.SPACE_MD),
            bgcolor=theme.PANEL_BG,
        )

    def select_section(self, key: str) -> None:
        self._apply_section(key)
        theme.safe_update(self)

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
        self.chat.set_section(key)

    # ==================== 中栏：起步引导 ====================

    def _build_guide(self) -> None:
        self.guide = DesignGuide(self)
        self.guide_panel = ft.Container(
            content=self.guide,
            left=0, top=0, right=0, bottom=0,
            padding=ft.Padding(theme.SPACE_XS, theme.SPACE_MD,
                               theme.SPACE_LG, theme.SPACE_MD),
        )

    async def guide_on_save_idea(self) -> None:
        idea = self.guide.idea.value or ""
        self.guide_progress = await design.save_progress(
            idea=idea, current_step=self.guide.current_step,
            steps_done=self.guide_progress.get("steps_done") or [])
        self.guide.refresh(self.guide_progress)
        self.guide.flash_saved()
        self.app.append_log("✓ 引导想法已保存")

    async def guide_on_start(self) -> None:
        await self.guide_on_save_idea()
        await self.chat.send_guide(self.guide.current_step)

    async def guide_on_done(self) -> None:
        key = self.guide.current_step
        label = design.step_by_key(key).get("label", key)
        self.guide_progress = await design.mark_done(key)
        self.guide.refresh(self.guide_progress)
        self.chat.set_guide_step(self.guide_progress["current_step"])
        self.app.append_log(f"✓ 已完成「{label}」，进入下一步")

    async def guide_on_select_step(self, key: str) -> None:
        """点击步骤条切换当前步骤：落库（可续做），但不自动标记完成。"""
        if key not in design.STEP_KEYS:
            return
        self.guide_progress = await design.save_progress(
            idea=(self.guide.idea.value or "").strip(),
            current_step=key,
            steps_done=self.guide_progress.get("steps_done") or [])
        self.guide.refresh(self.guide_progress)
        self.chat.set_guide_step(key)

    async def on_step_adopted(self, step_key: str,
                              result: dict) -> tuple[str, bool]:
        """步收尾采纳完成：刷新对应设定 UI + 推进引导进度。

        返回 (最新当前步骤 key, 是否发生了推进)，供协作台渲染回执。
        """
        if result.get("kind") == "fields":
            await self._reload_story_fields()
            theme.safe_update(self.f_premise, self.f_theme, self.f_synopsis,
                              self.f_genre)
        elif result.get("kind") == "sections":
            await self.refresh_world()
        before = self.guide_progress.get("current_step")
        self.guide_progress = await design.mark_done(step_key)
        self.guide.refresh(self.guide_progress)
        nxt = self.guide_progress["current_step"]
        self.chat.set_guide_step(nxt)
        advanced = nxt != before
        label = (design.step_by_key(step_key) or {}).get("label", step_key)
        self.app.append_log(f"✓ 『{label}』方案已采纳" +
                            ("，进入下一步" if advanced else "（设定已更新）"))
        return nxt, advanced

    def guide_on_generate_outline(self) -> None:
        self.select_section("outline")
        asyncio.create_task(self.generate_outline())

    # ==================== 中栏：故事内核 ====================

    def _build_story(self) -> None:
        self.f_genre = ft.TextField(label="类型 / 题材", dense=True,
                                    expand=2,
                                    hint_text="如：东方玄幻 / 科幻悬疑 / 都市言情")
        self.f_premise = ft.TextField(
            label="一句话前提（故事核）", multiline=True, min_lines=2,
            max_lines=3, shift_enter=True,
            hint_text="用一句话讲清：主角 + 目标 + 最大阻碍")
        self.f_theme = ft.TextField(
            label="主题立意", multiline=True, min_lines=1, max_lines=3,
            shift_enter=True, hint_text="作品想探讨的核心命题，如：自由与代价")
        self.f_synopsis = ft.TextField(
            label="故事梗概", multiline=True, min_lines=6, max_lines=16,
            shift_enter=True, hint_text="主线走向、关键转折、结局设想……")
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
                    theme.section_header(ft.Icons.MENU_BOOK, "故事内核"),
                    ft.Text("这些字段会作为 Tier 1 全局设定注入每一次生成。",
                            size=theme.SIZE_XS, color=theme.TEXT_MUTED),
                    ft.Row([self.f_genre, self.f_total, self.f_wpc],
                           spacing=theme.SPACE_MD),
                    self.f_premise,
                    self.f_theme,
                    self.f_synopsis,
                    self.f_style,
                    self.f_guidance,
                    ft.Row([
                        ft.FilledButton("保存故事设定", icon=ft.Icons.SAVE,
                                        on_click=self.save_story),
                        self.story_hint,
                    ], spacing=theme.SPACE_MD),
                ],
                spacing=theme.SPACE_MD, scroll=ft.ScrollMode.AUTO,
                expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
            left=0, top=0, right=0, bottom=0,
            padding=ft.Padding(theme.SPACE_XS, theme.SPACE_MD,
                               theme.SPACE_LG, theme.SPACE_MD),
        )

    async def save_story(self, e=None) -> None:
        fields = {
            "genre": (self.f_genre.value or "").strip(),
            "premise": (self.f_premise.value or "").strip(),
            "theme": (self.f_theme.value or "").strip(),
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
        self.app.append_log("✓ 故事设定已保存（内核 / 文风 / 全局指导）")

    # ==================== 中栏：人物 ====================

    def _build_cast(self) -> None:
        self.cast_list = ft.ListView(expand=True, spacing=theme.SPACE_SM,
                                     padding=ft.Padding(0, theme.SPACE_XS,
                                                        theme.SPACE_XS,
                                                        theme.SPACE_XS))
        new_btn = ft.FilledButton("新建角色", icon=ft.Icons.PERSON_ADD_ALT,
                                  on_click=lambda e:
                                  self.show_character_dialog(None, new=True))
        self.cast_panel = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row([theme.section_header(ft.Icons.GROUPS, "人物角色"),
                            ft.Container(expand=True), new_btn],
                           spacing=theme.SPACE_SM),
                    ft.Text("角色卡作为 Tier 2 半静态设定注入生成；定稿管线也会"
                            "自动登记新登场角色。", size=theme.SIZE_XS,
                            color=theme.TEXT_MUTED),
                    self.cast_list,
                ],
                spacing=theme.SPACE_MD, expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
            left=0, top=0, right=0, bottom=0,
            padding=ft.Padding(theme.SPACE_XS, theme.SPACE_MD,
                               theme.SPACE_LG, theme.SPACE_MD),
        )

    async def refresh_cast(self) -> None:
        if not self.app.project:
            return
        chars = await db.list_characters()
        if not chars:
            self.cast_list.controls = [theme.empty_state(
                ft.Icons.PERSON_ADD_ALT, "还没有角色卡",
                "点右上角「新建角色」，或先在 AI 协作台头脑风暴再采纳")]
        else:
            self.cast_list.controls = [self._cast_tile(c) for c in chars]
        theme.safe_update(self.cast_list)

    def _cast_tile(self, c: dict) -> ft.Container:
        role = _ROLE_TEXT.get(c.get("role", ""), "配角")
        desc = (c.get("personality") or c.get("background") or "（暂无描述）")
        return theme.tile_card(
            ft.Row([
                ft.Column([
                    ft.Row([ft.Text(c["name"], size=theme.SIZE_MD,
                                    weight=theme.W_SEMIBOLD, color=theme.TEXT),
                            theme.chip(role)], spacing=theme.SPACE_SM),
                    ft.Text(desc, size=theme.SIZE_XS, color=theme.TEXT_MUTED,
                            max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                ], spacing=theme.SPACE_XXS, expand=True, tight=True),
                ft.IconButton(icon=ft.Icons.EDIT, icon_size=theme.ICON_INLINE,
                              icon_color=theme.TEXT_MUTED, tooltip="编辑角色卡",
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

    def open_character_from_ai(self, parsed: dict) -> None:
        """采纳 AI 建议：预填角色对话框（作者确认后才入库）。"""
        char = {
            "name": parsed.get("name", ""),
            "role": parsed.get("role", "supporting"),
            "personality": parsed.get("personality", ""),
            "background": parsed.get("background", ""),
            "abilities": parsed.get("abilities", ""),
            "aliases": json.dumps(parsed.get("aliases") or [],
                                  ensure_ascii=False),
            "cs_knowledge": json.dumps(parsed.get("knowledge") or [],
                                       ensure_ascii=False),
        }
        self.show_character_dialog(char, new=True)

    def show_character_dialog(self, char: Optional[dict], *,
                              new: bool = False) -> None:
        editing = char is not None and not new
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
                                   max_lines=4,
                                   value=char.get("personality", ""))
        background = ft.TextField(label="背景", multiline=True, min_lines=2,
                                  max_lines=4, value=char.get("background", ""))
        abilities = ft.TextField(label="能力 / 特长", multiline=True,
                                 min_lines=2, max_lines=4,
                                 value=char.get("abilities", ""))
        knowledge = ft.TextField(
            label="已知核心秘密（每行一条）", multiline=True, min_lines=2,
            max_lines=5,
            value="\n".join(self._parse_list(char.get("cs_knowledge"))))

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

    # ==================== 中栏：结构化世界观 ====================

    def _build_world(self) -> None:
        self.world_list = ft.Column(
            spacing=theme.SPACE_MD,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        self.world_hint = ft.Text("", size=theme.SIZE_XS,
                                  color=theme.semantic_color("success"))
        add_btn = ft.TextButton("添加分节", icon=ft.Icons.ADD,
                                on_click=lambda e: self.show_add_section_dialog())
        self.world_panel = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row([theme.section_header(ft.Icons.PUBLIC, "世界观设定"),
                            ft.Container(expand=True), self.world_hint,
                            add_btn], spacing=theme.SPACE_SM),
                    ft.Text("分节编辑后自动投影为 settings.md，作为 Tier 1 全"
                            "局设定注入每一次 AI 生成。",
                            size=theme.SIZE_XS, color=theme.TEXT_MUTED),
                    self.world_list,
                ],
                spacing=theme.SPACE_MD, scroll=ft.ScrollMode.AUTO,
                expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
            left=0, top=0, right=0, bottom=0,
            padding=ft.Padding(theme.SPACE_XS, theme.SPACE_MD,
                               theme.SPACE_LG, theme.SPACE_MD),
        )

    async def refresh_world(self) -> None:
        if not self.app.project:
            return
        self.world_sections = await design.load_sections(self.app.project)
        self._render_world_sections()
        self.chat.refresh_world_options()

    def _render_world_sections(self) -> None:
        self._world_fields = {}
        controls = []
        for s in self.world_sections:
            key = s["section_key"]
            label = s.get("label") or key
            tf = ft.TextField(value=s.get("content") or "", multiline=True,
                              min_lines=3, max_lines=12, shift_enter=True,
                              hint_text="（待填写：具体的设定要点、代价与限制）")
            self._world_fields[key] = tf
            save_btn = ft.TextButton(
                "保存本节", icon=ft.Icons.SAVE,
                on_click=lambda e, k=key:
                asyncio.create_task(self.save_world_section(k)))
            ai_btn = ft.TextButton(
                "让 AI 补全", icon=ft.Icons.AUTO_AWESOME,
                on_click=lambda e, lab=label: self._ask_ai_world(lab))
            controls.append(theme.tile_card(ft.Column([
                ft.Row([ft.Text(label, size=theme.SIZE_SM,
                                weight=theme.W_SEMIBOLD, color=theme.TEXT),
                        ft.Container(expand=True), ai_btn, save_btn],
                       spacing=theme.SPACE_XS),
                tf,
            ], spacing=theme.SPACE_SM), padding=theme.SPACE_MD))
        self.world_list.controls = controls or [theme.empty_state(
            ft.Icons.PUBLIC, "尚无世界观分节", "点「添加分节」开始搭建世界")]
        theme.safe_update(self.world_list)

    async def save_world_section(self, key: str) -> None:
        tf = self._world_fields.get(key)
        content = (tf.value if tf else "") or ""
        self.world_sections = await design.save_section(
            self.app.project, key, content)
        self.chat.refresh_world_options()
        self._flash(self.world_hint, "✓ 已保存并同步 settings.md")
        self.app.append_log("✓ 世界观已保存（settings.md 已同步）")

    def _ask_ai_world(self, label: str) -> None:
        self.chat.prefill(
            f"请扩充「{label}」部分：给出具体、可落地、有代价与限制的设定要点，"
            f"控制在合理篇幅。", mode="free")

    def show_add_section_dialog(self) -> None:
        label = ft.TextField(label="分节名称", autofocus=True,
                             hint_text="如：历史大事 / 经济体系 / 语言与种族")

        async def confirm(ev=None):
            text = (label.value or "").strip()
            if not text:
                label.error_text = "名称不能为空"
                self.page.update()
                return
            key = "custom_" + uuid.uuid4().hex[:6]
            order = 5.0 + len(self.world_sections) * 0.1
            await db.upsert_world_section(key, label=text, content="",
                                          order_index=order)
            self.page.pop_dialog()
            await self.refresh_world()
            self.app.append_log(f"✓ 已添加世界观分节：{text}")

        async def cancel(ev=None):
            self.page.pop_dialog()

        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("添加世界观分节"),
            content=ft.Column([label], spacing=theme.SPACE_MD, tight=True,
                              width=360),
            actions=[ft.TextButton("取消", on_click=cancel),
                     ft.FilledButton("添加", on_click=confirm)],
        ))

    # ==================== 中栏：结构大纲 ====================

    def _build_outline(self) -> None:
        self.outline_guidance = ft.TextField(
            label="大纲补充要求（可选）", dense=True, multiline=True,
            min_lines=1, max_lines=3,
            hint_text="如：首卷 12 章，节奏偏快；第 3 章必须在朝堂埋下伏笔")
        self.outline_gen_btn = ft.FilledButton(
            "AI 生成结构大纲", icon=ft.Icons.AUTO_AWESOME,
            on_click=lambda e: asyncio.create_task(self.generate_outline()))
        self.outline_mode = ft.Dropdown(
            text="写入方式", value="append", dense=True, width=120,
            options=[ft.DropdownOption(key="append", text="追加到现有章节"),
                     ft.DropdownOption(key="replace", text="替换全部章节")])
        self.outline_apply_btn = ft.OutlinedButton(
            "采纳并写入章节", icon=ft.Icons.DOWNLOAD_DONE, disabled=True,
            on_click=lambda e: self._ask_apply_outline())
        self.outline_hint = ft.Text("", size=theme.SIZE_XS,
                                    color=theme.TEXT_MUTED)
        self.outline_confirm = ft.Row([
            ft.Text("确认写入章节表？", size=theme.SIZE_SM,
                    color=theme.TEXT),
            ft.FilledButton("确认写入",
                            on_click=lambda e:
                            asyncio.create_task(self._do_apply_outline())),
            ft.TextButton("取消", on_click=lambda e:
                          self._set_confirm_visible(False)),
        ], spacing=theme.SPACE_SM, visible=False)
        self.outline_view = ft.Column(
            spacing=theme.SPACE_MD,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        self.outline_panel = ft.Container(
            content=ft.Column(
                controls=[
                    theme.section_header(ft.Icons.ACCOUNT_TREE, "结构大纲"),
                    ft.Text("让 AI 依据当前设定生成分卷与章节细纲，采纳后"
                            "批量写入章节表（等价于在写作界面逐章建章）。",
                            size=theme.SIZE_XS, color=theme.TEXT_MUTED),
                    self.outline_guidance,
                    ft.Row([self.outline_gen_btn, self.outline_hint],
                           spacing=theme.SPACE_MD),
                    theme.divider(),
                    self.outline_view,
                    ft.Row([self.outline_mode, self.outline_apply_btn],
                           spacing=theme.SPACE_SM),
                    self.outline_confirm,
                ],
                spacing=theme.SPACE_MD, scroll=ft.ScrollMode.AUTO,
                expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
            left=0, top=0, right=0, bottom=0,
            padding=ft.Padding(theme.SPACE_XS, theme.SPACE_MD,
                               theme.SPACE_LG, theme.SPACE_MD),
        )

    async def generate_outline(self) -> None:
        app = self.app
        if not app.project:
            return
        if not app.current_model:
            app.append_log("⚠️ 未选择模型：请到设置页选择模型")
            return
        if not await app.check_connection(notify=True):
            return
        self.outline_gen_btn.disabled = True
        self.outline_hint.value = "生成中…（结构化输出，可能稍久）"
        self.outline_hint.color = theme.TEXT_MUTED
        app.status_bar.set_state("busy")
        if self.page:
            self.page.update()
        data = {"volumes": []}
        try:
            data = await design.build_outline(
                app.project, app.current_model,
                guidance=self.outline_guidance.value or "")
        except Exception as ex:
            self.outline_hint.value = f"✗ 生成失败：{ex}"
            self.outline_hint.color = theme.semantic_color("danger")
        finally:
            self.outline_gen_btn.disabled = False
            app.status_bar.set_state("ready")
        self._outline = data
        self._render_outline()
        n = design.count_chapters(data)
        if n:
            self.outline_hint.value = f"✓ 已生成 {n} 章大纲，请预览后采纳"
            self.outline_hint.color = theme.semantic_color("success")
            app.append_log(f"✓ 结构大纲已生成：{n} 章")
        elif "生成失败" not in self.outline_hint.value:
            self.outline_hint.value = "未生成有效大纲（可调整要求后重试）"
            self.outline_hint.color = theme.semantic_color("warning")
        if self.page:
            self.page.update()

    def _render_outline(self) -> None:
        volumes = self._outline.get("volumes") or []
        if not volumes:
            self.outline_view.controls = [theme.empty_state(
                ft.Icons.ACCOUNT_TREE, "尚未生成结构大纲",
                "填写补充要求后点「AI 生成结构大纲」")]
            self.outline_apply_btn.disabled = True
        else:
            controls = []
            no = 0
            for v in volumes:
                rows = [ft.Row([
                    ft.Icon(ft.Icons.MENU_BOOK, size=theme.ICON_INLINE,
                            color=theme.ACCENT),
                    ft.Text(v.get("title") or "未命名卷", size=theme.SIZE_MD,
                            weight=theme.W_SEMIBOLD, color=theme.TEXT),
                    ft.Container(expand=True),
                    theme.metric_text(f"{len(v.get('chapters', []))} 章",
                                      size=theme.SIZE_XXS),
                ], spacing=theme.SPACE_SM)]
                if v.get("summary"):
                    rows.append(ft.Text(v["summary"], size=theme.SIZE_XS,
                                        color=theme.TEXT_MUTED))
                for c in v.get("chapters", []):
                    no += 1
                    meta = " · ".join(x for x in
                                      (c.get("role"), c.get("purpose")) if x)
                    rows.append(theme.tile_card(ft.Column([
                        ft.Text(f"第{no}章 {c.get('title', '')}",
                                size=theme.SIZE_SM, color=theme.TEXT),
                        ft.Text(meta or "（无结构说明）", size=theme.SIZE_XXS,
                                color=theme.TEXT_MUTED),
                        ft.Text(c.get("key_events", ""), size=theme.SIZE_XS,
                                color=theme.TEXT_MUTED, max_lines=2,
                                overflow=ft.TextOverflow.ELLIPSIS)
                        if c.get("key_events") else ft.Container(height=0),
                    ], spacing=theme.SPACE_XXS), padding=theme.SPACE_SM))
                controls.append(ft.Column(rows, spacing=theme.SPACE_XS))
            self.outline_view.controls = controls
            self.outline_apply_btn.disabled = False
        theme.safe_update(self.outline_view, self.outline_apply_btn)

    def _ask_apply_outline(self) -> None:
        self._set_confirm_visible(True)

    def _set_confirm_visible(self, visible: bool) -> None:
        self.outline_confirm.visible = visible
        theme.safe_update(self.outline_confirm)

    async def _do_apply_outline(self) -> None:
        n = design.count_chapters(self._outline)
        if not n:
            return
        replace = (self.outline_mode.value == "replace")
        self._set_confirm_visible(False)
        self.outline_apply_btn.disabled = True
        try:
            result = await apply_outline_cmd.apply_outline(
                self.app.project, self._outline.get("volumes", []),
                replace=replace)
        except Exception as ex:
            self.outline_hint.value = f"✗ 写入失败：{ex}"
            self.outline_hint.color = theme.semantic_color("danger")
            self.outline_apply_btn.disabled = False
            self.app.append_log(f"✗ 大纲写入失败：{ex}")
            if self.page:
                self.page.update()
            return
        self.outline_apply_btn.disabled = False
        extra = f"（已清空旧 {result['removed']} 章）" if result["removed"] else ""
        self.outline_hint.value = f"✓ 已写入 {result['created']} 章{extra}"
        self.outline_hint.color = theme.semantic_color("success")
        self.app.append_log(f"✓ 大纲已写入章节表：{result['created']} 章{extra}，"
                            f"可切换到写作界面开始创作")
        await self.app.reload_chapters()
        if self.page:
            self.page.update()

    # ==================== 右栏：AI 协作台 ====================

    def _build_ai_panel(self) -> None:
        self.chat = DesignChat(self)
        self.ai_panel = self.chat

    # ==================== 采纳落库 ====================

    def current_value(self, target: str, section_key: str = "") -> str:
        """采纳预览用：返回目标字段的当前内容。"""
        fields = {"premise": self.f_premise, "theme": self.f_theme,
                  "synopsis": self.f_synopsis, "genre": self.f_genre}
        if target in fields:
            return fields[target].value or ""
        if target == "world":
            return next((s.get("content") or "" for s in self.world_sections
                         if s["section_key"] == section_key), "")
        return ""

    async def apply_adoption(self, target: str, text: str, *,
                             section_key: str = "",
                             world_mode: str = "append") -> str:
        """把确认后的 AI 产出写入设定库，返回写入目标的中文标签。"""
        text = (text or "").strip()
        if target in _ADOPT_LABELS:
            field = {"premise": self.f_premise, "theme": self.f_theme,
                     "synopsis": self.f_synopsis, "genre": self.f_genre}[target]
            field.value = text
            theme.safe_update(field)
            await db.update_project(**{target: text})
            return _ADOPT_LABELS[target]
        if target == "world":
            cur = self.current_value("world", section_key)
            content = text if world_mode == "replace" else (
                f"{cur.rstrip()}\n\n{text}" if cur.strip() else text)
            self.world_sections = await design.save_section(
                self.app.project, section_key, content)
            tf = self._world_fields.get(section_key)
            if tf is not None:
                tf.value = content
                theme.safe_update(tf)
            self.chat.refresh_world_options()
            name = next((s.get("label") for s in self.world_sections
                         if s["section_key"] == section_key), section_key)
            return f"世界观 · {name}"
        raise ValueError(f"未知采纳目标：{target}")

    # ==================== 数据装载 ====================

    async def _reload_story_fields(self) -> None:
        """从库中刷新「故事内核」字段（进入界面 / 步收尾采纳后调用）。"""
        proj = await db.get_project() or {}
        self.f_genre.value = proj.get("genre", "") or ""
        self.f_premise.value = proj.get("premise", "") or ""
        self.f_theme.value = proj.get("theme", "") or ""
        self.f_synopsis.value = proj.get("synopsis", "") or ""
        self.f_total.value = str(proj.get("total_chapters", 100) or 100)
        self.f_wpc.value = str(proj.get("words_per_chapter", 3000) or 3000)
        self.f_style.value = proj.get("writing_style", "") or ""
        self.f_guidance.value = proj.get("global_guidance", "") or ""

    async def load(self) -> None:
        """进入设计界面 / 切换项目时拉取最新设定并重载（隔离）对话。"""
        if not self.app.project:
            return
        await self._reload_story_fields()
        await self.refresh_cast()
        await self.refresh_world()
        self.guide_progress = await design.load_progress()
        self.guide.refresh(self.guide_progress)
        self.chat.set_guide_step(self.guide_progress.get("current_step") or "core")
        await self.chat.reload()
        self.chat.set_section(self.section)
        theme.safe_update(self)

    async def save_current(self) -> None:
        """设计界面 Ctrl+S：保存当前板块。"""
        if self.section == "story":
            await self.save_story()
        elif self.section == "guide":
            await self.guide_on_save_idea()
        elif self.section == "world":
            self._flash(self.world_hint, "请在分节卡片内点「保存本节」")
        else:
            self.app.append_log("ℹ️ 人物请在角色卡对话框内保存；大纲请点「采纳」")

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
        config.set_key("ui_design_nav_width", int(self.nav.width or 0))
        config.set_key("ui_design_ai_width", int(self.ai_panel.width or 0))
        config.save_config()

    def _flash(self, ctrl: ft.Text, msg: str) -> None:
        ctrl.value = msg
        ctrl.color = theme.semantic_color("success")
        theme.safe_update(ctrl)
