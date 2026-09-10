"""双模式编辑画布（方案 6.2 中栏 / 6.3 落地要点，P2/P3 扩展）。

- 编辑模式（TextField 纯文本输入）/ 预览模式（生成流式渲染 + 采纳动作）
  / 审阅模式（只读段落渲染 + 诊断波浪线高亮）
- 双层常驻 ft.Stack + visible 切换，绝不销毁重建控件（保滚动/光标/Undo）
- 3 秒输入停顿自动保存（防抖）+ dirty 标记
- 生成中不锁编辑区，Esc / 软停止随时定格；AI 产出必须作者确认才落盘
- P2：Ctrl+K 行内指令胶囊 → 精修 → Diff 对比卡 hunk 级采纳；
  Ghost 候选胶囊（状态栏 ⚡ / Alt+G 开关，Alt+/ 手动触发）
- P3：最大行宽/字号排版约束（apply_typography）
"""
import asyncio
import time
from datetime import datetime
from typing import Callable, Optional

import flet as ft

from core import config
from core.canon.validator import Issue
from ui import theme
from ui.components.diff_card import DiffCard
from ui.components.ghost_bar import GhostBar, GhostController

SAVE_DEBOUNCE_S = 3.0        # 输入停顿 3 秒自动保存（方案 5.6）
FLUSH_INTERVAL_S = 0.08      # 流式刷新 80ms 缓冲批量刷新（方案 5.2.2）


