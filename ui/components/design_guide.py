"""起步引导面板（设计界面中栏）：把一句话想法逐步推进为完整作品框架。

四步：故事内核 → 世界观 → 结构大纲 → 人物。步骤条展示进度，
当前步高亮、已完成为打点；每个动作通过 view 回调驱动（UI 只做编排）。
「作者参与程度」下拉框调节分步引导时 AI 的提问频率（持久化到 config.json）。
"""
import asyncio

import flet as ft

from core import config
from core.design import GUIDE_STEPS, PARTICIPATION_LEVELS
from ui import theme

_STEP_ICONS = {
    "core": ft.Icons.LIGHTBULB,
    "world": ft.Icons.PUBLIC,
    "structure": ft.Icons.ACCOUNT_TREE,
    "cast": ft.Icons.GROUPS,
}


class DesignGuide(ft.Column):
    def __init__(self, view):
        self.view = view
        self.current_step = GUIDE_STEPS[0]["key"]
        self.steps_done: list[str] = []

        self.idea = ft.TextField(
            label="用一句话说说你的想法",
            multiline=True, min_lines=3, max_lines=7, shift_enter=True,
            hint_text="如：一个能听见亡者遗言的验尸官，被卷入一桩"
                      "指向皇室的连环命案。")
        self.save_hint = ft.Text("", size=theme.SIZE_XS,
                                 color=theme.semantic_color("success"))

        # ---- 作者参与程度（AI 提问频率，三档）----
        self.participation_dd = ft.Dropdown(
            value=config.get("design_participation", "high"), dense=True,
            width=250,
            options=[ft.DropdownOption(key=k, text=v["label"])
                     for k, v in PARTICIPATION_LEVELS.items()],
            on_select=lambda e: self._save_participation())

        step_row = ft.Row(spacing=theme.SPACE_SM,
                          run_spacing=theme.SPACE_SM, wrap=True)
        self._step_tiles: dict[str, ft.Container] = {}
        self._tile_parts: dict[str, tuple] = {}
        for st in GUIDE_STEPS:
            tile, parts = self._make_tile(st)
            self._step_tiles[st["key"]] = tile
            self._tile_parts[st["key"]] = parts
            step_row.controls.append(tile)

        self.cur_label = ft.Text("", size=theme.SIZE_LG,
                                 weight=theme.W_SEMIBOLD, color=theme.TEXT)
        self.cur_goal = ft.Text("", size=theme.SIZE_SM, color=theme.TEXT_MUTED)
        self.progress_text = ft.Text("", size=theme.SIZE_XS,
                                     color=theme.TEXT_FAINT)

        self.start_btn = ft.FilledButton(
            "保存并让 AI 开始引导", icon=ft.Icons.AUTO_AWESOME,
            on_click=lambda e: asyncio.create_task(self.view.guide_on_start()))
        self.done_btn = ft.OutlinedButton(
            "标记完成，进入下一步", icon=ft.Icons.CHECK,
            on_click=lambda e: asyncio.create_task(self.view.guide_on_done()))
        self.outline_btn = ft.OutlinedButton(
            "去生成结构大纲", icon=ft.Icons.ACCOUNT_TREE,
            visible=False, on_click=lambda e: self.view.guide_on_generate_outline())

        super().__init__(
            controls=[
                theme.section_header(ft.Icons.LIGHTBULB, "起步引导"),
                ft.Text("从一个念头开始，与 AI 按「内核 → 世界观 → 结构 → 人物」"
                        "四步逐步打磨；每一步的结论都可在右侧协作台采纳进设定库。",
                        size=theme.SIZE_XS, color=theme.TEXT_MUTED),
                self.idea,
                ft.Row([
                    ft.FilledButton("保存想法", icon=ft.Icons.SAVE,
                                    on_click=lambda e: asyncio.create_task(
                                        self.view.guide_on_save_idea())),
                    self.save_hint,
                ], spacing=theme.SPACE_MD),
                theme.divider(),
                step_row,
                theme.divider(),
                ft.Row([
                    ft.Text("作者参与程度", size=theme.SIZE_XS,
                            color=theme.TEXT_MUTED),
                    self.participation_dd,
                ], spacing=theme.SPACE_SM, wrap=True),
                self.cur_label,
                self.cur_goal,
                self.progress_text,
                ft.Row([self.start_btn, self.done_btn],
                       spacing=theme.SPACE_SM, wrap=True),
                ft.Row([self.outline_btn]),
            ],
            spacing=theme.SPACE_MD, scroll=ft.ScrollMode.AUTO, expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    def _make_tile(self, st: dict) -> tuple[ft.Container, tuple]:
        icon = ft.Icon(_STEP_ICONS.get(st["key"], ft.Icons.CIRCLE),
                       size=theme.ICON_INLINE, color=theme.TEXT_MUTED)
        label = ft.Text(st["label"], size=theme.SIZE_SM,
                        color=theme.TEXT_MUTED)
        check = ft.Icon(ft.Icons.CHECK_CIRCLE, size=theme.ICON_SMALL,
                        color=theme.semantic_color("success"), visible=False)
        tile = ft.Container(
            content=ft.Row([icon, label, check], spacing=theme.SPACE_XS,
                           tight=True),
            padding=ft.Padding(theme.SPACE_MD, theme.SPACE_SM,
                               theme.SPACE_MD, theme.SPACE_SM),
            border_radius=theme.RADIUS_SM, ink=True,
            on_click=lambda e, k=st["key"]: asyncio.create_task(
                self.view.guide_on_select_step(k)),
        )
        return tile, (icon, label, check)

    # ---------- 刷新 ----------

    def _save_participation(self) -> None:
        """参与程度改档即保存到 config.json（全局生效）。"""
        if self.participation_dd.value in PARTICIPATION_LEVELS:
            config.set_key("design_participation",
                           self.participation_dd.value)
            config.save_config()

    def refresh(self, progress: dict) -> None:
        self.idea.value = progress.get("idea", "") or ""
        self.current_step = progress.get("current_step") or GUIDE_STEPS[0]["key"]
        self.steps_done = list(progress.get("steps_done") or [])
        active_index = 0
        for i, st in enumerate(GUIDE_STEPS):
            key = st["key"]
            icon, label, check = self._tile_parts[key]
            active = key == self.current_step
            check.visible = key in self.steps_done
            self._step_tiles[key].bgcolor = theme.ACCENT_SOFT if active else None
            color = theme.ON_ACCENT_SOFT if active else theme.TEXT_MUTED
            icon.color = color
            label.color = color
            label.weight = theme.W_SEMIBOLD if active else theme.W_REGULAR
            if active:
                active_index = i
        st = GUIDE_STEPS[active_index]
        self.cur_label.value = (f"第 {active_index + 1} / {len(GUIDE_STEPS)} 步"
                                f" · {st['label']}")
        self.cur_goal.value = st["goal"]
        done_labels = [s["label"] for s in GUIDE_STEPS
                       if s["key"] in self.steps_done]
        self.progress_text.value = ("已完成：" + "、".join(done_labels)) \
            if done_labels else "尚未标记完成任何步骤"
        self.outline_btn.visible = (self.current_step == "structure")
        theme.safe_update(self)

    def flash_saved(self, msg: str = "✓ 想法已保存") -> None:
        self.save_hint.value = msg
        self.save_hint.color = theme.semantic_color("success")
        theme.safe_update(self.save_hint)
