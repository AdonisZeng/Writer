"""AI 协作台（设计界面右栏）：多轮持久对话 + 分步引导 + 分类采纳。

纪律与写作界面一致：AI 产出**先预览、作者确认后**才写入设定库（不静默落盘）；
对话历史按项目持久化（每个项目独立 `state.db`），切换项目互不串扰。

本版新增：
- 采纳「提炼后写入」（AI 自动提炼到目标字段，可改用原样）；
- AI 主动提问的 choice 结构化选项（点选即回答并自动续跑）；
- assistant 回复 Markdown 渲染。
"""
import asyncio
import time
from typing import Optional

import flet as ft

from core import ai_service, config, design
from ui import theme

FLUSH_INTERVAL_S = 0.08          # 流式刷新节流（与编辑器一致）

_TARGET_LABELS = {
    "premise": "一句话前提",
    "theme": "主题立意",
    "synopsis": "故事梗概",
    "genre": "类型 / 题材",
    "world": "世界观分节",
    "character": "角色卡",
}

# 可「提炼后采纳」的字段（角色卡走对话框，不在此列）
_EXTRACT_TARGETS = {"premise", "theme", "synopsis", "genre", "world"}

_QUICK_PROMPTS: dict[str, list[str]] = {
    "guide": [
        "带我梳理一个完整的故事框架",
        "我只有一个模糊的念头，请提问引导我",
    ],
    "story": [
        "把一句话前提打磨得更抓人，给 3 个方向",
        "梳理主线三幕结构与关键转折",
        "设计一个出人意料但自洽的结局",
    ],
    "cast": [
        "为当前主角设计一个立体的对手（反派）",
        "检查主要角色动机与弧光是否完整",
        "设计一个能推动主线的配角及其秘密",
    ],
    "world": [
        "扩充力量 / 科技体系，并给出代价与限制",
        "梳理主要势力格局与它们的核心矛盾",
        "补充地理、时代与日常生活质感",
    ],
    "outline": [
        "基于现有设定给出分卷结构建议",
        "检查主线节奏与伏笔铺排是否合理",
    ],
}


