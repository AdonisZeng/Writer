"""红绿 Diff 对比卡（P2，方案 6.1-3 / 6.3）：hunk 级 Accept / Reject。

非模态浮层，嵌在编辑器下方动作区；逐块采纳后一次性写回，
绝不允许无预览的一刀切覆盖（UI 负面清单 3）。
"""
from typing import Callable, Optional

import flet as ft

from core import diff_utils


class DiffCard(ft.Container):
    def __init__(self):
        self.summary_text = ft.Text("", size=12, color=ft.Colors.OUTLINE)
        self.hunks_view = ft.ListView(spacing=6, height=260)
        self.title = ft.Row([
            ft.Text("🟢🩸 改写对比", size=13, weight=ft.FontWeight.W_600),
            ft.Container(expand=True), self.summary_text,
        ], spacing=8)
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
                     ft.OutlinedButton("全部跳过",
                                       on_click=self._handle_reject_all),
                     ft.OutlinedButton("取消", on_click=self._handle_cancel),
                 ], spacing=8)],
                spacing=8),
            visible=False,
            border_radius=12,
            bgcolor=ft.Colors.SURFACE,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            padding=12,
        )

    # ---------- 展示 ----------

    def show(self, old: str, new: str, summary_prefix: str = "") -> None:
        self._old_text = old
        self._hunks = diff_utils.compute_hunks(old, new)
        if not self._hunks:
            self.summary_text.value = "（无差异）"
            self.hunks_view.controls = [
                ft.Text("AI 未做任何改动。", size=12,
                        color=ft.Colors.OUTLINE)]
            self.visible = True
            if self.page:
                self.update()
            return
        self.title.controls[0].value = \
            f"🟢🩸 改写对比 · {summary_prefix}".strip()
        self.summary_text.value = diff_utils.hunks_summary(self._hunks)
        self.hunks_view.controls = [
            self._build_hunk_tile(i, h)
            for i, h in enumerate(self._hunks)]
        self.visible = True
        if self.page:
            self.update()

    def _build_hunk_tile(self, index: int, h: diff_utils.Hunk) -> ft.Container:
        def line_block(text: str, color) -> ft.Container:
            return ft.Container(
                content=ft.Text(text if text.strip() else "（空）", size=11,
                                selectable=True, max_lines=6,
                                overflow=ft.TextOverflow.ELLIPSIS),
                bgcolor=color, border_radius=6, padding=6, expand=True)

        rows = []
        if h.old_text:
            rows.append(ft.Row([
                ft.Text("−", size=13, color=ft.Colors.RED_400,
                        weight=ft.FontWeight.W_700),
                line_block(h.old_text,
                           ft.Colors.with_opacity(0.12, ft.Colors.RED))],
                spacing=6))
        if h.new_text:
            rows.append(ft.Row([
                ft.Text("+", size=13, color=ft.Colors.GREEN_500,
                        weight=ft.FontWeight.W_700),
                line_block(h.new_text,
                           ft.Colors.with_opacity(0.12, ft.Colors.GREEN))],
                spacing=6))
        checkbox = ft.Checkbox(
            label=f"块 {index + 1}（{h.action}）", value=True,
            on_change=lambda e, i=index: self._toggle(i, e.control.value))
        return ft.Container(
            content=ft.Column([
                ft.Row([checkbox,
                        ft.Container(expand=True),
                        ft.Text(f"旧 {h.a1 + 1}–{h.a2} 行", size=10,
                                color=ft.Colors.OUTLINE)], spacing=8),
                *rows],
                spacing=4),
            padding=8, border_radius=8,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        )

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
        self._hunks = [h for h in self._hunks]  # keep
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
