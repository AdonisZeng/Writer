"""伏笔监控（方案 6.2 左栏 / 5.4.5）。

数据源 canon_plot_lines；休眠超阈值（章差 > 配置值）高亮提醒，
根治百万字长篇「作者彻底遗忘 50 章前埋的线索」。纯 SQL 计算，零成本。
"""
import flet as ft


class PlotMonitor(ft.Column):
    def __init__(self, app):
        self.app = app
        self.list_view = ft.ListView(spacing=2, height=110)
        super().__init__(
            controls=[
                ft.Row([ft.Text("📜 伏笔监控", size=13,
                                weight=ft.FontWeight.W_600)],
                       spacing=4),
                self.list_view,
            ],
            spacing=6)

    def refresh(self, dormant: list[dict], active: list[dict]):
        self.list_view.controls = []
        if not active:
            self.list_view.controls.append(
                ft.Text("（暂无剧情线记录，定稿后自动登记）", size=11,
                        color=ft.Colors.OUTLINE))
        elif not dormant:
            self.list_view.controls.append(
                ft.Text(f"✓ {len(active)} 条剧情线均在推进", size=11,
                        color=ft.Colors.GREEN))
        for p in dormant:
            self.list_view.controls.append(
                ft.Container(
                    content=ft.Text(
                        f"『{p['name']}』已休眠 {p['dormant_chapters']} 章，"
                        f"建议近期安排回收或推进",
                        size=11, color=ft.Colors.AMBER, max_lines=2,
                        overflow=ft.TextOverflow.ELLIPSIS),
                    padding=6, border_radius=6,
                    bgcolor=ft.Colors.with_opacity(0.08, ft.Colors.AMBER)))
        if self.page:
            self.update()