class EditorView(ft.Column):
    def __init__(self, project_provider: Callable[[], str],
                 on_words: Callable[[int], None],
                 on_saved: Callable[[str], None],
                 on_dirty: Callable[[], None]):
        self.project_provider = project_provider
        self.on_words_cb = on_words
        self.on_saved_cb = on_saved
        self.on_dirty_cb = on_dirty

        # 生命周期回调（由 WriterApp 注入）
        self.on_stop_cb: Optional[Callable] = None
        self.on_accept_cb: Optional[Callable] = None
        self.on_discard_cb: Optional[Callable] = None
        self.on_regenerate_cb: Optional[Callable] = None
        self.on_pov_cb: Optional[Callable] = None
        self.on_finalize_cb: Optional[Callable] = None
        self.on_rollback_cb: Optional[Callable] = None
        self.on_refine_cb: Optional[Callable] = None

        self.current_chapter: Optional[dict] = None
        self.dirty = False
        self._save_timer: Optional[asyncio.Task] = None
        self._last_flush = 0.0
        self._gen_buf: list[str] = []
        self._review_issues: list[Issue] = []
        self.review_mode = False

        # ---- 顶部信息条 ----
        self.breadcrumb = ft.Text("未选择章节", size=theme.SIZE_LG,
                                  weight=theme.W_SEMIBOLD, color=theme.TEXT)
        self.status_chip = ft.Container(
            content=ft.Text("", size=theme.SIZE_XS, color="#FFFFFF"),
            visible=False, padding=ft.Padding(theme.SPACE_SM, theme.SPACE_XXS,
                                              theme.SPACE_SM, theme.SPACE_XXS),
            border_radius=theme.RADIUS_PILL,
        )
        self.word_label = theme.metric_text("0 字")
        self.pov_btn = ft.PopupMenuButton(
            tooltip="视角（POV）：点选在出场角色间切换（5.4.6 信息差）",
            content=theme.chip("视角：默认"),
            items=[ft.PopupMenuItem(content=ft.Text("（本章无出场角色）"),
                                    disabled=True)],
            on_select=self._handle_pov,
        )
        self.review_btn = ft.IconButton(
            icon=ft.Icons.RATE_REVIEW, icon_size=theme.ICON_ACTION,
            icon_color=theme.TEXT_MUTED,
            tooltip="审阅模式：只读渲染 + 诊断波浪线",
            on_click=self._toggle_review,
        )
        self.finalize_btn = ft.FilledButton(
            "定稿", icon=ft.Icons.VERIFIED, visible=False,
            on_click=self._handle_finalize,
        )
        self.rollback_btn = ft.OutlinedButton(
            "回滚定稿", icon=ft.Icons.UNDO, visible=False,
            on_click=self._handle_rollback,
        )
        self.stop_btn = ft.FilledButton(
            "停止生成 (Esc)", icon=ft.Icons.STOP, visible=False,
            bgcolor=theme.semantic_color("danger"), color="#FFFFFF",
            on_click=self._handle_stop,
        )

        # ---- Ctrl+K 指令胶囊（非模态浮层，6.1-1）----
        self.k_instruction = ft.TextField(
            hint_text="对本选区的指令（如：精简对话 / 更肃杀一点 / 增加环境描写）",
            dense=True, expand=True, border_radius=theme.RADIUS_PILL,
            on_submit=self._handle_refine,
        )
        self.k_pill = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.AUTO_FIX_HIGH, size=theme.ICON_SMALL,
                        color=theme.ACCENT),
                ft.Text("精修", size=theme.SIZE_SM, weight=theme.W_SEMIBOLD,
                        color=theme.ACCENT),
                self.k_instruction,
                ft.FilledButton("生成改写", on_click=self._handle_refine),
                ft.IconButton(icon=ft.Icons.CLOSE,
                              icon_size=theme.ICON_SMALL,
                              tooltip="关闭", on_click=self._hide_k_pill),
            ], spacing=theme.SPACE_SM),
            visible=False, opacity=0.0,
            border_radius=theme.RADIUS_PILL,
            bgcolor=theme.ACCENT_SOFT,
            border=ft.Border.all(1, ft.Colors.with_opacity(
                0.28, theme.ACCENT)),
            padding=ft.Padding(theme.SPACE_MD, theme.SPACE_XS,
                               theme.SPACE_XS, theme.SPACE_XS),
            animate_opacity=theme.ANIM_FAST,
        )

        # ---- 双模式画布（Stack 常驻，visible 切换）----
        self._font_size = int(config.get("editor_font_size", 15))
        self.text_field = ft.TextField(
            multiline=True, shift_enter=True,
            min_lines=1, expand=True,
            border_color=ft.Colors.TRANSPARENT,
            focused_border_color=ft.Colors.TRANSPARENT,
            content_padding=ft.Padding(theme.SPACE_XL, 20, theme.SPACE_XL, 20),
            text_size=self._font_size,
            text_style=self._body_style(self._font_size),
            # 稿纸底色由画布层提供，输入区自身不填充：显式 filled=False 才能
            # 真正关掉「悬停/聚焦背景填充」——否则只要设了 bgcolor，Flet 会把
            # filled 隐式置为 True，鼠标划过时整行被主题色刷成色块、文字不可读。
            filled=False, bgcolor=ft.Colors.TRANSPARENT,
            hint_text="从左侧选择章节开始创作；选中文本后 Ctrl+K 精修…",
            on_change=self._handle_change,
        )
        self.preview_text = ft.Text("", selectable=True,
                                    style=self._body_style(self._font_size))
        # 最大行宽约束（P3 排版自定义，config editor_width；0 = 不限宽）
        self._content_width = config.get("editor_width", 780)
        self.edit_layer = self._build_layer(self.text_field, visible=True)
        self.preview_layer = self._build_layer(
            ft.Column([self.preview_text], scroll=ft.ScrollMode.AUTO,
                      expand=True),
            visible=False, padding=ft.Padding(theme.SPACE_XL, 20,
                                              theme.SPACE_XL, 20))
        self.canvas = ft.Stack(
            controls=[self.edit_layer, self.preview_layer], expand=True,
        )

        # ---- Ghost 候选胶囊 + Diff 对比卡（P2）----
        self.ghost_bar = GhostBar()
        self.ghost = GhostController(self, self.ghost_bar)
        self.diff_card = DiffCard()
        self.diff_card.on_apply_cb = self._apply_diff_result
        self._refine_target = ("", 0, 0)   # (old_text, sel_start, sel_end)
        self._refine_full = False

        # ---- 画布下方动作条 ----
        self.gen_hint = ft.Text("", size=theme.SIZE_XS,
                                color=theme.TEXT_MUTED, visible=False)
        self.review_actions = ft.Row(
            controls=[
                ft.FilledButton("写入正文", icon=ft.Icons.SAVE,
                                on_click=self._handle_accept),
                ft.OutlinedButton("重新生成", icon=ft.Icons.REFRESH,
                                  on_click=self._handle_regenerate),
                ft.OutlinedButton("丢弃", icon=ft.Icons.DELETE_OUTLINE,
                                  on_click=self._handle_discard),
                ft.Container(
                    content=ft.Text("生成内容未经确认不会落盘",
                                    size=theme.SIZE_XS,
                                    color=theme.TEXT_FAINT),
                    margin=ft.Margin(theme.SPACE_SM, 0, 0, 0),
                ),
            ],
            spacing=theme.SPACE_SM, visible=False,
        )

        super().__init__(
            controls=[
                ft.Row([self.breadcrumb, self.status_chip, self.word_label,
                        self.pov_btn,
                        ft.Container(expand=True),
                        self.rollback_btn, self.finalize_btn,
                        self.review_btn, self.stop_btn],
                       spacing=theme.SPACE_MD),
                self.k_pill,
                self.canvas,
                self.ghost_bar,
                self.diff_card,
                ft.Row([self.gen_hint, self.review_actions],
                       spacing=theme.SPACE_SM),
            ],
            spacing=theme.SPACE_SM,
            expand=True,
        )

    @staticmethod
    def _body_style(size: int) -> ft.TextStyle:
        """正文阅读样式：衬线 + 加大行高（长文阅读的舒适区）。"""
        return ft.TextStyle(
            size=size, height=theme.LINE_HEIGHT_READ,
            font_family=theme.FONT_SERIF,
            font_family_fallback=theme.FONT_SERIF_FALLBACK,
            color=theme.TEXT,
        )

    def _build_layer(self, content, visible: bool, padding=None) -> ft.Container:
        """画布层：一张抬起的「稿纸」——最大行宽居中，暖色柔和阴影。"""
        inner = ft.Container(
            content=content, width=self._content_width or None, expand=True)
        return ft.Container(
            content=ft.Column([inner], horizontal_alignment=ft.
                              CrossAxisAlignment.CENTER, expand=True),
            expand=True, visible=visible, border_radius=theme.RADIUS_MD,
            bgcolor=theme.CARD_BG,
            padding=padding,
            shadow=theme.paper_shadow(),
        )

    def apply_typography(self) -> None:
        """P3 排版自定义：字号 / 最大行宽（settings 保存后调用）。"""
        self._font_size = int(config.get("editor_font_size", 15))
        self._content_width = config.get("editor_width", 780)
        self.text_field.text_size = self._font_size
        self.text_field.text_style = self._body_style(self._font_size)
        self.preview_text.style = self._body_style(self._font_size)
        for layer in (self.edit_layer, self.preview_layer):
            inner = layer.content.controls[0]
            inner.width = self._content_width or None
        if self.page:
            self.update()

    # ==================== 章节装载 ====================

    def set_chapter(self, chapter: dict, text: str) -> None:
        self.current_chapter = chapter
        self.dirty = False
        self.text_field.value = text
        self.breadcrumb.value = f"第{chapter['number']}章 {chapter['title']}"
        self._update_status_chip(chapter.get("status", "outlined"))
        self.word_label.value = f"{len(text)} 字"
        self._set_review_issues([])
        self.enter_edit_mode()
        if self.page:
            self.update()

    def clear_chapter(self) -> None:
        self.current_chapter = None
        self.text_field.value = ""
        self.breadcrumb.value = "未选择章节"
        self.status_chip.visible = False
        self.word_label.value = "0 字"
        self._set_review_issues([])
        if self.page:
            self.update()

    def _update_status_chip(self, status: str) -> None:
        self.status_chip.visible = True
        self.status_chip.content.value = theme.status_label(status)
        self.status_chip.bgcolor = theme.status_color(status)

    def refresh_status_chip(self, chapter: dict) -> None:
        self._update_status_chip(chapter.get("status", "outlined"))
        if self.page:
            self.update()

    # ==================== POV / 定稿 / 回滚 ====================

    def update_pov_options(self, chapter: dict, pov: str) -> None:
        """POV 菜单：本章出场角色间切换。"""
        try:
            import json as _json
            names = _json.loads(chapter.get("characters") or "[]")
        except (ValueError, TypeError):
            names = []
        items = []
        if pov:
            items.append(ft.PopupMenuItem(
                content=ft.Text("默认（无固定视角）", size=theme.SIZE_SM),
                on_click=lambda e: self._set_pov("")))
        else:
            items.append(ft.PopupMenuItem(
                content=ft.Text("默认（无固定视角）", size=theme.SIZE_SM),
                checked=True, disabled=True))
        for n in names:
            selected = (n == pov)
            items.append(ft.PopupMenuItem(
                content=ft.Text(n, size=theme.SIZE_SM),
                checked=selected, disabled=selected,
                on_click=None if selected else
                (lambda e, nn=n: self._set_pov(nn))))
        self.pov_btn.items = items
        self.pov_btn.content.content.value = f"视角：{pov or '默认'}"
        if self.page:
            self.pov_btn.update()

    def _set_pov(self, name: str) -> None:
        if self.on_pov_cb:
            self.on_pov_cb(name)

    def _handle_pov(self, e=None) -> None:
        pass  # on_select 由 _set_pov 驱动

    def update_action_buttons(self, chapter: Optional[dict],
                              is_latest_finalized: bool = False) -> None:
        status = (chapter or {}).get("status", "")
        self.finalize_btn.visible = bool(chapter) and status in (
            "outlined", "drafted", "revised")
        self.finalize_btn.disabled = status == "outlined"
        self.finalize_btn.tooltip = (
            "先创建草稿或细纲，再定稿" if status == "outlined"
            else "过 Canon Gate 后置为 finalized 并执行后处理")
        self.rollback_btn.visible = bool(chapter) and \
            status == "finalized" and is_latest_finalized
        if self.page:
            self.update()

    def _handle_finalize(self, e=None) -> None:
        if self.on_finalize_cb:
            self.on_finalize_cb()

    def _handle_rollback(self, e=None) -> None:
        if self.on_rollback_cb:
            self.on_rollback_cb()

    # ========== 模式切换（visible 切换，不销毁控件） ==========

    def enter_edit_mode(self) -> None:
        self.review_mode = False
        self.edit_layer.visible = True
        self.preview_layer.visible = False
        self.review_actions.visible = False
        self.gen_hint.visible = False
        self.stop_btn.visible = False
        if self.page:
            self.update()

    def enter_preview_mode(self) -> None:
        self._gen_buf = []
        self.preview_text.value = ""
        self.preview_text.spans = None
        self.edit_layer.visible = False
        self.preview_layer.visible = True
        self.review_actions.visible = False
        self.gen_hint.value = "正在生成… 可随时 Esc 停止"
        self.gen_hint.color = theme.TEXT_MUTED
        self.gen_hint.visible = True
        self.stop_btn.visible = True
        if self.page:
            self.update()

    def show_review(self, text: str) -> None:
        """生成结束：呈现采纳动作（禁止静默落盘）。"""
        self.preview_text.value = text
        self.gen_hint.visible = False
        self.stop_btn.visible = False
        self.review_actions.visible = True
        if self.page:
            self.update()

    def show_hint(self, message: str, error: bool = False) -> None:
        """行内提示条：错误就地呈现，不依赖「生成日志」页签。"""
        self.gen_hint.value = message
        self.gen_hint.color = (theme.semantic_color("danger") if error
                               else theme.TEXT_MUTED)
        self.gen_hint.visible = bool(message)
        if self.page:
            self.update()

    # ==================== 审阅模式（波浪线，P2）====================

    def _set_review_issues(self, issues: list[Issue]) -> None:
        self._review_issues = issues or []

    def set_review_issues(self, issues: list[Issue]) -> None:
        self._set_review_issues(issues)
        if self.review_mode:
            self._render_review()
            if self.page:
                self.update()

    def _toggle_review(self, e=None) -> None:
        if self.review_mode:
            self.enter_edit_mode()
            return
        self.review_mode = True
        self._render_review()
        self.edit_layer.visible = False
        self.preview_layer.visible = True
        if self.page:
            self.update()

    def _render_review(self) -> None:
        """按段落渲染只读正文；有 warning 诊断的段落加波浪线（6.3）。"""
        text = self.text_field.value or ""
        paragraphs = text.split("\n")
        bad_paras: set[int] = set()
        for issue in self._review_issues:
            if issue.severity == "warning" and issue.paragraph:
                bad_paras.add(issue.paragraph)
        spans = []
        for i, para in enumerate(paragraphs, start=1):
            style = None
            if i in bad_paras:
                style = ft.TextStyle(
                    decoration=ft.TextDecoration.UNDERLINE,
                    decoration_style=ft.TextDecorationStyle.WAVY,
                    decoration_color=theme.semantic_color("warning"),
                    decoration_thickness=1.5,
                )
            spans.append(ft.TextSpan(
                text=para + ("\n" if i < len(paragraphs) else ""),
                style=style))
        self.preview_text.value = None
        self.preview_text.spans = spans
        self.gen_hint.visible = False
        self.stop_btn.visible = False
        self.review_actions.visible = False

    # ==================== Ctrl+K 精修（P2）====================

    def show_k_pill(self) -> None:
        """Ctrl+K：按当前选区状态呼出指令胶囊。"""
        if self.current_chapter is None:
            return
        sel = self.text_field.selection
        has_sel = bool(sel and isinstance(sel, (tuple, list))
                       and sel[1] > sel[0])
        self.k_instruction.label = (
            f"对选中 {sel[1] - sel[0]} 字的指令…" if has_sel
            else "整章精修指令（未选中文本时按整章处理）")
        self.k_pill.visible = True
        self.k_pill.opacity = 1.0
        if self.page:
            self.update()
            self.k_instruction.focus()

    def _hide_k_pill(self, e=None) -> None:
        self.k_pill.visible = False
        self.k_pill.opacity = 0.0
        if self.page:
            self.update()

    def _handle_refine(self, e=None) -> None:
        if self.on_refine_cb:
            self.on_refine_cb(self.k_instruction.value or "",
                              self._current_selection())

    def _current_selection(self) -> tuple[int, int]:
        sel = self.text_field.selection
        if sel and isinstance(sel, (tuple, list)) and sel[1] > sel[0]:
            return (sel[0], sel[1])
        return (0, len(self.text_field.value or ""))

    def start_refine(self, instruction: str, sel: tuple[int, int],
                     runner: Callable) -> None:
        """由 WriterApp 注入执行器（持有 model/日志等），编辑器只收集上下文。"""
        text = self.text_field.value or ""
        old = text[sel[0]:sel[1]]
        self._refine_target = (old, sel[0], sel[1])
        self._refine_full = (sel == (0, len(text)))
        before = text[max(0, sel[0] - 400):sel[0]] if not self._refine_full \
            else ""
        after = text[sel[1]:sel[1] + 400] if not self._refine_full else ""
        self.k_pill.visible = False
        self.k_pill.opacity = 0.0
        self.gen_hint.value = "精修中…"
        self.gen_hint.color = theme.TEXT_MUTED
        self.gen_hint.visible = True
        if self.page:
            self.update()
        runner(instruction, old, before, after, self._refine_full)

    def show_diff(self, old: str, new: str) -> None:
        """精修产出 → Diff 对比卡（绝不直接覆盖）。"""
        self.gen_hint.visible = False
        scope = "整章" if self._refine_full else "选区"
        self.diff_card.show(old, new, scope)
        if self.page:
            self.update()

    def _apply_diff_result(self, new_text: str) -> None:
        """逐块采纳完成：写回编辑器（选区或整章），标脏触发自动保存。"""
        old, a, b = self._refine_target
        text = self.text_field.value or ""
        if self._refine_full:
            self.text_field.value = new_text
        else:
            self.text_field.value = text[:a] + new_text + text[b:]
        self._handle_change()
        if self.page:
            self.update()
        if self.on_diff_applied_cb:
            self.on_diff_applied_cb()

    on_diff_applied_cb: Optional[Callable] = None  # 占位（实例化时覆盖）

    # ==================== 流式渲染（80ms 缓冲）====================

    def append_gen_chunk(self, delta: str) -> None:
        self._gen_buf.append(delta)
        now = time.time()
        if now - self._last_flush >= FLUSH_INTERVAL_S:
            self._last_flush = now
            self.preview_text.value = "".join(self._gen_buf)
            if self.page:
                self.preview_text.update()

    def gen_text_so_far(self) -> str:
        return "".join(self._gen_buf)

    # ==================== 编辑与自动保存 ====================

    def _handle_change(self, e=None) -> None:
        if self.current_chapter is None:
            return
        text = self.text_field.value or ""
        self.dirty = True
        self.word_label.value = f"{len(text)} 字"
        self.on_words_cb(len(text))
        self.on_dirty_cb()
        if self._save_timer:
            self._save_timer.cancel()
        self._save_timer = asyncio.create_task(self._debounced_save())
        if self.page:
            self.word_label.update()
        # Ghost：敲键盘即打断 + 重置防抖（5.2.5）
        self.ghost.on_text_change(e)

    async def _debounced_save(self) -> None:
        try:
            await asyncio.sleep(SAVE_DEBOUNCE_S)
        except asyncio.CancelledError:
            return
        if self.dirty:
            await self.save_now()

    async def save_now(self) -> Optional[str]:
        """立即保存当前章节（防抖到期 / Ctrl+S / 生成前调用）。"""
        if self.current_chapter is None or not self.dirty:
            return None
        from core import db, file_manager
        chapter = self.current_chapter
        text = self.text_field.value or ""
        path = file_manager.find_chapter_file(
            self.project_provider(), chapter["number"], chapter["title"])
        await asyncio.to_thread(file_manager.write_text, path, text)
        await db.update_chapter(chapter["id"], word_count=len(text))
        self.dirty = False
        self.on_saved_cb(datetime.now().strftime("%H:%M"))
        return path

    # ==================== 生成采纳动作 ====================

    def _handle_stop(self, e=None) -> None:
        if self.on_stop_cb:
            self.on_stop_cb()

    def _handle_accept(self, e=None) -> None:
        if self.on_accept_cb:
            self.on_accept_cb(self.gen_text_so_far()
                              or (self.preview_text.value or ""))

    def _handle_discard(self, e=None) -> None:
        self.enter_edit_mode()
        if self.on_discard_cb:
            self.on_discard_cb()

    def _handle_regenerate(self, e=None) -> None:
        if self.on_regenerate_cb:
            self.on_regenerate_cb()
