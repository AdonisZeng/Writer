"""章内节拍面板（P3 补齐，方案 7-③.5 可选模式 / 6.2 节拍流）。

细纲可拆 3~4 个节拍（起/承/转/合），逐节拍扩写：每拍 600~800 字，
避免一口气生成 3000 字导致中段水化；拍完成后可微调下一拍方向。
数据：chapters.beats JSON [{title, done}]

视觉：标题图标化；条目令牌化；空态组件化。
"""
import json
from typing import Callable

import flet as ft

from ui import theme


class BeatPanel(ft.Column):
    def __init__(self, app):
        self.app = app
        self.list_view = ft.Column(spacing=theme.SPACE_XS)
        self.new_beat = ft.TextField(hint_text="下一拍标题/方向（如：机关冲突）",
                                     dense=True, expand=True)
        self.gen_btn = ft.FilledButton(
            "生成下一节拍", icon=ft.Icons.NAVIGATE_NEXT,
            tooltip="只推演一拍 600~800 字，完成后可在预览区确认写入",
            on_click=self._handle_generate)
        self.empty = theme.empty_state(
            ft.Icons.MOVIE, "本章未拆节拍（可选）",
            "逐拍扩写可防止中段水化，每拍 600~800 字")
        add_btn = ft.IconButton(icon=ft.Icons.ADD, icon_size=theme.ICON_INLINE,
                                tooltip="添加节拍", on_click=self._handle_add)
        super().__init__(
            controls=[
                theme.section_header(ft.Icons.MOVIE, "章内节拍"),
                self.empty,
                self.list_view,
                ft.Row([self.new_beat, add_btn], spacing=theme.SPACE_XS),
                ft.Row([self.gen_btn]),
            ],
            spacing=theme.SPACE_SM)

    # ---------- 数据 ----------

    def refresh(self, beats_json: str) -> list[dict]:
        beats = self._parse(beats_json)
        self.list_view.controls = []
        for i, b in enumerate(beats):
            self.list_view.controls.append(self._build_tile(i, b))
        self.empty.visible = not beats
        self.gen_btn.disabled = not any(not b.get("done") for b in beats)
        if self.page:
            self.update()
        return beats

    def _parse(self, beats_json: str) -> list[dict]:
        try:
            data = json.loads(beats_json or "[]")
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    def _build_tile(self, index: int, beat: dict) -> ft.Row:
        return ft.Row([
            ft.Checkbox(value=bool(beat.get("done")),
                        on_change=lambda e, i=index:
                        self._handle_toggle(i, e.control.value)),
            ft.Text(beat.get("title", ""), size=theme.SIZE_SM, expand=True,
                    color=theme.TEXT,
                    max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
            ft.IconButton(icon=ft.Icons.DELETE_OUTLINE,
                          icon_size=theme.ICON_INLINE,
                          icon_color=theme.TEXT_MUTED,
                          tooltip="删除节拍",
                          on_click=lambda e, i=index: self._handle_delete(i)),
        ], spacing=theme.SPACE_XS)

    # ---------- 事件 ----------

    def _handle_add(self, e=None) -> None:
        title = (self.new_beat.value or "").strip()
        if not title:
            return
        self.new_beat.value = ""
        beats = self._mutate(lambda beats: beats.append(
            {"title": title, "done": False}))
        self.refresh(json.dumps(beats, ensure_ascii=False))
        self.app.save_beats(beats)

    def _handle_toggle(self, index: int, done: bool) -> None:
        def mutate(beats):
            if 0 <= index < len(beats):
                beats[index]["done"] = bool(done)
        beats = self._mutate(mutate)
        self.refresh(json.dumps(beats, ensure_ascii=False))
        self.app.save_beats(beats)

    def _handle_delete(self, index: int) -> None:
        def mutate(beats):
            if 0 <= index < len(beats):
                beats.pop(index)
        beats = self._mutate(mutate)
        self.refresh(json.dumps(beats, ensure_ascii=False))
        self.app.save_beats(beats)

    def _mutate(self, fn: Callable) -> list[dict]:
        beats = self._parse(self._current_beats_json())
        fn(beats)
        return beats

    def _current_beats_json(self) -> str:
        ch = self.app.current
        return (ch or {}).get("beats") or "[]"

    def _handle_generate(self, e=None) -> None:
        self.app.on_generate_next_beat()