class DesignChat(ft.Container):
    """设计协作台（右栏）。view = DesignView，负责落库与板块联动。"""

    def __init__(self, view):
        self.view = view
        self.mode = "free"           # free=自由对话 / guide=分步引导
        self.guide_step = "core"
        self._chat_task: Optional[asyncio.Task] = None
        self._cancel_event: Optional[asyncio.Event] = None
        self._stream_acc = ""
        self._stream_ctrl: Optional[ft.Control] = None
        self._last_flush = 0.0
        self._section = "guide"
        self._reloading = False
        # 本轮会话确定的收尾工具（send 时算一次，重试复用：
        # 中途被 ai_service 拉黑时不能让 System 提示与实际挂载打架）
        self._pending_tools: list = []

        # ---- 思考过程块（每轮 AI 消息一个，流式期展开、正文出现后折叠）----
        self._thought_acc = ""
        self._thought_open = False
        self._thought_collapsed = True
        self._thought_head: Optional[ft.Container] = None
        self._thought_body: Optional[ft.Container] = None
        self._thought_text: Optional[ft.Text] = None
        self._thought_caret: Optional[ft.Icon] = None
        self._last_thought_flush = 0.0

        # ---- 头部：模式切换 + 停止 / 清空 ----
        self._mode_btns: dict[str, ft.Container] = {}
        mode_switch = self._build_mode_switch()
        self.stop_btn = ft.OutlinedButton("停止", icon=ft.Icons.STOP,
                                          visible=False, on_click=self.stop)
        self.clear_btn = ft.IconButton(
            icon=ft.Icons.DELETE_SWEEP, icon_size=theme.ICON_INLINE,
            icon_color=theme.TEXT_MUTED, tooltip="清空本项目对话记录",
            on_click=lambda e: self._confirm_clear())

        self.focus_label = theme.chip("自由对话", active=True)
        self._focus_text = self.focus_label.content
        self.quick_row = ft.Row(wrap=True, spacing=theme.SPACE_XS,
                                run_spacing=theme.SPACE_XS)

        # ---- 对话区 ----
        self.chat_view = ft.ListView(
            expand=True, spacing=theme.SPACE_MD, auto_scroll=True,
            padding=ft.Padding(theme.SPACE_XXS, theme.SPACE_SM,
                               theme.SPACE_SM, theme.SPACE_SM))
        self._welcome = theme.empty_state(
            ft.Icons.AUTO_AWESOME, "与 AI 协作打磨作品框架",
            "对话会记住上下文；产出经「采纳」预览确认后才写入设定库",
            compact=False)
        self.chat_view.controls.append(self._welcome)

        # ---- 采纳目标 ----
        self.accept_target = ft.Dropdown(
            text="采纳到", value="premise", dense=True, expand=True,
            options=[ft.DropdownOption(key=k, text=v)
                     for k, v in _TARGET_LABELS.items()],
            on_select=lambda e: self._sync_world_row())
        self.world_section = ft.Dropdown(
            text="分节", dense=True, expand=True, options=[])
        self.world_mode = ft.Dropdown(
            text="方式", value="append", dense=True, width=96,
            options=[ft.DropdownOption(key="append", text="追加"),
                     ft.DropdownOption(key="replace", text="替换")])
        self.world_row = ft.Row(
            [ft.Text("分节", size=theme.SIZE_XS, color=theme.TEXT_MUTED),
             self.world_section, self.world_mode],
            spacing=theme.SPACE_SM, visible=False)

        # ---- 输入 ----
        self.chat_input = ft.TextField(
            hint_text="描述你的设计诉求…（Enter 发送 / Shift+Enter 换行）",
            multiline=True, min_lines=1, max_lines=5, shift_enter=True,
            dense=True, expand=True,
            on_submit=lambda e: asyncio.create_task(self.send()))
        self.send_btn = ft.FilledButton("发送", icon=ft.Icons.SEND,
                                        on_click=lambda e:
                                        asyncio.create_task(self.send()))

        super().__init__(
            content=ft.Column(
                controls=[
                    theme.section_header(ft.Icons.AUTO_AWESOME, "AI 协作台",
                                         accent=True, trailing=self.stop_btn),
                    ft.Row([mode_switch, ft.Container(expand=True),
                            self.clear_btn], spacing=theme.SPACE_SM),
                    self.focus_label,
                    self.quick_row,
                    theme.divider(),
                    self.chat_view,
                    theme.divider(),
                    ft.Row([ft.Text("采纳到", size=theme.SIZE_XS,
                                    color=theme.TEXT_MUTED),
                            self.accept_target], spacing=theme.SPACE_SM),
                    self.world_row,
                    ft.Row([self.chat_input, self.send_btn],
                           spacing=theme.SPACE_SM,
                           vertical_alignment=ft.CrossAxisAlignment.END),
                ],
                spacing=theme.SPACE_SM, expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            width=int(config.get("ui_design_ai_width", 400)),
            padding=ft.Padding(theme.SPACE_MD, theme.SPACE_MD,
                               theme.SPACE_LG, theme.SPACE_MD),
            bgcolor=theme.PANEL_BG,
        )
        self._preselect_target()
        self._sync_world_row()

    # ==================== 头部：模式 ====================

    def _build_mode_switch(self) -> ft.Container:
        for key, label in (("free", "自由对话"), ("guide", "分步引导")):
            btn = ft.Container(
                content=ft.Text(label, size=theme.SIZE_XS),
                padding=ft.Padding(theme.SPACE_MD, theme.SPACE_XS,
                                   theme.SPACE_MD, theme.SPACE_XS),
                border_radius=theme.RADIUS_SM, ink=True,
                on_click=lambda e, k=key: self.set_mode(k),
            )
            self._mode_btns[key] = btn
        return ft.Container(
            content=ft.Row(list(self._mode_btns.values()),
                           spacing=theme.SPACE_XXS, tight=True),
            bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.ON_SURFACE),
            border_radius=theme.RADIUS_MD,
            padding=ft.Padding(3, 3, 3, 3),
        )

    def _style_modes(self) -> None:
        for key, btn in self._mode_btns.items():
            active = key == self.mode
            btn.bgcolor = theme.ACCENT_SOFT if active else None
            btn.content.color = (theme.ON_ACCENT_SOFT if active
                                 else theme.TEXT_MUTED)
            btn.content.weight = (theme.W_SEMIBOLD if active
                                  else theme.W_REGULAR)

    def set_mode(self, mode: str, *, update: bool = True) -> None:
        self.mode = mode if mode in ("free", "guide") else "free"
        self._style_modes()
        self._update_focus()
        self._preselect_target()
        self._sync_world_row()
        if update:
            theme.safe_update(self)

    # ==================== 聚焦 / 快捷提示 / 采纳目标 ====================

    def set_guide_step(self, key: str) -> None:
        self.guide_step = key or "core"
        if self.mode == "guide":
            self._update_focus()
            self._preselect_target()
        theme.safe_update(self._focus_text)

    def set_section(self, section: str) -> None:
        """板块切换时刷新聚焦标签、快捷提示与默认采纳目标。"""
        self._section = section
        self.quick_row.controls = [
            theme.chip(p, on_click=lambda e, t=p: self._fill_input(t),
                       tooltip="填入输入框，可再编辑")
            for p in _QUICK_PROMPTS.get(section, [])]
        self._update_focus()
        self._preselect_target()
        theme.safe_update(self.quick_row, self._focus_text, self.world_row)

    def _preselect_target(self) -> None:
        """按当前引导步骤 / 板块智能预选采纳目标（减少误采纳）。"""
        target = None
        if self.mode == "guide":
            st = design.step_by_key(self.guide_step) or {}
            for t in st.get("adopt_targets") or []:
                if t in _TARGET_LABELS:
                    target = t
                    break
        if target is None:
            target = {"story": "premise", "cast": "character",
                      "world": "world", "outline": "premise"}.get(
                self._section)
        if target:
            self.accept_target.value = target

    def _section_label(self) -> str:
        return dict(self.view.SECTIONS).get(self._section,
                                            self.view.section)

    def _update_focus(self) -> None:
        if self.mode == "guide":
            st = design.step_by_key(self.guide_step) or {}
            self._focus_text.value = f"分步引导 · {st.get('label', '')}"
        else:
            self._focus_text.value = f"当前板块：{self._section_label()}"
        self._focus_text.color = theme.ON_ACCENT_SOFT

    def _fill_input(self, text: str) -> None:
        self.chat_input.value = text
        theme.safe_update(self.chat_input)
        try:
            self.chat_input.focus()
        except Exception:
            pass

    def prefill(self, text: str, *, mode: Optional[str] = None,
                step: Optional[str] = None) -> None:
        """外部（如世界观分节的「让 AI 补全」）预填输入框并可切换模式。"""
        if mode:
            self.set_mode(mode)
        if step:
            self.set_guide_step(step)
        self._fill_input(text)

    def _sync_world_row(self) -> None:
        is_world = (self.accept_target.value == "world")
        self.world_row.visible = is_world
        theme.safe_update(self.world_row)

    def refresh_world_options(self) -> None:
        sections = self.view.world_sections or []
        self.world_section.options = [
            ft.DropdownOption(key=s["section_key"],
                              text=s.get("label") or s["section_key"])
            for s in sections]
        if sections and not self.world_section.value:
            self.world_section.value = sections[0]["section_key"]
        theme.safe_update(self.world_section)

    # ==================== 历史 ====================

    async def reload(self) -> None:
        """切换项目 / 进入设计界面：清空并重载本项目对话（修复串历史）。

        防重入：并发调用（如切模式与切项目同时触发）会各自 await 后再渲染，
        导致消息重复；此处用标记串行化，且**在渲染前清空**避免交错叠加。
        """
        if self.is_chatting() or self._reloading:
            return
        self._reloading = True
        try:
            history = await design.load_history()
            self.chat_view.controls = []
            if not history:
                self.chat_view.controls.append(self._welcome)
            else:
                for i, m in enumerate(history):
                    role = m.get("role", "assistant")
                    answered = role == "assistant" and (
                        any(x.get("role") == "user" for x in history[i + 1:])
                        or self._step_done_locked(m.get("content") or "",
                                                  m.get("step") or ""))
                    self._add_msg(role, m.get("content") or "",
                                  with_actions=(role == "assistant"),
                                  answered=answered,
                                  step=m.get("step") or "")
            self._update_focus()
            theme.safe_update(self.chat_view)
        finally:
            self._reloading = False

    def _step_done_locked(self, text: str, step: str = "") -> bool:
        """历史消息里的收尾块：该步已完成则锁定回显（防误点旧卡重复采纳）。"""
        _, choices = design.parse_choices(text or "")
        if not choices:
            return False
        key = step if step in design.STEP_KEYS else ""
        if not key:
            key = design.resolve_step_key(str(choices.get("step_done") or ""))
        return bool(key) and key in (
            self.view.guide_progress.get("steps_done") or [])

    def _confirm_clear(self) -> None:
        async def confirm(ev=None):
            self.view.app.page.pop_dialog()
            await design.clear_history()
            self.chat_view.controls = [self._welcome]
            self.view.app.append_log("✓ 已清空本项目设计对话")
            theme.safe_update(self.chat_view)

        async def cancel(ev=None):
            self.view.app.page.pop_dialog()

        self.view.app.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("清空对话"),
            content=ft.Text("确定清空本项目的设计对话记录吗？此操作不可撤销，"
                            "不影响已写入的设定。"),
            actions=[ft.TextButton("取消", on_click=cancel),
                     ft.FilledButton("清空", on_click=confirm,
                                     color="#FFFFFF",
                                     bgcolor=theme.semantic_color("danger"))],
        ))

    # ==================== 消息渲染 ====================

    def _add_msg(self, role: str, text: str, with_actions: bool = False,
                 answered: bool = False,
                 step: str = "") -> tuple[ft.Control, ft.Container]:
        is_user = role == "user"
        if self._welcome in self.chat_view.controls:
            self.chat_view.controls.remove(self._welcome)
        if is_user:
            body: ft.Control = ft.Text(text, size=theme.SIZE_SM,
                                       selectable=True, color=theme.TEXT)
        else:
            body = ft.Markdown(
                text or "", selectable=True, auto_follow_links=False,
                extension_set=ft.MarkdownExtensionSet.GITHUB_FLAVORED,
                code_theme=theme.chat_markdown_code_theme(),
                md_style_sheet=theme.chat_markdown_style_sheet())
        bubble = ft.Container(
            content=ft.Column([
                ft.Text("你" if is_user else "AI 顾问", size=theme.SIZE_XXS,
                        weight=theme.W_SEMIBOLD,
                        color=(theme.ON_ACCENT_SOFT if is_user
                               else theme.TEXT_MUTED)),
                body,
            ], spacing=theme.SPACE_XS, tight=True),
            bgcolor=(theme.ACCENT_SOFT if is_user
                     else ft.Colors.with_opacity(0.05, ft.Colors.ON_SURFACE)),
            border_radius=theme.RADIUS_MD, padding=theme.SPACE_MD,
            expand=True,
        )
        self.chat_view.controls.append(bubble)
        if with_actions:
            self._finalize(body, bubble, text, answered=answered, step=step)
        return body, bubble

    def _finalize(self, body: ft.Control, bubble: ft.Container, raw_text: str,
                  *, answered: bool = False, step: str = "") -> None:
        """收尾一条 AI 消息：剥离 choice 块、挂采纳/复制、渲染选项卡。

        step：本消息所属的引导步骤（收尾块没给 step_done 时的兜底来源）。
        """
        self._collapse_thought()
        clean, choices = design.parse_choices(raw_text or "")
        if choices is not None and isinstance(body, ft.Markdown):
            body.value = clean
        self._add_actions(bubble, clean or raw_text or "")
        if choices is not None:
            self._append_choice_card(bubble, choices, answered=answered,
                                     source=clean or raw_text or "",
                                     step=step)

    def _add_actions(self, bubble: ft.Container, text: str) -> None:
        bubble.content.controls.append(ft.Row([
            ft.TextButton("采纳", icon=ft.Icons.DOWNLOAD_DONE,
                          on_click=lambda e, t=text: self._handle_accept(t)),
            ft.TextButton("复制", icon=ft.Icons.COPY_ALL,
                          on_click=lambda e, t=text: self._copy(t)),
        ], spacing=theme.SPACE_XS))

    # ---- 思考过程：可折叠块（on_thought 流式填充） ----

    def _attach_thought_block(self, bubble: ft.Container) -> None:
        """在 AI 气泡里挂「思考过程」折叠块：思考流式期默认展开，
        正式回答开始输出后自动折叠；无思考内容时整块不显示。"""
        self._thought_acc = ""
        self._thought_open = False
        self._thought_collapsed = True
        self._thought_text = ft.Text("", size=theme.SIZE_XS,
                                     color=theme.TEXT_MUTED, selectable=True)
        self._thought_body = ft.Container(
            content=self._thought_text, visible=False,
            padding=ft.Padding(theme.SPACE_MD, theme.SPACE_XXS, 0, 0),
            border=ft.Border(left=ft.BorderSide(2, theme.BORDER_COLOR)),
            opacity=0.9)
        self._thought_caret = ft.Icon(ft.Icons.EXPAND_MORE,
                                      size=theme.ICON_INLINE,
                                      color=theme.TEXT_FAINT)
        head_row = ft.Row([
            ft.Icon(ft.Icons.PSYCHOLOGY_ALT, size=theme.ICON_INLINE,
                    color=theme.TEXT_FAINT),
            ft.Text("思考过程", size=theme.SIZE_XXS, color=theme.TEXT_FAINT),
            self._thought_caret,
        ], spacing=theme.SPACE_XXS, tight=True)
        self._thought_head = ft.Container(
            content=head_row, visible=False, ink=True,
            padding=ft.Padding(0, theme.SPACE_XXS, 0, 0),
            on_click=lambda e: self._toggle_thought())
        # 插到角色标签之后、正文之前
        bubble.content.controls.insert(1, self._thought_head)
        bubble.content.controls.insert(2, self._thought_body)

    def _toggle_thought(self) -> None:
        self._thought_open = not self._thought_open
        self._paint_thought()
        theme.safe_update(self._thought_body, self._thought_caret)

    def _paint_thought(self) -> None:
        if self._thought_body is None:
            return
        self._thought_body.visible = self._thought_open
        self._thought_caret.name = (ft.Icons.EXPAND_LESS if self._thought_open
                                    else ft.Icons.EXPAND_MORE)

    def _collapse_thought(self) -> None:
        """正式回答开始后折叠思考块（用户仍可点开回看）。"""
        if self._thought_collapsed or not self._thought_acc:
            return
        self._thought_collapsed = True
        self._thought_open = False
        self._paint_thought()
        theme.safe_update(self._thought_body, self._thought_caret)

    def _on_thought(self, piece: str) -> None:
        """思考流回调：展开块并流式填充（与正文流同节流频率）。"""
        if self._thought_text is None:
            return
        self._thought_acc += piece
        self._thought_collapsed = False
        if not self._thought_head.visible:
            self._thought_head.visible = True
            self._thought_open = True
            self._paint_thought()
            theme.safe_update(self._thought_head, self._thought_body)
            self._last_thought_flush = 0.0   # 首块立即渲染
        now = time.time()
        if now - self._last_thought_flush < FLUSH_INTERVAL_S:
            return
        self._last_thought_flush = now
        self._thought_text.value = self._thought_acc
        try:
            self._thought_text.update()
        except Exception:
            pass

    def _copy(self, text: str) -> None:
        page = self._page()
        if page:
            page.set_clipboard(text)
            self.view.app.append_log("✓ 已复制到剪贴板")

    # ---- AI 主动提问：choice 选项卡 ----

    def _append_choice_card(self, bubble: ft.Container, choices: dict, *,
                            answered: bool = False, source: str = "",
                            step: str = "") -> None:
        opts = choices.get("options") or []
        state = {"sel": opts[0]["id"], "answered": bool(answered)}
        busy = {"on": False}
        tiles: dict[str, ft.Container] = {}
        hint = ft.Text("", size=theme.SIZE_XXS, color=theme.TEXT_FAINT)

        def paint() -> None:
            for o in opts:
                tile = tiles[o["id"]]
                sel = (o["id"] == state["sel"])
                tile.bgcolor = (
                    theme.ACCENT_SOFT if sel
                    else ft.Colors.with_opacity(0.04, ft.Colors.ON_SURFACE))
                tile.border = ft.Border.all(
                    1, theme.ACCENT if sel else theme.BORDER_COLOR)

        confirm_btn = ft.FilledButton("确定", icon=ft.Icons.CHECK)

        def lock(msg: str) -> None:
            state["answered"] = True
            confirm_btn.disabled = True
            hint.value = msg
            for t in tiles.values():
                t.ink = False
            theme.safe_update(confirm_btn, hint, *tiles.values())

        async def run_adopt(step_key: str) -> None:
            try:
                advanced = await self._adopt_step(
                    step_key, source, choices.get("sections") or [])
                lock("（已采纳并进入下一步）" if advanced
                     else "（已采纳，设定已更新）")
            except Exception as ex:
                busy["on"] = False
                confirm_btn.disabled = False
                hint.value = f"采纳失败：{ex}"
                theme.safe_update(confirm_btn, hint)
                self.view.app.append_log(f"✗ 步收尾采纳失败：{ex}")

        def submit() -> None:
            if state["answered"] or busy["on"]:
                return
            o = next(x for x in opts if x["id"] == state["sel"])
            # 收尾块所属步骤：优先本消息记录的实际步骤（可信），其次模型
            # 给出的 step_done（常乱写，做语义归一兜底）
            step_key = step if step in design.STEP_KEYS else ""
            if not step_key:
                step_key = design.resolve_step_key(
                    str(choices.get("step_done") or ""))
            if o.get("action") == "adopt" and step_key:
                # 本步收尾：直接采纳方案（落库 + 推进引导），不再续跑对话
                busy["on"] = True
                confirm_btn.disabled = True
                hint.value = "正在采纳本步方案…"
                for t in tiles.values():
                    t.ink = False
                theme.safe_update(confirm_btn, hint, *tiles.values())
                asyncio.create_task(run_adopt(step_key))
                return
            lock("（已作答，正在继续…）")
            preset = f"我选择：{o['title']}"
            if o.get("detail"):
                preset += f"（{o['detail']}）"
            asyncio.create_task(self.send(preset=preset))

        confirm_btn.on_click = lambda e: submit()

        def select(oid: str) -> None:
            if state["answered"]:
                return
            state["sel"] = oid
            paint()
            theme.safe_update(*tiles.values())

        rows: list[ft.Control] = [ft.Row([
            ft.Icon(ft.Icons.HELP_OUTLINE, size=theme.ICON_INLINE,
                    color=theme.ACCENT),
            ft.Text(choices.get("question", ""), size=theme.SIZE_SM,
                    weight=theme.W_SEMIBOLD, color=theme.TEXT, expand=True),
        ], spacing=theme.SPACE_XS)]
        for o in opts:
            children: list[ft.Control] = [
                ft.Text(o["title"], size=theme.SIZE_SM, color=theme.TEXT)]
            if o.get("detail"):
                children.append(ft.Text(
                    o["detail"], size=theme.SIZE_XS, color=theme.TEXT_MUTED,
                    max_lines=3, overflow=ft.TextOverflow.ELLIPSIS))
            tile = ft.Container(
                content=ft.Column(children, spacing=theme.SPACE_XXS,
                                  tight=True),
                padding=theme.SPACE_SM, border_radius=theme.RADIUS_SM,
                ink=True, on_click=lambda e, oid=o["id"]: select(oid))
            tiles[o["id"]] = tile
            rows.append(tile)
        rows.append(hint)
        rows.append(ft.Row([confirm_btn]))
        card = ft.Container(
            content=ft.Column(rows, spacing=theme.SPACE_XS),
            padding=theme.SPACE_SM, border_radius=theme.RADIUS_SM,
            border=ft.Border.all(1, theme.BORDER_COLOR),
            bgcolor=ft.Colors.with_opacity(0.03, ft.Colors.ON_SURFACE))
        paint()
        bubble.content.controls.append(card)
        if state["answered"]:
            lock("（该轮已作答）")
        else:
            hint.value = "请选择一个方向后点「确定」"

    # ---- 本步收尾：直接采纳（落库 + 推进引导） ----

    async def _adopt_step(self, step_key: str, source: str,
                          sections: list) -> bool:
        """「直接采纳」：把本步方案写入设定库 → 推进引导 → 回执（不续跑对话）。

        返回是否发生了步骤推进（False=仅更新已完成步骤的设定）。
        """
        app = self.view.app
        if step_key not in design.STEP_KEYS:
            raise ValueError(f"无效的步骤标记：{step_key}")
        if step_key not in ("core", "world"):
            raise RuntimeError("本步暂不支持「直接采纳」，请用「采纳」或"
                               "「标记完成，进入下一步」")
        if not (source or "").strip():
            raise RuntimeError("没有可采纳的方案正文")
        if self.is_chatting() or app.generating:
            raise RuntimeError("对话进行中，请稍后再试")
        if (step_key != "world" or not sections) and not app.current_model:
            raise RuntimeError("未选择模型，无法提炼内容")
        result = await design.apply_step_adoption(
            app.project, step_key, source, sections=sections,
            model=app.current_model or "",
            section_key=self.world_section.value or "")
        nxt, advanced = await self.view.on_step_adopted(step_key, result)
        self._append_receipt(step_key, result, nxt, advanced)
        return advanced

    def _append_receipt(self, step_key: str, result: dict,
                        next_key: str, advanced: bool) -> None:
        """对话流末尾追加采纳回执卡（含写入明细与「开始下一步引导」按钮）。"""
        st = design.step_by_key(step_key) or {}
        next_st = design.step_by_key(next_key) or {}
        lines: list[str] = []
        if result.get("kind") == "fields":
            written = result.get("written") or []
            skipped = result.get("skipped") or []
            if written:
                lines.append("已写入：" + "、".join(x["label"] for x in written))
            if skipped:
                lines.append("未提炼到内容，已跳过：" +
                             "、".join(x["label"] for x in skipped))
        else:
            created = [x["label"] for x in result.get("written") or []
                       if x.get("action") == "created"]
            updated = [x["label"] for x in result.get("written") or []
                       if x.get("action") != "created"]
            if created:
                lines.append("新建分节：" + "、".join(created))
            if updated:
                lines.append("更新分节：" + "、".join(updated))
            if not created and not updated:
                lines.append("未写入任何分节（可改用「采纳」手动写入）")
        if advanced and next_st:
            title = (f"✓ 『{st.get('label', step_key)}』已完成，"
                     f"进入『{next_st.get('label')}』")
        else:
            title = f"✓ 『{st.get('label', step_key)}』设定已更新"
        children: list[ft.Control] = [ft.Row([
            ft.Icon(ft.Icons.CHECK_CIRCLE, size=theme.ICON_INLINE,
                    color=theme.semantic_color("success")),
            ft.Text(title, size=theme.SIZE_SM, weight=theme.W_SEMIBOLD,
                    color=theme.TEXT, expand=True),
        ], spacing=theme.SPACE_XS)]
        children += [ft.Text(t, size=theme.SIZE_XS, color=theme.TEXT_MUTED)
                     for t in lines]
        if advanced and next_key:
            start_btn = ft.FilledButton(
                f"开始『{next_st.get('label', next_key)}』引导",
                icon=ft.Icons.AUTO_AWESOME)

            def start(ev=None, k=next_key, b=start_btn):
                b.disabled = True
                theme.safe_update(b)
                asyncio.create_task(self.send_guide(k))

            start_btn.on_click = start
            children.append(ft.Row([start_btn]))
        card = theme.tile_card(ft.Column(children, spacing=theme.SPACE_XS),
                               padding=theme.SPACE_MD)
        if self._welcome in self.chat_view.controls:
            self.chat_view.controls.remove(self._welcome)
        self.chat_view.controls.append(card)
        theme.safe_update(self.chat_view)

    # ==================== 采纳（先预览，确认后落库） ====================

    def _handle_accept(self, text: str) -> None:
        target = self.accept_target.value or "premise"
        if target == "character":
            parsed = design.parse_character_snippet(text)
            self._show_character_preview(parsed)
            return
        section_key = self.world_section.value if target == "world" else ""
        world_mode = self.world_mode.value or "append"
        if target == "world" and not section_key:
            self.view.app.append_log("⚠️ 请先选择世界观分节")
            return
        old = self.view.current_value(target, section_key)
        text = (text or "").strip()
        if not text:
            return
        if config.get("design_extract_on_adopt", True) \
                and target in _EXTRACT_TARGETS:
            asyncio.create_task(self._extract_then_preview(
                target, text, old, section_key, world_mode))
        else:
            self._show_text_preview(target, text, old, section_key, world_mode)

    async def _extract_then_preview(self, target: str, raw: str, old: str,
                                    section_key: str, world_mode: str) -> None:
        busy = theme.tile_card(ft.Row([
            theme.inline_loader(14),
            ft.Text("正在提炼为字段内容…", size=theme.SIZE_XS,
                    color=theme.TEXT_MUTED),
        ], spacing=theme.SPACE_SM), padding=theme.SPACE_SM)
        self.chat_view.controls.append(busy)
        theme.safe_update(self.chat_view)
        refined = raw
        try:
            refined = await design.extract_for_target(
                self.view.app.project, target, raw, current=old,
                model=self.view.app.current_model) or raw
        except Exception as ex:
            self.view.app.append_log(f"⚠️ 提炼失败，改用原样采纳：{ex}")
        self._dismiss_card(busy)
        self._show_text_preview(target, refined, old, section_key, world_mode,
                                raw=raw)

    def _preview_card(self, title: str, old: str, new: str, on_confirm,
                      on_alt=None, alt_label: str = "") -> ft.Container:
        def block(label: str, value: str, tone: str) -> ft.Column:
            return ft.Column([
                ft.Text(label, size=theme.SIZE_XXS, color=theme.TEXT_FAINT),
                ft.Container(
                    content=ft.Text(value or "（空）", size=theme.SIZE_XS,
                                    selectable=True, color=theme.TEXT,
                                    max_lines=6,
                                    overflow=ft.TextOverflow.ELLIPSIS),
                    bgcolor=ft.Colors.with_opacity(0.08, tone),
                    border_radius=theme.RADIUS_XS,
                    padding=theme.SPACE_SM),
            ], spacing=theme.SPACE_XXS)

        actions = [ft.FilledButton("确认写入",
                                   on_click=lambda e: on_confirm(card))]
        if on_alt is not None:
            actions.append(ft.TextButton(alt_label or "改用原样",
                                         on_click=lambda e: on_alt(card)))
        actions.append(ft.TextButton("取消",
                                     on_click=lambda e: self._dismiss_card(card)))
        card = theme.tile_card(ft.Column([
            ft.Row([ft.Icon(ft.Icons.DOWNLOAD_DONE, size=theme.ICON_INLINE,
                            color=theme.ACCENT),
                    ft.Text(f"采纳预览 · {title}", size=theme.SIZE_SM,
                            weight=theme.W_SEMIBOLD, color=theme.TEXT)]),
            block("原内容", old, theme.semantic_color("danger")),
            block("将写入", new, theme.semantic_color("success")),
            ft.Row(actions, spacing=theme.SPACE_SM),
        ], spacing=theme.SPACE_SM), padding=theme.SPACE_MD)
        return card

    def _show_text_preview(self, target: str, text: str, old: str,
                           section_key: str, world_mode: str,
                           raw: Optional[str] = None) -> None:
        label = _TARGET_LABELS.get(target, target)
        if target == "world":
            name = next((s.get("label") for s in self.view.world_sections
                         if s["section_key"] == section_key), section_key)
            label = f"世界观 · {name}"
        on_alt = None
        alt_label = ""
        if raw is not None and raw.strip() and raw.strip() != text.strip():
            alt_label = "改用原样"

            def on_alt(card):
                self._dismiss_card(card)
                self._show_text_preview(target, raw, old, section_key,
                                        world_mode)

        card = self._preview_card(
            label, old, text.strip(),
            lambda c: asyncio.create_task(
                self._apply_text(c, target, text, section_key, world_mode)),
            on_alt=on_alt, alt_label=alt_label)
        self.chat_view.controls.append(card)
        theme.safe_update(self.chat_view)

    async def _apply_text(self, card, target: str, text: str,
                          section_key: str, world_mode: str) -> None:
        try:
            label = await self.view.apply_adoption(
                target, text, section_key=section_key, world_mode=world_mode)
            self.view.app.append_log(f"✓ 已采纳到「{label}」")
        except Exception as ex:
            self.view.app.append_log(f"✗ 采纳失败：{ex}")
        self._dismiss_card(card)

    def _show_character_preview(self, parsed: dict) -> None:
        role_zh = {"protagonist": "主角", "antagonist": "反派",
                   "supporting": "配角"}.get(parsed.get("role"), "配角")
        summary = "\n".join(
            f"{k}：{parsed.get(k)}" for k in
            ("personality", "background", "abilities") if parsed.get(k)) \
            or parsed.get("raw", "")
        card = theme.tile_card(ft.Column([
            ft.Row([ft.Icon(ft.Icons.GROUPS, size=theme.ICON_INLINE,
                            color=theme.ACCENT),
                    ft.Text("采纳为角色卡（将打开角色对话框确认）",
                            size=theme.SIZE_SM, weight=theme.W_SEMIBOLD,
                            color=theme.TEXT)]),
            ft.Text(f"姓名：{parsed.get('name')}（{role_zh}）",
                    size=theme.SIZE_SM, color=theme.TEXT),
            ft.Text(summary or "（暂无描述，可在对话框中补充）",
                    size=theme.SIZE_XS, color=theme.TEXT_MUTED,
                    max_lines=8, overflow=ft.TextOverflow.ELLIPSIS),
            ft.Row([
                ft.FilledButton("打开角色卡",
                                on_click=lambda e:
                                self._open_character(card, parsed)),
                ft.TextButton("取消",
                              on_click=lambda e: self._dismiss_card(card)),
            ], spacing=theme.SPACE_SM),
        ], spacing=theme.SPACE_SM), padding=theme.SPACE_MD)
        self.chat_view.controls.append(card)
        theme.safe_update(self.chat_view)

    def _open_character(self, card, parsed: dict) -> None:
        self._dismiss_card(card)
        self.view.open_character_from_ai(parsed)

    def _dismiss_card(self, card: ft.Container) -> None:
        if card in self.chat_view.controls:
            self.chat_view.controls.remove(card)
        theme.safe_update(self.chat_view)

    # ==================== 发送 / 流式 ====================

    def _page(self):
        """当前 Page；未挂载（构造期 / 无头测试）时返回 None。"""
        try:
            return self.page
        except RuntimeError:
            return None

    def is_chatting(self) -> bool:
        return self._chat_task is not None and not self._chat_task.done()

    async def send_guide(self, step_key: str) -> None:
        """引导某一步：切到 guide 模式并自动发起一轮请求。"""
        st = design.step_by_key(step_key) or {}
        self.set_mode("guide", update=False)
        self.set_guide_step(step_key)
        idea = (self.view.guide.idea.value or "").strip()
        if idea:
            prompt = (f"我的初步想法是：{idea}\n\n"
                      f"请教我如何把「{st.get('label')}」这一步设计扎实？"
                      f"先谈谈你的理解，再给出初步方案。")
        else:
            prompt = (f"请教我如何从零开始设计「{st.get('label')}」？"
                      f"先给我几个可选方向与需要我回答的关键问题。")
        await self.send(preset=prompt)

    async def send(self, preset: Optional[str] = None) -> None:
        text = (preset if preset is not None
                else (self.chat_input.value or "")).strip()
        if not text:
            return
        app = self.view.app
        if app.generating:
            app.append_log("⚠️ 正文生成中，请先停止再使用设计协作")
            return
        if self.is_chatting():
            return
        if not app.current_model:
            app.append_log("⚠️ 未选择模型：请到设置页选择模型")
            page = self._page()
            if page:
                page.show_dialog(
                    ft.SnackBar(ft.Text("请先在设置页选择模型")))
            return
        if not await app.check_connection(notify=True):
            return

        self.chat_input.value = ""
        self._add_msg("user", text)
        ai_body, ai_box = self._add_msg("ai", "正在思考…")
        self._attach_thought_block(ai_box)
        self._stream_ctrl = ai_body
        self._stream_acc = ""
        self._last_flush = 0.0
        self.send_btn.disabled = True
        self.stop_btn.visible = True
        app.status_bar.set_state("busy")
        page = self._page()
        if page:
            page.update()

        step = self.guide_step if self.mode == "guide" else ""
        try:
            self._pending_tools = self._finish_tools()
            messages = await design.build_chat_messages(
                app.project, text, mode=self.mode, step=step,
                finish_tool=bool(self._pending_tools))
        except Exception as ex:
            if isinstance(ai_body, ft.Markdown):
                ai_body.value = f"✗ 装配上下文失败：{ex}"
            self._finish_chat()
            return
        self._cancel_event = asyncio.Event()
        self._chat_task = asyncio.create_task(
            self._run_chat(messages, ai_body, ai_box, step))

    def _finish_tools(self) -> list[dict]:
        """收尾工具（Function Calling 快路径）启用判定。

        design_tool_call：auto=支持则启用（默认）/ on=强制 / off=禁用。
        auto 下模型被记为「不支持工具」（ai_service 运行时探测）后自动回落
        文本协议；on 强制传入、被拒同样由 ai_service 剥离兜底。
        """
        mode = str(config.get("design_tool_call", "auto") or "auto").lower()
        if mode == "off":
            return []
        if mode == "on" or ai_service.tools_supported(
                self.view.app.current_model):
            return [design.FINISH_TOOL]
        return []

    def _merge_tool_finish(self, final: str, tool_calls) -> str:
        """工具路径归一：submit_step_finish 参数 → 标准 choice 块拼回正文。

        下游（协议审计 / 采纳卡 / 历史回显）只认 ```choice 文本协议，两条
        传输路径在此汇合。工具参数经过引擎级 Schema 校验，优先于正文里
        模型手写的（可能残缺的）choice 块。
        """
        if self.mode != "guide":
            return final
        call = next((c for c in tool_calls or []
                     if c.get("name") == design.FINISH_TOOL_NAME), None)
        if not call:
            return final
        args = design.loads_json(call.get("arguments") or "")
        choices = design.tool_args_to_choices(args, step_key=self.guide_step)
        if not choices:
            return final
        self.view.app.append_log("✓ 收尾动作经 Function Calling 提交")
        text = design.remove_choice_blocks(final or "")
        return f"{text}\n\n{design.choice_block(choices)}".strip()

    async def _run_chat(self, messages: list[dict], ai_body: ft.Control,
                        ai_box: ft.Container, step: str) -> None:
        app = self.view.app
        mode = self.mode
        try:
            stats = await ai_service.call_llm_stream(
                app.current_model, messages, on_chunk=self._on_chunk,
                on_thought=self._on_thought,
                purpose="design", temperature=0.8,
                cancel_event=self._cancel_event,
                tools=self._pending_tools or None)
            final = stats.text or self._stream_acc
            final = self._merge_tool_finish(final,
                                            getattr(stats, "tool_calls", ()))
            final = await self._maybe_retry_finish(messages, final, step)
            if isinstance(ai_body, ft.Markdown):
                ai_body.value = final or "（无内容返回）"
            if final:
                self._finalize(ai_body, ai_box, final, step=step)
                await design.append_message("assistant", final,
                                            mode=mode, step=step)
            app.append_log("✓ AI 设计建议已生成：可「采纳」写入设定库")
        except ai_service.GenerationCancelled as c:
            if isinstance(ai_body, ft.Markdown):
                ai_body.value = c.partial or "（已停止）"
            if c.partial:
                self._finalize(ai_body, ai_box, c.partial, step=step)
                await design.append_message("assistant", c.partial,
                                            mode=mode, step=step)
            app.append_log("■ 设计协作已停止（半成品保留）")
        except Exception as ex:
            if isinstance(ai_body, ft.Markdown):
                ai_body.value = f"✗ 生成失败：{ex}"
            app.status_bar.set_state("offline")
            app.append_log(f"✗ 设计协作失败：{ex}")
        finally:
            self._finish_chat()

    def _finish_chat(self) -> None:
        app = self.view.app
        self.send_btn.disabled = False
        self.stop_btn.visible = False
        self._stream_ctrl = None
        self._chat_task = None
        self._pending_tools = []
        if app.status_bar.state_text.value != "离线":
            app.status_bar.set_state("ready")
        page = self._page()
        if page:
            page.update()

    async def _maybe_retry_finish(self, messages: list[dict], final: str,
                                  step: str) -> str:
        """收尾协议打回：模型没按格式输出收尾块时，自动要求它重新输出。

        最多打回一次（成本 = 多一轮 LLM 调用，本地模型需多等几秒）；重试后
        仍不规范就接受现状，由解析层的嗅探/归一兜底。打回消息与中间回复
        **不写入对话历史**，保持上下文干净。
        """
        if self.mode != "guide" or step not in ("core", "world"):
            return final
        _, choices = design.parse_choices(final or "")
        issues = design.protocol_issues(final or "", choices, step_key=step)
        if not issues:
            return final
        app = self.view.app
        app.append_log("⚠️ AI 回复未按收尾协议输出，已自动要求重新输出")
        if isinstance(self._stream_ctrl, ft.Markdown):
            self._stream_ctrl.value = "（格式不符合协议，正在要求 AI 重新输出…）"
        self._reset_thought_block()
        self._stream_acc = ""
        self._last_flush = 0.0
        page = self._page()
        if page:
            page.update()
        retry_msgs = list(messages) + [
            {"role": "assistant", "content": final},
            {"role": "user", "content": design.retry_feedback(step, issues)},
        ]
        stats = await ai_service.call_llm_stream(
            app.current_model, retry_msgs, on_chunk=self._on_chunk,
            on_thought=self._on_thought, purpose="design", temperature=0.8,
            cancel_event=self._cancel_event,
            tools=self._pending_tools or None)
        retried = stats.text or self._stream_acc
        return self._merge_tool_finish(retried,
                                       getattr(stats, "tool_calls", ()))

    def _reset_thought_block(self) -> None:
        """打回重试时清空思考块（复用同一气泡，不重复挂控件）。"""
        self._thought_acc = ""
        self._thought_open = False
        self._thought_collapsed = True
        if self._thought_text is not None:
            self._thought_text.value = ""
        if self._thought_head is not None:
            self._thought_head.visible = False
        if self._thought_body is not None:
            self._thought_body.visible = False
        theme.safe_update(self._thought_text, self._thought_head,
                          self._thought_body)

    def _on_chunk(self, piece: str) -> None:
        self._stream_acc += piece
        self._collapse_thought()     # 正文开始输出：折叠思考块
        now = time.time()
        if now - self._last_flush < FLUSH_INTERVAL_S:
            return
        self._last_flush = now
        if self._stream_ctrl is not None:
            try:
                self._stream_ctrl.value = self._stream_acc
            except Exception:
                return
            page = self._page()
            if page:
                try:
                    self.chat_view.update()
                except Exception:
                    pass

    def stop(self, e=None) -> None:
        if self._cancel_event:
            self._cancel_event.set()
