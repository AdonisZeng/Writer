"""Canon 诊断清单（方案 6.1-3 / 6.2 右栏，Linter 式而非阻断式）。

- 分三级展示：Error / Warning / Info（彩色圆点，非 emoji），永不模态弹窗
- 诊断条目携带定位信息（quote + 段落序号）
- 操作：[定位] [忽略]；Error 级不可忽略
- 零问题 → 「全部通过」正向态；语义审查时内联加载指示
"""
from typing import Optional

import flet as ft

from core.canon.validator import Issue
from ui import theme

_DOT_SIZE = 8


class DiagnosticsPanel(ft.Column):
    def __init__(self, app):
        self.app = app
        self._issues: list[Issue] = []
        self._dismissed: set[str] = set()

        scan_btn = ft.IconButton(icon=ft.Icons.SEARCH,
                                 icon_size=theme.ICON_INLINE,
                                 tooltip="扫描项目元数据",
                                 on_click=self._handle_scan)
        review_btn = ft.IconButton(icon=ft.Icons.PSYCHOLOGY_ALT,
                                   icon_size=theme.ICON_INLINE,
                                   tooltip="语义审查（AI 对照正史排查矛盾）",
                                   on_click=self._handle_review)
        self.header = theme.section_header(
            ft.Icons.SHIELD, "Canon 诊断",
            trailing=ft.Row([scan_btn, review_btn],
                            spacing=theme.SPACE_XXS, tight=True))

        self.count_row = ft.Row(spacing=theme.SPACE_SM, visible=False)
        self.loading = ft.Row([
            theme.inline_loader(theme.ICON_SMALL),
            ft.Text("语义审查中…", size=theme.SIZE_XS, color=theme.TEXT_MUTED),
        ], spacing=theme.SPACE_XS, visible=False)
        self.error_row = ft.Container(visible=False)
        self.list_view = ft.ListView(expand=True, spacing=theme.SPACE_SM)

        super().__init__(
            controls=[self.header, self.count_row, self.loading,
                      self.error_row, self.list_view],
            spacing=theme.SPACE_SM, expand=True)

    # ---------- 状态 ----------

    def set_loading(self, loading: bool) -> None:
        self.loading.visible = bool(loading)
        if self.page:
            self.loading.update()

    def set_error(self, message: str) -> None:
        """审查失败时就地呈现错误，而不是只写进「生成日志」页签。"""
        self.error_row.content = theme.inline_error(message)
        self.error_row.visible = True
        if self.page:
            self.error_row.update()

    # ---------- 数据 ----------

    def set_issues(self, issues: list[Issue], dismissed: Optional[set] = None):
        self._issues = issues or []
        self._dismissed = dismissed or set()
        self.error_row.visible = False
        visible = [i for i in self._issues
                   if i.fingerprint not in self._dismissed]
        counts = {sev: sum(1 for i in visible if i.severity == sev)
                  for sev in theme.SEVERITY_ORDER}

        # 计数：彩色圆点 + 等宽数字（无 emoji）
        row_controls: list[ft.Control] = []
        for sev in theme.SEVERITY_ORDER:
            row_controls.append(theme.severity_dot(sev, size=_DOT_SIZE))
            row_controls.append(theme.metric_text(str(counts[sev]),
                                                  size=theme.SIZE_XS))
        self.count_row.controls = row_controls
        self.count_row.visible = bool(visible)

        if not visible:
            self.list_view.controls = [theme.empty_state(
                ft.Icons.VERIFIED, "未发现一致性问题",
                "可点上方放大镜重新扫描，或用 AI 语义审查对照正史")]
        else:
            self.list_view.controls = [self._build_tile(i) for i in visible]
        if self.page:
            self.update()

    def _build_tile(self, issue: Issue) -> ft.Container:
        is_error = issue.severity == "error"
        msg = ft.Text(issue.message, size=theme.SIZE_SM, expand=True,
                      color=theme.TEXT,
                      weight=theme.W_MEDIUM if is_error else theme.W_REGULAR,
                      max_lines=4, overflow=ft.TextOverflow.ELLIPSIS)
        loc = ft.Container(expand=True)
        if issue.quote:
            loc_text = f"「{issue.quote[:24]}」"
            if issue.paragraph:
                loc_text += f"（段{issue.paragraph}）"
            loc.content = theme.metric_text(
                loc_text, size=theme.SIZE_XS, color=theme.TEXT_FAINT)
        actions = []
        if issue.quote:
            actions.append(ft.TextButton("定位", icon=ft.Icons.MY_LOCATION,
                                         on_click=lambda e, i=issue:
                                         self.app.locate_issue(i)))
        if not is_error:
            actions.append(ft.TextButton("忽略", icon=ft.Icons.HIDE_SOURCE,
                                         on_click=lambda e, i=issue:
                                         self._handle_dismiss(i)))
        row = ft.Row(
            [theme.severity_dot(issue.severity, size=_DOT_SIZE), msg],
            spacing=theme.SPACE_SM, vertical_alignment=ft.CrossAxisAlignment.START)
        body: list[ft.Control] = [row]
        if loc.content:
            body.append(loc)
        if actions:
            body.append(ft.Row(actions, spacing=theme.SPACE_XXS))
        return theme.tile_card(ft.Column(body, spacing=theme.SPACE_XXS),
                               padding=theme.SPACE_SM)

    # ---------- 事件 ----------

    async def _handle_dismiss(self, issue: Issue) -> None:
        await self.app.dismiss_issue(issue)

    def _handle_scan(self, e=None) -> None:
        import asyncio
        asyncio.create_task(self.app.run_metadata_scan())

    def _handle_review(self, e=None) -> None:
        import asyncio
        asyncio.create_task(self.app.run_semantic_review())
