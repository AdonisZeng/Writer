"""伏笔监控（方案 6.2 左栏 / 5.4.5）。

数据源 canon_plot_lines；休眠超阈值（章差 > 配置值）高亮提醒，
根治百万字长篇「作者彻底遗忘 50 章前埋的线索」。纯 SQL 计算，零成本。

视觉：标题图标化；空态/正常态组件化（不再是一行灰字）。
"""
import flet as ft

from ui import theme

_LIST_HEIGHT = 118


class PlotMonitor(ft.Column):
    def __init__(self, app):
        self.app = app
        self.list_view = ft.ListView(spacing=theme.SPACE_XXS,
                                     height=_LIST_HEIGHT)
        super().__init__(
            controls=[
                theme.section_header(ft.Icons.SIGNPOST, "伏笔监控"),
                self.list_view,
            ],
            spacing=theme.SPACE_SM)

    def refresh(self, dormant: list[dict], active: list[dict]):
        self.list_view.controls = []
        if not active and not dormant:
            self.list_view.controls.append(theme.empty_state(
                ft.Icons.HISTORY, "暂无剧情线记录", "定稿后自动登记",
                compact=True))
        elif not dormant:
            self.list_view.controls.append(ft.Row([
                ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE,
                        size=theme.ICON_INLINE,
                        color=theme.semantic_color("success")),
                ft.Text(f"{len(active)} 条剧情线均在推进",
                        size=theme.SIZE_XS,
                        color=theme.semantic_color("success")),
            ], spacing=theme.SPACE_XS))
        for p in dormant:
            self.list_view.controls.append(theme.tile_card(
                ft.Row([
                    ft.Icon(ft.Icons.HOURGLASS_EMPTY,
                            size=theme.ICON_SMALL,
                            color=theme.semantic_color("warning")),
                    ft.Text(
                        f"『{p['name']}』已休眠 {p['dormant_chapters']} 章，"
                        f"建议近期安排回收或推进",
                        size=theme.SIZE_XS,
                        color=theme.semantic_color("warning"),
                        max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                        expand=True),
                ], spacing=theme.SPACE_XS,
                    vertical_alignment=ft.CrossAxisAlignment.START),
                padding=theme.SPACE_XS + 2, radius=theme.RADIUS_XS,
                bgcolor=ft.Colors.with_opacity(
                    0.09, theme.semantic_color("warning"))))
        if self.page:
            self.update()
