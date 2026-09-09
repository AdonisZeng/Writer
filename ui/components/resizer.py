"""可拖拽分隔条：用鼠标拖动调整相邻面板的宽度。

- 基于 GestureDetector 的 pan 手势（Flet 0.86 事件系统）；
- pan_start 记录起始指针位置与面板宽度，pan_update 以
  「起始宽度 + 指针位移 × 方向」实时改写面板宽度（绝对定位法，
  不受事件节流丢帧影响）；
- 悬停 / 拖拽时高亮提示，光标显示左右调整（RESIZE_LEFT_RIGHT）；
- 宽度 clamp 在 [min_width, max_width]，防止把面板拖没。
"""
from typing import Callable, Optional

import flet as ft


class VResizer(ft.GestureDetector):
    """垂直走向的分隔条，用于水平 Row 布局（拖动改变左右面板宽度）。

    direction：+1 表示向右拖动加宽目标面板（目标面板位于分隔条左侧）；
              -1 表示向左拖动加宽目标面板（目标面板位于分隔条右侧）。
    在父级 Row 中请配合 vertical_alignment=ft.CrossAxisAlignment.STRETCH
    使用，使分隔条占满整行高度。
    """

    def __init__(self, get_width: Callable[[], float],
                 set_width: Callable[[float], None],
                 direction: int = 1,
                 min_width: float = 180, max_width: float = 640,
                 on_drag_end: Optional[Callable[[], None]] = None):
        self._get_width = get_width
        self._set_width = set_width
        self._direction = 1 if direction >= 0 else -1
        self._min_width = min_width
        self._max_width = max_width
        self._on_drag_end_cb = on_drag_end
        self._start_x = 0.0
        self._start_w = 0.0
        self._dragging = False

        self._bar = ft.Container(
            width=6,
            bgcolor=ft.Colors.with_opacity(0.0, ft.Colors.ON_SURFACE),
        )
        super().__init__(
            content=self._bar,
            drag_interval=16,
            mouse_cursor=ft.MouseCursor.RESIZE_LEFT_RIGHT,
            on_pan_start=self._on_pan_start,
            on_pan_update=self._on_pan_update,
            on_pan_end=self._on_pan_end,
            on_pan_cancel=self._on_pan_end,
            on_enter=self._on_enter,
            on_exit=self._on_exit,
        )

    # ==================== 拖拽 ====================

    def _on_pan_start(self, e: ft.DragStartEvent) -> None:
        self._dragging = True
        self._start_x = e.local_position.x
        self._start_w = self._get_width() or 0

    def _on_pan_update(self, e: ft.DragUpdateEvent) -> None:
        if not self._dragging:
            return
        dx = e.local_position.x - self._start_x
        w = self._start_w + self._direction * dx
        w = max(self._min_width, min(self._max_width, w))
        self._set_width(w)

    def _on_pan_end(self, e=None) -> None:
        if not self._dragging:
            return
        self._dragging = False
        self._set_hover(False)
        if self._on_drag_end_cb:
            self._on_drag_end_cb()

    # ==================== 悬停高亮 ====================

    def _set_hover(self, hovered: bool) -> None:
        self._bar.bgcolor = (
            ft.Colors.with_opacity(0.35, ft.Colors.PRIMARY) if hovered
            else ft.Colors.with_opacity(0.0, ft.Colors.ON_SURFACE))
        if self.page:
            try:
                self._bar.update()
            except Exception:
                pass

    def _on_enter(self, e=None) -> None:
        self._set_hover(True)

    def _on_exit(self, e=None) -> None:
        if not self._dragging:
            self._set_hover(False)
