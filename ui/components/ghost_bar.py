"""Ghost 行内补全（P2 降级版，方案 5.2.5 / 6.3）。

候选预览条（TextField 无法渲染行内灰字，P3 再评估自定义控件）：
- 编辑区下方浮出「续写」胶囊；[采纳] 按钮基于光标位置切片插入（两个铁律：
  绝不 value += 追加；selection 不可用时降级复制到剪贴板）
- GhostController：防抖调度 + 强打断 + 快照式 Enter 采纳，随状态栏开关启停

视觉：强调色浅底胶囊 + 克制淡入，与 Ctrl+K 精修胶囊同一语言。
"""
import asyncio
from typing import Optional

import flet as ft

from core import ai_service, config
from ui import theme


class GhostBar(ft.Container):
    """候选胶囊：续写：<半句> [采纳 / 复制 / 忽略]"""

    def __init__(self):
        self.text_view = ft.Text("", size=theme.SIZE_SM, expand=True,
                                 max_lines=2, color=theme.TEXT,
                                 overflow=ft.TextOverflow.ELLIPSIS)
        self.on_accept_cb: Optional[callable] = None
        self.on_copy_cb: Optional[callable] = None
        super().__init__(
            content=ft.Row([
                ft.Icon(ft.Icons.BOLT, size=theme.ICON_SMALL,
                        color=theme.ACCENT),
                ft.Text("续写", size=theme.SIZE_SM, weight=theme.W_SEMIBOLD,
                        color=theme.ACCENT),
                self.text_view,
                ft.TextButton("采纳", icon=ft.Icons.KEYBOARD_RETURN,
                              on_click=self._accept),
                ft.IconButton(icon=ft.Icons.CONTENT_COPY,
                              icon_size=theme.ICON_SMALL,
                              tooltip="复制到剪贴板", on_click=self._copy),
                ft.IconButton(icon=ft.Icons.CLOSE,
                              icon_size=theme.ICON_SMALL,
                              tooltip="忽略 (Esc)", on_click=self._dismiss),
            ], spacing=theme.SPACE_XS),
            visible=False,
            opacity=0.0,
            border_radius=theme.RADIUS_PILL,
            bgcolor=theme.ACCENT_SOFT,
            border=ft.Border.all(1, ft.Colors.with_opacity(
                0.28, theme.ACCENT)),
            padding=ft.Padding(theme.SPACE_MD, theme.SPACE_XXS,
                               theme.SPACE_XXS, theme.SPACE_XXS),
            margin=ft.Margin(0, theme.SPACE_XS, 0, 0),
            animate_opacity=theme.ANIM_FAST,
        )

    def show(self, suggestion: str) -> None:
        self.text_view.value = suggestion
        self.visible = True
        self.opacity = 1.0
        if self.page:
            self.update()

    def hide(self) -> None:
        self.visible = False
        self.opacity = 0.0
        if self.page:
            self.update()

    def _accept(self, e=None) -> None:
        if self.on_accept_cb:
            self.on_accept_cb()

    def _dismiss(self, e=None) -> None:
        if self.on_accept_cb is None:
            return
        self.hide()

    def _copy(self, e=None) -> None:
        if self.on_copy_cb:
            self.on_copy_cb()


