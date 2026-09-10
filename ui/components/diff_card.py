"""红绿 Diff 对比卡（P2，方案 6.1-3 / 6.3）：hunk 级 Accept / Reject。

非模态浮层，嵌在编辑器下方动作区；逐块采纳后一次性写回，
绝不允许无预览的一刀切覆盖（UI 负面清单 3）。

视觉：红绿取自去饱和语义色；动作区精简为「应用所选 / 全部采纳 / 取消」。
"""
from typing import Callable, Optional

import flet as ft

from core import diff_utils
from ui import theme

_HUNKS_HEIGHT = 240


class DiffCard(ft.Container):
    def __init__(self):
        self.summary_text = ft.Text("", size=theme.SIZE_XS,
                                    color=theme.TEXT_MUTED)
        self.hunks_view = ft.ListView(spacing=theme.SPACE_SM,
                                      height=_HUNKS_HEIGHT)
        self.title_text = ft.Text("改写对比", size=theme.SIZE_MD,
                                  weight=theme.W_SEMIBOLD, color=theme.TEXT)
        self.title = ft.Row([
            ft.Icon(ft.Icons.DIFFERENCE, size=theme.ICON_INLINE,
                    color=theme.ACCENT),
            self.title_text,
            ft.Container(expand=True), self.summary_text,
        ], spacing=theme.SPACE_XS)
        self._hunks: list[diff_utils.Hunk] = []
        self._old_text = ""
        self.on_apply_cb: Optional[Callable[[str], None]] = None
        self.on_cancel_cb: Optional[Callable[[], None]] = None

        super().__init__(
            content=ft.Column(
                [self.title, self.hunks_view,
                 ft.Row([
                     ft.FilledButton("应用所选改动",
                                     on_click=self._handle_apply),
                     ft.OutlinedButton("全部采纳",
                                       on_click=self._handle_accept_all),
                     ft.TextButton("取消", on_click=self._handle_cancel),
                 ], spacing=theme.SPACE_SM)],
                spacing=theme.SPACE_SM),
            visible=False,
            border_radius=theme.RADIUS_MD,
            bgcolor=theme.CARD_BG,
            border=ft.Border.all(1, theme.BORDER_COLOR),
            padding=theme.SPACE_MD,
            animate_opacity=theme.ANIM_FAST,
        )

    # ---------- 展示 ----------

    def show(self, old: str, new: str, summary_prefix: str = "") -> None:
        self._old_text = old
        self._hunks = diff_utils.compute_hunks(old, new)
        self.title_text.value = \
            f"改写对比 · {summary_prefix}".strip() if summary_prefix \
            else "改写对比"
        if not self._hunks:
            self.summary_text.value = "（无差异）"
            self.hunks_view.controls = [theme.empty_state(
                ft.Icons.CHECK_CIRCLE_OUTLINE, "AI 未做任何改动")]
            self._reveal()
            return
        self.summary_text.value = diff_utils.hunks_summary(self._hunks)
        self.hunks_view.controls = [
            self._build_hunk_tile(i, h)
            for i, h in enumerate(self._hunks)]
        self._reveal()

    def _reveal(self) -> None:
        self.visible = True
        self.opacity = 1.0
        if self.page:
            self.update()

    def _build_hunk_tile(self, index: int, h: diff_utils.Hunk) -> ft.Container:
        def line_block(text: str, tone: str) -> ft.Container:
            return ft.Container(
                content=ft.Text(text if text.strip() else "（空）",
                                size=theme.SIZE_XS, selectable=True,
                                max_lines=6, color=theme.TEXT,
                                overflow=ft.TextOverflow.ELLIPSIS),
                bgcolor=ft.Colors.with_opacity(0.12, tone),
                border_radius=theme.RADIUS_XS, padding=theme.SPACE_XS + 2,
                expand=True)

        rows = []
        if h.old_text:
            rows.append(ft.Row([
                ft.Text("−", size=theme.SIZE_MD,
                        color=theme.semantic_color("danger"),
                        weight=theme.W_BOLD),
                line_block(h.old_text, theme.semantic_color("danger"))],
                spacing=theme.SPACE_SM))
        if h.new_text:
            rows.append(ft.Row([
                ft.Text("+", size=theme.SIZE_MD,
                        color=theme.semantic_color("success"),
                        weight=theme.W_BOLD),
                line_block(h.new_text, theme.semantic_color("success"))],
                spacing=theme.SPACE_SM))
        checkbox = ft.Checkbox(
            label=f"块 {index + 1}（{h.action}）", value=True,
            on_change=lambda e, i=index: self._toggle(i, e.control.value))
        return theme.tile_card(ft.Column([
            ft.Row([checkbox, ft.Container(expand=True),
                    theme.metric_text(f"旧 {h.a1 + 1}–{h.a2} 行",
                                      size=theme.SIZE_XXS)],
                   spacing=theme.SPACE_SM),
            *rows], spacing=theme.SPACE_XS), padding=theme.SPACE_SM)

    def _toggle(self, index: int, value: bool) -> None:
        self._hunks[index].accepted = bool(value)

    # ---------- 应用 ----------

    def _accepted_indices(self) -> set[int]:
        return {i for i, h in enumerate(self._hunks) if h.accepted}

    def _handle_apply(self, e=None) -> None:
        new_text = diff_utils.apply_hunks(self._old_text, self._hunks,
                                          self._accepted_indices())
        self.visible = False
        if self.page:
            self.update()
        if self.on_apply_cb:
            self.on_apply_cb(new_text)

    def _handle_accept_all(self, e=None) -> None:
        for h in self._hunks:
            h.accepted = True
        self._handle_apply()

    def _handle_reject_all(self, e=None) -> None:
        self._handle_cancel()

    def _handle_cancel(self, e=None) -> None:
        self.visible = False
        if self.page:
            self.update()
        if self.on_cancel_cb:
            self.on_cancel_cb()
