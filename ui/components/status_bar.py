"""底部飞行仪表盘状态栏（方案 6.2）。

界面模式切换（✍️ 写作 / 🎨 设计）· 连接灯（🟢 就绪 / 🟡 推理中 / 🔴 离线）
· 模型名 · 速度（TTFT · tok/s）· 本会话用量 · 字数 · 自动保存时间。
Ghost ⚡ 开关与缓存命中为 P1/P2 交付。
"""
import flet as ft

_STATE_COLORS = {"ready": ft.Colors.GREEN, "busy": ft.Colors.AMBER,
                 "offline": ft.Colors.RED}
_STATE_TEXT = {"ready": "就绪", "busy": "推理中", "offline": "离线"}
_MODE_LABELS = {"write": "✍️ 写作", "design": "🎨 设计"}


class StatusBar(ft.Row):
    def __init__(self):
        self.lamp = ft.Container(width=10, height=10, border_radius=5,
                                 bgcolor=_STATE_COLORS["offline"],
                                 margin=ft.Margin(0, 0, 4, 0))
        self.state_text = ft.Text(_STATE_TEXT["offline"], size=12,
                                  weight=ft.FontWeight.W_500)
        self.model_text = ft.Text("未连接", size=12, color=ft.Colors.OUTLINE)
        self.speed_text = ft.Text("", size=12, color=ft.Colors.OUTLINE)
        self.tokens_text = ft.Text("", size=12, color=ft.Colors.OUTLINE)
        self.context_btn = ft.TextButton(
            content=ft.Text("", size=11, color=ft.Colors.OUTLINE),
            tooltip="上下文透视：查看本次生成的 Tier 分账（6.2）",
            visible=False, on_click=self._handle_context,
        )
        self.words_text = ft.Text("0 字", size=12, color=ft.Colors.OUTLINE)
        self.saved_text = ft.Text("未保存", size=12, color=ft.Colors.OUTLINE)
        self.on_context_cb = None
        self.on_ghost_cb = None
        self.ghost_enabled = False
        self.ghost_btn = ft.TextButton(
            content=ft.Text("⚡ 补全:关", size=11),
            tooltip="Ghost 行内补全开关 (Alt+G)；开启后停顿 1.5s 或 Alt+/ 触发",
            on_click=self._handle_ghost,
        )

        # ---- 界面模式切换（写作 / 设计，与模型名同一行）----
        self.mode = "write"
        self.on_mode_cb = None
        self._mode_btns: dict[str, ft.Container] = {}
        for key in ("write", "design"):
            btn = ft.Container(
                content=ft.Text(_MODE_LABELS[key], size=12),
                padding=ft.Padding(12, 4, 12, 4),
                border_radius=8, ink=True,
                tooltip="切换到设计界面（大纲 / 世界观 / 人物 + AI 协作）"
                if key == "design" else "切换到写作界面（章节正文创作）",
                on_click=lambda e, k=key: self._handle_mode(k),
            )
            self._mode_btns[key] = btn
        self.mode_switcher = ft.Container(
            content=ft.Row(list(self._mode_btns.values()), spacing=2,
                           tight=True),
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            border_radius=10, padding=ft.Padding(3, 3, 3, 3),
        )

        super().__init__(
            controls=[
                self.mode_switcher,
                ft.VerticalDivider(width=1, thickness=14,
                                   color=ft.Colors.OUTLINE_VARIANT),
                self.lamp, self.state_text,
                ft.VerticalDivider(width=1, thickness=14,
                                   color=ft.Colors.OUTLINE_VARIANT),
                self.model_text,
                ft.VerticalDivider(width=1, thickness=14,
                                   color=ft.Colors.OUTLINE_VARIANT),
                self.speed_text, self.tokens_text, self.context_btn,
                ft.Container(expand=True),
                self.ghost_btn,
                ft.VerticalDivider(width=1, thickness=14,
                                   color=ft.Colors.OUTLINE_VARIANT),
                self.words_text,
                ft.VerticalDivider(width=1, thickness=14,
                                   color=ft.Colors.OUTLINE_VARIANT),
                self.saved_text,
            ],
            spacing=10,
            margin=ft.Margin(16, 6, 16, 6),
        )
        self._sync_ghost_label()
        self._style_modes()

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
            btn.bgcolor = (ft.Colors.PRIMARY_CONTAINER if active
                           else ft.Colors.TRANSPARENT)
            btn.content.color = (ft.Colors.ON_PRIMARY_CONTAINER if active
                                 else ft.Colors.OUTLINE)
            btn.content.weight = (ft.FontWeight.W_600 if active
                                  else ft.FontWeight.NORMAL)

    def _handle_mode(self, mode: str) -> None:
        if self.on_mode_cb:
            self.on_mode_cb(mode)

    def _sync_ghost_label(self) -> None:
        self.ghost_btn.content.value = f"⚡ 补全:{'开' if self.ghost_enabled else '关'}"
        self.ghost_btn.content.color = (ft.Colors.PRIMARY
                                        if self.ghost_enabled
                                        else ft.Colors.OUTLINE)

    def set_ghost(self, enabled: bool) -> None:
        self.ghost_enabled = enabled
        self._sync_ghost_label()
        if self.page:
            self.ghost_btn.update()

    def _handle_ghost(self, e=None) -> None:
        if self.on_ghost_cb:
            self.on_ghost_cb(not self.ghost_enabled)

    def set_state(self, state: str) -> None:
        self.lamp.bgcolor = _STATE_COLORS.get(state, ft.Colors.RED)
        self.state_text.value = _STATE_TEXT.get(state, state)
        self.state_text.update()

    def set_state_full(self, state: str) -> None:
        """整体刷新（初次构建时 update 尚不可用的场合由父级 update 覆盖）。"""
        self.lamp.bgcolor = _STATE_COLORS.get(state, ft.Colors.RED)
        self.state_text.value = _STATE_TEXT.get(state, state)

    def set_model(self, model: str) -> None:
        self.model_text.value = model or "未选择模型"
        self.model_text.update()

    def set_speed(self, ttft: float | None, tok_per_s: float | None) -> None:
        parts = []
        if ttft is not None:
            parts.append(f"TTFT {ttft * 1000:.0f}ms")
        if tok_per_s is not None:
            parts.append(f"{tok_per_s:.0f} tok/s")
        self.speed_text.value = " · ".join(parts)
        self.speed_text.update()

    def set_tokens(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.tokens_text.value = f"{prompt_tokens + completion_tokens} tok"
        self.tokens_text.update()

    def set_words(self, count: int) -> None:
        self.words_text.value = f"{count} 字"
        self.words_text.update()

    def set_saved(self, time_str: str) -> None:
        self.saved_text.value = f"已保存 {time_str}" if time_str else "未保存"
        self.saved_text.update()

    def set_context(self, tiers: dict, budget: int) -> None:
        """上下文透视数据（6.2：缓存 x.xk/32k 形态）。"""
        used = tiers.get("total", 0)
        self.context_btn.visible = True
        self.context_btn.content.value = f"上下文 {used / 1000:.1f}k/{budget / 1000:.0f}k"
        self.context_btn.data = {"tiers": tiers, "budget": budget}
        self.context_btn.update()

    def _handle_context(self, e=None) -> None:
        if self.on_context_cb:
            self.on_context_cb(self.context_btn.data)