class GhostController:
    """非侵入挂载：防抖调度 + 强打断 + 候选条显隐，随状态栏开关随时启停。"""

    def __init__(self, editor, ghost_bar: GhostBar):
        self.editor = editor
        self.ghost_bar = ghost_bar
        self.enabled = bool(config.get("ghost_enabled", False))
        self.debounce_ms = config.get("ghost_debounce_ms", 1500)
        self.current_ghost = ""
        self.pending_task: Optional[asyncio.Task] = None
        self.cancel_event = asyncio.Event()
        # 快照：Enter 采纳时回滚刚插入的换行（P2 降级方案的补偿，见模块注释）
        self._snapshot_text = ""
        self._snapshot_cursor = 0

        ghost_bar.on_accept_cb = self.accept_current
        ghost_bar.on_copy_cb = self.copy_current

    def set_enabled(self, enabled: bool) -> None:
        """状态栏 ⚡ 开关 / Alt+G：切换并回写 config.json 记忆状态。"""
        self.enabled = enabled
        config.set_key("ghost_enabled", enabled)
        config.save_config()
        if not enabled:
            self._clear_ghost()

    def set_debounce(self, ms: int) -> None:
        self.debounce_ms = ms

    def on_text_change(self, e=None) -> None:
        """敲键盘即打断：取消在途请求与候选展示，重置防抖计时。"""
        if not self.enabled:
            return
        self._clear_ghost()
        self.cancel_event = asyncio.Event()
        self.pending_task = asyncio.create_task(self._debounce_worker())

    async def _debounce_worker(self, now: bool = False) -> None:
        try:
            await asyncio.sleep(0 if now else self.debounce_ms / 1000.0)
            text = self.editor.text_field.value or ""
            if len(text.strip()) < 10:            # 字数过少不补全
                return
            suggestion = await ai_service.fetch_ghost_suggestion(
                text, self.cancel_event)
            if suggestion and not self.cancel_event.is_set():
                self.current_ghost = suggestion
                # 快照（供 Enter 采纳时补偿换行）
                self._snapshot_text = text
                try:
                    sel = self.editor.text_field.selection
                    self._snapshot_cursor = (sel[0] if sel and isinstance(
                        sel, (tuple, list)) else len(text))
                except Exception:
                    self._snapshot_cursor = len(text)
                self.ghost_bar.show(suggestion)
        except asyncio.CancelledError:
            pass

    def manual_trigger(self) -> None:
        """Alt+/：开关关闭时也可手动要半句灵感（半自动模式）。"""
        self._clear_ghost()
        self.cancel_event = asyncio.Event()
        self.pending_task = asyncio.create_task(self._debounce_worker(now=True))

    def accept_current(self) -> None:
        """采纳：基于快照光标位置切片插入（铁律 ①）。"""
        if not self.current_ghost:
            return
        tf = self.editor.text_field
        # 采纳时 Enter 会在字段里插入一个换行——用快照重建，规避污染
        base = self._snapshot_text or tf.value or ""
        start = self._snapshot_cursor
        tf.value = base[:start] + self.current_ghost + base[start:]
        tf.selection = (start + len(self.current_ghost),) * 2
        self._clear_ghost()
        self.editor.dirty = True
        self.editor._handle_change()
        if self.editor.page:
            tf.update()

    def copy_current(self) -> None:
        """铁律 ② 降级路径：selection 不可用时交还作者 Ctrl+V。"""
        if self.editor.page and self.current_ghost:
            self.editor.page.set_clipboard(self.current_ghost)

    def on_key(self, e) -> bool:
        """全局键盘：Enter 整段采纳 / Ctrl+→ 逐词采纳（P3 降级实现）/
        Esc 放弃。返回 True 表示已消费。"""
        if not self.current_ghost:
            return False
        key = getattr(e, "key", "")
        if key == "Enter" and not getattr(e, "shift", False):
            self.accept_current()
            return True
        if key == "ArrowRight" and getattr(e, "ctrl", False):
            self.accept_word()
            return True
        if key == "Escape":
            self._clear_ghost()
            return True
        return False

    def accept_word(self) -> None:
        """P3 逐词采纳（降级实现）：按标点/空格切分，每次采纳一小段。

        无自定义 Flutter 控件时以此近似「逐词采纳」手感；插入仍走
        快照光标切片，绝不追加到文末。
        """
        import re
        if not self.current_ghost:
            return
        m = re.match(r"^(.*?[,，。！？；、,.!?…—]|\S+\s?)", self.current_ghost)
        seg = m.group(1) if m else self.current_ghost
        tf = self.editor.text_field
        base = self._snapshot_text or tf.value or ""
        start = self._snapshot_cursor
        tf.value = base[:start] + seg + base[start:]
        self._snapshot_cursor = start + len(seg)
        self._snapshot_text = tf.value
        rest = self.current_ghost[len(seg):].strip()
        if not rest:
            self._clear_ghost()
        else:
            self.current_ghost = rest
            self.ghost_bar.text_view.value = rest
            if self.ghost_bar.page:
                self.ghost_bar.text_view.update()
        self.editor.dirty = True
        self.editor._handle_change()
        if self.editor.page:
            tf.update()

    def _clear_ghost(self) -> None:
        """置位 cancel_event、取消 pending_task、清空候选、隐藏胶囊。"""
        self.cancel_event.set()
        if self.pending_task:
            self.pending_task.cancel()
            self.pending_task = None
        self.current_ghost = ""
        if self.ghost_bar.visible:
            self.ghost_bar.hide()
