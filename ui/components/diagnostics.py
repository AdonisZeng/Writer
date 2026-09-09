"""Canon 诊断清单（方案 6.1-3 / 6.2 右栏，Linter 式而非阻断式）。

- 分三级展示：🔴 Error / 🟡 Warning / 🔵 Info，永不模态弹窗
- 诊断条目携带定位信息（quote + 段落序号）
- 操作：[定位] [忽略]；Error 级不可忽略（需作者修改）
"""
from typing import Callable, Optional

import flet as ft

from core.canon.validator import Issue

_SEV_COLORS = {"error": ft.Colors.RED, "warning": ft.Colors.AMBER,
               "info": ft.Colors.BLUE}
_SEV_DOTS = {"error": "🔴", "warning": "🟡", "info": "🔵"}


class DiagnosticsPanel(ft.Column):
    def __init__(self, app):
        self.app = app
        self._issues: list[Issue] = []
        self._dismissed: set[str] = set()

        self.header = ft.Row(
            [ft.Text("🛡️ Canon 诊断", size=13, weight=ft.FontWeight.W_600),
             ft.Container(expand=True),
             ft.IconButton(icon=ft.Icons.SEARCH, icon_size=16,
                           tooltip="扫描项目元数据",
                           on_click=self._handle_scan),
             ft.IconButton(icon=ft.Icons.PSYCHOLOGY_ALT, icon_size=16,
                           tooltip="语义审查（AI 对照正史排查矛盾）",
                           on_click=self._handle_review)],
            spacing=4)
        self.count_text = ft.Text("", size=11, color=ft.Colors.OUTLINE)
        self.list_view = ft.ListView(expand=True, spacing=6)

        super().__init__(
            controls=[self.header, self.count_text, self.list_view],
            spacing=6, expand=True)

    # ---------- 数据 ----------

    def set_issues(self, issues: list[Issue], dismissed: Optional[set] = None):
        self._issues = issues or []
        self._dismissed = dismissed or set()
        visible = [i for i in self._issues
                   if i.fingerprint not in self._dismissed]
        n_err = sum(1 for i in visible if i.severity == "error")
        n_warn = sum(1 for i in visible if i.severity == "warning")
        n_info = sum(1 for i in visible if i.severity == "info")
        self.count_text.value = f"🔴 {n_err} · 🟡 {n_warn} · 🔵 {n_info}"
        self.list_view.controls = [self._build_tile(i) for i in visible]
        if self.page:
            self.update()

    def _build_tile(self, issue: Issue) -> ft.Container:
        msg = ft.Text(issue.message, size=12, expand=True,
                      max_lines=4, overflow=ft.TextOverflow.ELLIPSIS)
        loc = ft.Container(expand=True)
        if issue.quote:
            loc_text = f"「{issue.quote[:24]}」"
            if issue.paragraph:
                loc_text += f"（段{issue.paragraph}）"
            loc.content = ft.Text(loc_text, size=11,
                                  color=ft.Colors.OUTLINE,
                                  max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        actions = []
        if issue.quote:
            actions.append(ft.TextButton("定位", icon=ft.Icons.MY_LOCATION,
                                     on_click=lambda e, i=issue:
                                     self.app.locate_issue(i)))
        if issue.severity != "error":
            actions.append(ft.TextButton("忽略", icon=ft.Icons.HIDE_SOURCE,
                                         on_click=lambda e, i=issue:
                                         self._handle_dismiss(i)))
        row = ft.Row(
            [ft.Text(_SEV_DOTS.get(issue.severity, "🔵"), size=12), msg],
            spacing=6)
        return ft.Container(
            content=ft.Column([row, loc,
                               ft.Row(actions, spacing=4)]
                              if actions or loc.content else
                              [row, loc],
                              spacing=2),
            padding=8, border_radius=8,
            bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.ON_SURFACE),
        )

    # ---------- 事件 ----------

    async def _handle_dismiss(self, issue: Issue) -> None:
        await self.app.dismiss_issue(issue)

    def _handle_scan(self, e=None) -> None:
        import asyncio
        asyncio.create_task(self.app.run_metadata_scan())

    def _handle_review(self, e=None) -> None:
        import asyncio
        asyncio.create_task(self.app.run_semantic_review())
