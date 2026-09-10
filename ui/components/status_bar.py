"""底部飞行仪表盘状态栏（方案 6.2）。

界面模式切换（写作 / 设计）· 连接灯（就绪 / 推理中 / 离线）
· 模型名 · 速度（TTFT · tok/s）· 本会话用量 · 字数 · 自动保存时间。

视觉：暖中性令牌；实时数值一律等宽（消除宽度抖动）；连接灯做克制过渡。
"""
import flet as ft

from ui import theme

_STATE_SEMANTIC = {"ready": "success", "busy": "warning", "offline": "danger"}
_STATE_TEXT = {"ready": "就绪", "busy": "推理中", "offline": "离线"}
_MODE_LABELS = {"write": "写作", "design": "设计"}
_MODE_ICONS = {"write": ft.Icons.EDIT_NOTE, "design": ft.Icons.PALETTE}

_LAMP_ANIM = theme.ANIM_BASE


def _vdiv() -> ft.VerticalDivider:
    return ft.VerticalDivider(width=1, thickness=14,
                              color=theme.BORDER_COLOR)


class StatusBar(ft.Row):
    def __init__(self):
        # ---- 连接灯 ----
        self.lamp = ft.Container(
            width=9, height=9, border_radius=5,
            bgcolor=theme.semantic_color("danger"),
            margin=ft.Margin(0, 0, 2, 0), animate=_LAMP_ANIM)
        self.state_text = ft.Text(_STATE_TEXT["offline"], size=theme.SIZE_SM,
                                  weight=theme.W_MEDIUM)
        # ---- 指标（等宽，防抖动）----
        self.model_text = ft.Text("未连接", size=theme.SIZE_SM,
                                  color=theme.TEXT_MUTED)
        self.speed_text = theme.metric_text("")
        self.tokens_text = theme.metric_text("")
        self.context_btn = ft.TextButton(
            content=theme.metric_text("", color=theme.TEXT_MUTED),
            tooltip="上下文透视：查看本次生成的 Tier 分账（6.2）",
            visible=False, on_click=self._handle_context)
        self.words_text = theme.metric_text("0 字")
        self.saved_text = theme.metric_text("未保存")

        self.on_context_cb = None
        self.on_ghost_cb = None
        self.ghost_enabled = False
        self.ghost_label = ft.Text("补全:关", size=theme.SIZE_XS)
        self.ghost_icon = ft.Icon(ft.Icons.BOLT, size=theme.ICON_SMALL,
                                  color=theme.TEXT_MUTED)
        self.ghost_btn = ft.TextButton(
            content=ft.Row([self.ghost_icon, self.ghost_label],
                           spacing=theme.SPACE_XXS, tight=True),
            tooltip="行内补全开关 (Alt+G)；开启后停顿 1.5s 或 Alt+/ 触发",
            on_click=self._handle_ghost)

        # ---- 界面模式切换（写作 / 设计，与模型名同一行）----
        self.mode = "write"
        self.on_mode_cb = None
        self._mode_btns: dict[str, ft.Container] = {}
        for key in ("write", "design"):
            btn = ft.Container(
                content=ft.Row([
                    ft.Icon(_MODE_ICONS[key], size=theme.ICON_SMALL),
                    ft.Text(_MODE_LABELS[key], size=theme.SIZE_SM),
                ], spacing=theme.SPACE_XS, tight=True),
                padding=ft.Padding(theme.SPACE_MD, theme.SPACE_XS,
                                   theme.SPACE_MD, theme.SPACE_XS),
                border_radius=theme.RADIUS_SM, ink=True,
                animate=_LAMP_ANIM,
                tooltip="切换到设计界面（大纲 / 世界观 / 人物 + AI 协作）"
                if key == "design" else "切换到写作界面（章节正文创作）",
                on_click=lambda e, k=key: self._handle_mode(k),
            )
            self._mode_btns[key] = btn
        self.mode_switcher = ft.Container(
            content=ft.Row(list(self._mode_btns.values()),
                           spacing=theme.SPACE_XXS, tight=True),
            bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.ON_SURFACE),
            border_radius=theme.RADIUS_MD,
            padding=ft.Padding(3, 3, 3, 3),
        )

        super().__init__(
            controls=[
                self.mode_switcher,
                _vdiv(),
                self.lamp, self.state_text,
                _vdiv(),
                self.model_text,
                _vdiv(),
                self.speed_text, self.tokens_text, self.context_btn,
                ft.Container(expand=True),
                self.ghost_btn,
                _vdiv(),
                self.words_text,
                _vdiv(),
                self.saved_text,
            ],
            spacing=theme.SPACE_SM,
            margin=ft.Margin(theme.SPACE_LG, theme.SPACE_XS, theme.SPACE_LG,
                             theme.SPACE_XS),
        )
        self._sync_ghost_label()
        self._style_modes()

    # ==================== 模式切换 ====================

    def set_mode(self, mode: str) -> None:
        """同步模式切换器高亮（写作 / 设计）。"""
        self.mode = mode
        self._style_modes()
        if self.page:
            self.mode_switcher.update()

    def _style_modes(self) -> None:
        """仅设置样式属性（构造期 self.page 尚不可用，故与刷新分离）。"""
        for key, btn in self._mode_btns.items():
            active = key == self.mode
            btn.bgcolor = (theme.ACCENT_SOFT if active
                           else ft.Colors.TRANSPARENT)
            icon, label = btn.content.controls
            icon.color = (theme.ON_ACCENT_SOFT if active
                          else theme.TEXT_MUTED)
            label.color = (theme.ON_ACCENT_SOFT if active
                           else theme.TEXT_MUTED)
            label.weight = theme.W_SEMIBOLD if active else theme.W_REGULAR

    def _handle_mode(self, mode: str) -> None:
        if self.on_mode_cb:
            self.on_mode_cb(mode)

    # ==================== Ghost 开关 ====================

    def _sync_ghost_label(self) -> None:
        self.ghost_label.value = f"补全:{'开' if self.ghost_enabled else '关'}"
        color = theme.ACCENT if self.ghost_enabled else theme.TEXT_MUTED
        self.ghost_label.color = color
        self.ghost_icon.color = color

    def set_ghost(self, enabled: bool) -> None:
        self.ghost_enabled = enabled
        self._sync_ghost_label()
        if self.page:
            self.ghost_btn.update()

    def _handle_ghost(self, e=None) -> None:
        if self.on_ghost_cb:
            self.on_ghost_cb(not self.ghost_enabled)

    # ==================== 连接状态 ====================

    def set_state(self, state: str) -> None:
        self.lamp.bgcolor = theme.semantic_color(
            _STATE_SEMANTIC.get(state, "danger"))
        self.state_text.value = _STATE_TEXT.get(state, state)
        if self.page:
            self.state_text.update()

    def set_state_full(self, state: str) -> None:
        """整体刷新（初次构建时 update 尚不可用的场合由父级 update 覆盖）。"""
        self.lamp.bgcolor = theme.semantic_color(
            _STATE_SEMANTIC.get(state, "danger"))
        self.state_text.value = _STATE_TEXT.get(state, state)

    # ==================== 指标 ====================
    # 约定：先设属性，再刷新（Flet 0.86 构造期访问 page 会抛错）。

    def set_model(self, model: str) -> None:
        self.model_text.value = model or "未选择模型"
        if self.page:
            self.model_text.update()

    def set_speed(self, ttft: float | None, tok_per_s: float | None) -> None:
        parts = []
        if ttft is not None:
            parts.append(f"TTFT {ttft * 1000:.0f}ms")
        if tok_per_s is not None:
            parts.append(f"{tok_per_s:.0f} tok/s")
        self.speed_text.value = " · ".join(parts)
        if self.page:
            self.speed_text.update()

    def set_tokens(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.tokens_text.value = f"{prompt_tokens + completion_tokens} tok"
        if self.page:
            self.tokens_text.update()

    def set_words(self, count: int) -> None:
        self.words_text.value = f"{count} 字"
        if self.page:
            self.words_text.update()

    def set_saved(self, time_str: str) -> None:
        self.saved_text.value = f"已保存 {time_str}" if time_str else "未保存"
        if self.page:
            self.saved_text.update()

    def set_context(self, tiers: dict, budget: int) -> None:
        """上下文透视数据（6.2：缓存 x.xk/32k 形态）。"""
        used = tiers.get("total", 0)
        self.context_btn.visible = True
        self.context_btn.content.value = f"上下文 {used / 1000:.1f}k/{budget / 1000:.0f}k"
        self.context_btn.data = {"tiers": tiers, "budget": budget}
        if self.page:
            self.context_btn.update()

    def _handle_context(self, e=None) -> None:
        if self.on_context_cb:
            self.on_context_cb(self.context_btn.data)
