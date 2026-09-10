"""三栏工作台主视图（方案 6.2）：左章节树 / 中编辑画布 / 右细纲+生成+日志。

UI 总原则：作者在自己的文稿里指挥 AI；生成中不锁编辑区；
AI 产出先进预览区，作者确认后才写入正文（UI 负面清单 4）。
"""
import asyncio
import json
import os
import time
from typing import Optional

import flet as ft

from core import ai_service, canon, commands, config, db, file_manager, \
    paths, project, rag
from core.commands import batch_generate, export_book, finalize_chapter, \
    generate_draft, refine_draft, rollback, save_draft
from core.file_watcher import ChapterWatcher
from ui import shortcuts
from ui import theme
from ui.components.beat_panel import BeatPanel
from ui.components.cast_panel import CastPanel
from ui.components.chapter_tree import ChapterTree
from ui.components.diagnostics import DiagnosticsPanel
from ui.components.plot_monitor import PlotMonitor
from ui.components.resizer import VResizer
from ui.components.status_bar import StatusBar
from ui.views.editor import EditorView
from ui.views.design import DesignView
from ui.views.settings import SettingsView


class WriterApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.project: str = config.get("project", "")
        self.chapters: list[dict] = []
        self.current: Optional[dict] = None
        self.current_model: str = config.get("model", "")
        self.gen_task: Optional[asyncio.Task] = None
        self.cancel_event: Optional[asyncio.Event] = None
        self.generating = False
        self.session_tokens = 0
        self.left_collapsed = False
        self.right_collapsed = False
        self.mode = "write"          # write=写作界面 / design=设计界面

        self._build_ui()

    # ==================== UI 构建 ====================

    def _build_ui(self):
        page = self.page
        page.padding = 0
        page.spacing = 0
        page.title = "Writer"
        # 字体由 main.py 的 theme.register_fonts(page) 统一注册，此处不再清空

        # ---- 组件 ----
        self.status_bar = StatusBar()
        self.editor = EditorView(
            project_provider=lambda: self.project,
            on_words=self.status_bar.set_words,
            on_saved=self.status_bar.set_saved,
            on_dirty=lambda: None,
        )
        self.editor.on_stop_cb = self.on_generate_stop
        self.editor.on_accept_cb = self.on_generate_accept
        self.editor.on_discard_cb = self.on_generate_discard
        self.editor.on_regenerate_cb = self.on_generate_regenerate
        self.editor.on_pov_cb = self.on_pov_change
        self.editor.on_finalize_cb = self.on_finalize_click
        self.editor.on_rollback_cb = self.on_rollback_click

        self.tree = ChapterTree(
            on_select=self.select_chapter,
            on_add=self.show_add_chapter_dialog,
            on_rename=self.show_rename_dialog,
            on_delete=self.show_delete_dialog,
            on_reorder=self.on_reorder_chapters,
        )
        self.cast_panel = CastPanel(self)
        self.plot_monitor = PlotMonitor(self)
        self.diagnostics = DiagnosticsPanel(self)
        self.status_bar.on_context_cb = self.show_context_dialog

        # ---- 右栏：本章细纲 ----
        self.bp_title = ft.TextField(label="章节标题", dense=True)
        self.bp_role = ft.TextField(label="结构角色（如：开篇钩子）", dense=True)
        self.bp_purpose = ft.TextField(label="核心目的", dense=True,
                                       multiline=True, min_lines=2,
                                       max_lines=4)
        self.bp_events = ft.TextField(label="关键事件", dense=True,
                                      multiline=True, min_lines=2,
                                      max_lines=4)
        self.bp_characters = ft.TextField(label="出场角色（顿号分隔）",
                                          dense=True)
        self.bp_save_btn = ft.OutlinedButton("保存细纲",
                                             on_click=self.save_blueprint)
        self.beat_panel = BeatPanel(self)
        self.rerun_pp_btn = ft.OutlinedButton(
            "重跑定稿后处理", icon=ft.Icons.REPLAY, visible=False,
            tooltip="只补跑失败的后处理步骤（only_failed=True）",
            on_click=self.on_rerun_post_process)
        # ---- 右栏：生成 ----
        self.guidance = ft.TextField(
            label="作者本章指导（可选）",
            hint_text="微操指导：情节侧重、情绪基调、必须出现/回避的内容…",
            multiline=True, min_lines=2, max_lines=5, dense=True,
        )
        self.gen_btn = ft.FilledButton(
            "生成草稿", icon=ft.Icons.AUTO_AWESOME,
            on_click=self.on_generate_click,
        )
        # ---- 右栏：日志 ----
        self.log_view = ft.ListView(expand=True, spacing=theme.SPACE_XXS,
                                    auto_scroll=True)

        blueprint_section = ft.Column(
            controls=[
                theme.section_header(ft.Icons.EDIT_NOTE, "本章细纲"),
                self.bp_title, self.bp_role, self.bp_purpose,
                self.bp_events, self.bp_characters,
                ft.Row([self.bp_save_btn, self.rerun_pp_btn],
                       spacing=theme.SPACE_SM),
                theme.divider(),
                self.beat_panel,
            ],
            spacing=theme.SPACE_SM,
            scroll=ft.ScrollMode.AUTO, expand=True,
        )
        generate_section = ft.Column(
            controls=[
                theme.section_header(ft.Icons.AUTO_AWESOME, "AI 生成"),
                self.guidance,
                ft.Row([self.gen_btn]),
            ],
            spacing=theme.SPACE_SM,
        )
        log_section = ft.Column(
            controls=[
                self.log_view,
            ],
            spacing=theme.SPACE_SM, expand=True,
        )

        # ---- 右栏：统计（P2：用量聚合 + RAG 状态）----
        self.usage_view = ft.ListView(spacing=theme.SPACE_XS, expand=True)
        self.rag_status_text = ft.Text("RAG：未初始化", size=theme.SIZE_XS,
                                       color=theme.TEXT_MUTED)
        self.rag_rebuild_btn = ft.OutlinedButton(
            "重建知识库索引", icon=ft.Icons.MANAGE_SEARCH,
            tooltip="全量索引章节正文与 settings.md（需配置 rag_model）",
            on_click=self.on_rebuild_rag)
        self.rag_spinner = ft.Container(theme.inline_loader(14), visible=False)
        usage_section = ft.Column(
            controls=[self.rag_status_text,
                      ft.Row([self.rag_rebuild_btn, self.rag_spinner],
                             spacing=theme.SPACE_SM),
                      ft.Text("LLM 用量统计（按用途×模型）", size=theme.SIZE_SM,
                              weight=theme.W_SEMIBOLD, color=theme.TEXT_MUTED),
                      self.usage_view],
            spacing=theme.SPACE_SM, expand=True)

        # ---- 右栏自绘标签页（按钮行 + Stack visible 切换，控件常驻不重建）----
        self._right_tab_index = 0
        self._tab_titles = ["本章细纲", "Canon 诊断", "生成日志", "统计"]
        self.tab_title = ft.Text(self._tab_titles[0], size=theme.SIZE_SECTION,
                                 weight=theme.W_SEMIBOLD, color=theme.TEXT)
        self._tab_buttons = []
        for i, (label, icon) in enumerate(
                [("细纲", ft.Icons.EDIT_NOTE), ("诊断", ft.Icons.SHIELD),
                 ("日志", ft.Icons.TERMINAL), ("统计", ft.Icons.BAR_CHART)]):
            btn = ft.IconButton(
                icon=icon, icon_size=theme.ICON_ACTION, selected=i == 0,
                icon_color=theme.TEXT_MUTED,
                selected_icon_color=theme.ACCENT,
                tooltip=label, on_click=lambda e, idx=i: self._select_tab(idx))
            self._tab_buttons.append(btn)
        tab_header = ft.Row(
            [self.tab_title, ft.Container(expand=True)] + self._tab_buttons,
            spacing=theme.SPACE_XXS)

        bp_panel = ft.Container(
            content=ft.Column([blueprint_section, theme.divider(),
                               generate_section],
                              spacing=theme.SPACE_MD, expand=True,
                              scroll=ft.ScrollMode.AUTO),
            expand=True)
        diag_panel = ft.Container(content=self.diagnostics, expand=True)
        log_panel = ft.Container(content=self.log_view, expand=True)
        usage_panel = ft.Container(content=usage_section, expand=True)
        self._tab_views = [bp_panel, diag_panel, log_panel, usage_panel]

        right_tabs = ft.Column(
            controls=[tab_header, theme.divider(),
                      ft.Stack(controls=self._tab_views, expand=True)],
            spacing=theme.SPACE_XS, expand=True)
        for v in self._tab_views[1:]:
            v.visible = False

        self.right_panel = ft.Container(
            content=right_tabs,
            width=int(config.get("ui_right_width", 340)),
            padding=ft.Padding(theme.SPACE_MD, theme.SPACE_SM,
                               theme.SPACE_MD, theme.SPACE_SM),
            bgcolor=theme.PANEL_BG,
        )
        self.left_panel = ft.Container(
            content=ft.Column(
                [self.tree,
                 theme.divider(),
                 self.cast_panel,
                 self.plot_monitor],
                spacing=theme.SPACE_SM, expand=True),
            width=int(config.get("ui_left_width", 270)),
            padding=ft.Padding(theme.SPACE_SM, theme.SPACE_SM,
                               theme.SPACE_XS, theme.SPACE_SM),
            bgcolor=theme.PANEL_BG,
        )

        # ---- 可拖拽分隔条（拖动调整左/右栏宽度，宽度记忆进 config）----
        self.left_resizer = VResizer(
            get_width=lambda: self.left_panel.width,
            set_width=self._set_left_width,
            direction=1, min_width=200, max_width=520,
            on_drag_end=self._save_layout_widths)
        self.right_resizer = VResizer(
            get_width=lambda: self.right_panel.width,
            set_width=self._set_right_width,
            direction=-1, min_width=240, max_width=560,
            on_drag_end=self._save_layout_widths)

        # ---- 顶栏 ----
        self.app_title = ft.Row(
            [ft.Text("Writer", size=theme.SIZE_BRAND, weight=theme.W_BOLD,
                     color=theme.TEXT),
             ft.Container(width=theme.SPACE_SM),
             ft.Text(self.project or "未打开项目", size=theme.SIZE_SM,
                     color=theme.TEXT_MUTED)],
            spacing=0,
        )

        def _tool(icon, tip, handler) -> ft.IconButton:
            return ft.IconButton(icon=icon, icon_size=theme.ICON_ACTION,
                                 icon_color=theme.TEXT_MUTED, tooltip=tip,
                                 on_click=handler)

        top_bar = ft.Container(
            content=ft.Row(
                [
                    self.app_title,
                    ft.Container(expand=True),
                    _tool(ft.Icons.VIEW_SIDEBAR, "折叠/展开左栏",
                          self.toggle_left),
                    _tool(ft.Icons.VIEW_AGENDA, "折叠/展开右栏",
                          self.toggle_right),
                    _tool(ft.Icons.REFRESH, "刷新章节列表",
                          self.reload_chapters),
                    _tool(ft.Icons.AUTO_MODE, "批量生成（P3）",
                          self.show_batch_dialog),
                    _tool(ft.Icons.FILE_DOWNLOAD, "导出全本 TXT（P3）",
                          self.on_export),
                    _tool(ft.Icons.CENTER_FOCUS_STRONG, "专注模式 (F11)",
                          self.toggle_focus),
                    _tool(ft.Icons.SETTINGS, "设置", self.open_settings),
                ],
                spacing=theme.SPACE_XXS,
            ),
            padding=ft.Padding(theme.SPACE_LG, theme.SPACE_XS,
                               theme.SPACE_SM, theme.SPACE_XS),
            bgcolor=theme.CANVAS,
        )

        body = ft.Row(
            controls=[
                self.left_panel,
                self.left_resizer,
                ft.Container(
                    content=self.editor,
                    expand=True,
                    padding=ft.Padding(theme.SPACE_SM, theme.SPACE_SM,
                                       theme.SPACE_SM, theme.SPACE_SM),
                    alignment=ft.Alignment(0, 0),
                    bgcolor=theme.CANVAS,
                ),
                self.right_resizer,
                self.right_panel,
            ],
            spacing=0, expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        # ---- 写作 / 设计双界面（底部状态栏切换，控件常驻仅 visible 切换）----
        self.design_view = DesignView(self)
        self.write_layer = ft.Container(content=body, expand=True)
        self.body_stack = ft.Stack(
            controls=[self.write_layer, self.design_view], expand=True)

        status_bar_wrap = ft.Container(
            content=self.status_bar, bgcolor=theme.PANEL_BG,
            border=ft.Border.only(
                top=ft.BorderSide(1, theme.BORDER_COLOR)))

        self.main_layout = ft.Column(
            controls=[top_bar, theme.divider(), self.body_stack,
                      status_bar_wrap],
            spacing=0, expand=True,
        )

        # ---- 设置覆盖层 ----
        self.settings_view = SettingsView(self)

        self.root = ft.Stack(
            controls=[self.main_layout, self.settings_view], expand=True,
        )

    # ==================== 生命周期 ====================

    async def initialize(self) -> None:
        self.apply_theme()
        self.page.add(self.root)
        shortcuts.register(self.page, self.on_key)
        self.status_bar.set_model(self.current_model)
        self.status_bar.set_ghost(bool(config.get("ghost_enabled", False)))
        self.status_bar.on_ghost_cb = self.on_ghost_toggle
        self.status_bar.on_mode_cb = self._switch_mode
        self.status_bar.set_mode("write")
        self.editor.on_refine_cb = self.on_refine_request
        self.editor.on_log_cb = self.append_log
        self.watcher = ChapterWatcher(
            asyncio.get_running_loop(), self.on_external_files_changed)
        await self._open_project(self.project or "示例小说")

    def apply_theme(self) -> None:
        mode = config.get("theme_mode", "light")
        self.page.theme_mode = {
            "light": ft.ThemeMode.LIGHT,
            "dark": ft.ThemeMode.DARK,
            "system": ft.ThemeMode.SYSTEM,
        }.get(mode, ft.ThemeMode.LIGHT)
        # 暖中性编辑风：浅/深各构建一次；单一强调色由种子派生
        seed = theme.normalize_seed(config.get("color_seed"))
        registered = theme.fonts_map()
        self.page.theme = theme.build_theme(seed, dark=False,
                                            registered=registered)
        self.page.dark_theme = theme.build_theme(seed, dark=True,
                                                 registered=registered)
        if self.page.controls:
            self.page.update()

    def apply_typography(self) -> None:
        self.editor.apply_typography()

    async def _open_project(self, name: str) -> None:
        if not name:
            return
        self.project = name
        config.set_key("project", name)
        config.save_config()
        await project.init_project_db(name, title=name)
        await self.reload_chapters()
        self.app_title.controls[2].value = name
        # watchdog 热重载：监听本项目的 chapters/（方案 5.6）
        if getattr(self, "watcher", None):
            self.watcher.start(paths.chapters_dir(name))
        # 打开项目后自动选中第一章（侧栏面板随之初始化）
        if self.chapters and (not self.current or
                              self.current["id"] not in
                              {c["id"] for c in self.chapters}):
            self.tree.select(self.chapters[0]["id"])
            await self.select_chapter(self.chapters[0]["id"], force=True)
        if self.mode == "design":       # 切项目时同步刷新设计界面设定
            await self.design_view.load()
        if self.page:
            self.page.update()

    async def switch_project(self, name: str) -> None:
        if self.editor.dirty:
            await self.editor.save_now()
        self.editor.clear_chapter()
        self.current = None
        await self._open_project(name)

    async def refresh_all(self) -> None:
        await self.reload_chapters()
        if self.current:
            await self.select_chapter(self.current["id"], force=True)
        self.status_bar.set_model(self.current_model)

    # ==================== 章节管理 ====================

    async def reload_chapters(self, e=None) -> None:
        self.chapters = await db.list_chapters()
        self.tree.refresh(self.chapters)
        if self.current:
            self.current = next(
                (c for c in self.chapters if c["id"] == self.current["id"]),
                None)
            if self.current:
                self._fill_blueprint(self.current)

    async def select_chapter(self, chapter_id: str, force: bool = False,
                             e=None) -> None:
        if self.generating:
            self.append_log("⚠️ 生成中，请先停止再切换章节")
            return
        if self.editor.dirty:
            await self.editor.save_now()
        chapter = next((c for c in self.chapters if c["id"] == chapter_id),
                       None)
        if chapter is None:
            return
        if not force and self.current and self.current["id"] == chapter_id:
            return
        self.current = chapter
        text = await asyncio.to_thread(self._read_chapter_text, chapter)
        self.editor.set_chapter(chapter, text)
        self._fill_blueprint(chapter)
        self.status_bar.set_words(len(text))
        await self.refresh_side_panels(chapter)

    async def refresh_side_panels(self, chapter: dict) -> None:
        """章节切换/状态变化后同步左栏名册与伏笔、POV、动作按钮、诊断。"""
        # 登场名册
        try:
            names = json.loads(chapter.get("characters") or "[]")
        except (json.JSONDecodeError, TypeError):
            names = []
        await self.cast_panel.refresh(await db.list_characters(), names)
        # POV 徽标
        self.editor.update_pov_options(chapter, chapter.get("pov") or "")
        # 动作按钮
        latest = await finalize_chapter.get_latest_finalized()
        is_latest = bool(latest) and latest["id"] == chapter["id"]
        self.editor.update_action_buttons(chapter, is_latest)
        self.rerun_pp_btn.visible = chapter["status"] == "finalized"
        if self.page:
            self.rerun_pp_btn.update()
        # 伏笔监控
        threshold = config.get("plot_dormant_threshold", 25)
        dormant = await canon.store.get_dormant_plot_lines(
            chapter["number"], threshold)
        active = [p for p in await db.list_plot_lines()
                  if p["status"] == "active"]
        self.plot_monitor.refresh(dormant, active)
        # 诊断：确定性扫描（零成本）+ 已忽略过滤
        await self.run_metadata_scan(chapter=chapter, silent=True)

    async def run_metadata_scan(self, chapter: Optional[dict] = None,
                                silent: bool = False) -> None:
        """全项目元数据硬检查（零成本，诊断页数据源）。"""
        issues = await canon.scan_project_issues()
        chapter = chapter or self.current
        dismissed = await db.list_dismissed(chapter["id"]) if chapter else set()
        self.diagnostics.set_issues(issues, dismissed)
        if not silent and any(i.severity == "error" for i in issues):
            self.append_log("⚠️ 元数据扫描发现 error 级问题，见诊断页")

    def _merge_issues(self, issues: list) -> list:
        """按指纹去重（语义审查结果与扫描结果可能重叠）。"""
        seen, out = set(), []
        for i in issues:
            if i.fingerprint not in seen:
                seen.add(i.fingerprint)
                out.append(i)
        return out

    async def run_semantic_review(self, chapter: Optional[dict] = None) -> None:
        """语义审查（第二层）：AI 对照正史排查矛盾，仅诊断不修改。"""
        chapter = chapter or self.current
        if not chapter:
            self.append_log("⚠️ 请先选择章节再进行语义审查")
            return
        if not self.current_model:
            self.append_log("⚠️ 未选择模型，无法进行语义审查")
            return
        self.append_log("🛡️ 语义审查中（对照正史与 POV 情报）…")
        self.status_bar.set_state("busy")
        self.diagnostics.set_loading(True)
        try:
            content = await asyncio.to_thread(self._read_chapter_text, chapter)
            canon_ctx = await canon.build_canon_context(chapter["id"])
            pov_block = await canon.build_pov_block(chapter["id"])
            issues = await canon.reviewer.semantic_review(
                self.project, chapter["id"], content, canon_ctx, pov_block,
                model=self.current_model)
            deterministic = await canon.build_deterministic_issues(chapter)
            dismissed = await db.list_dismissed(chapter["id"])
            self.diagnostics.set_issues(
                self._merge_issues(deterministic + issues), dismissed)
            self.append_log(f"✓ 语义审查完成：{len(issues)} 条诊断"
                            f"（可逐条「忽略」，绝不机械改稿）")
        except Exception as ex:
            self.append_log(f"✗ 语义审查失败：{ex}")
            self.diagnostics.set_error(f"语义审查失败：{ex}")
        finally:
            self.diagnostics.set_loading(False)
            self.status_bar.set_state("ready")

    async def dismiss_issue(self, issue) -> None:
        if self.current:
            await db.dismiss_diagnostic(self.current["id"],
                                        issue.fingerprint)
            dismissed = await db.list_dismissed(self.current["id"])
            self.diagnostics.set_issues(self.diagnostics._issues, dismissed)

    async def locate_issue(self, issue) -> None:
        """定位：切到对应章并把光标放到 quote 处。"""
        if issue.chapter_id and self.current and \
                issue.chapter_id != self.current["id"]:
            await self.select_chapter(issue.chapter_id, force=True)
        if not issue.quote:
            self.append_log(f"ℹ️ {issue.code}：章节级问题，无行内定位")
            return
        text = self.editor.text_field.value or ""
        pos = text.find(issue.quote)
        if pos < 0:
            self.append_log(f"ℹ️ 未在正文中找到「{issue.quote[:20]}」"
                            f"（可能已被修改）")
            return
        end = pos + len(issue.quote)
        self.editor.text_field.selection = (pos, end)
        self.editor.text_field.focus()
        if self.page:
            self.editor.text_field.update()

    async def on_character_saved(self, name: str, **fields) -> None:
        await db.upsert_character(name, **fields)
        self.append_log(f"✓ 已登记角色：{name}")
        if self.current:
            await self.refresh_side_panels(self.current)

    def show_dialog_text(self, title: str, content: str) -> None:
        self.page.show_dialog(ft.AlertDialog(
            modal=False, title=ft.Text(title, size=theme.SIZE_LG,
                                       weight=theme.W_SEMIBOLD),
            content=ft.Text(content, size=theme.SIZE_SM, selectable=True,
                            color=theme.TEXT),
            actions=[ft.TextButton("关闭",
                                   on_click=lambda e: self.page.pop_dialog())],
        ))

    def show_context_dialog(self, data: Optional[dict]) -> None:
        """上下文透视抽屉（6.2：Tier 分账堆叠条）。"""
        if not data:
            return
        tiers, budget = data["tiers"], data["budget"]

        def bar(label: str, value: int, color: str) -> ft.Column:
            ratio = min(1.0, value / max(1, budget))
            return ft.Column([
                ft.Row([ft.Text(label, size=theme.SIZE_SM, color=theme.TEXT),
                        ft.Container(expand=True),
                        theme.metric_text(f"{value} tok",
                                          color=theme.TEXT_FAINT)]),
                ft.ProgressBar(value=ratio, color=color,
                               bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                               bar_height=8),
            ], spacing=theme.SPACE_XS)

        content = ft.Column([
            bar("Tier 1 · 全书静态（system：文风/世界观/禁忌）",
                tiers.get("tier1", 0), theme.ACCENT),
            bar("Tier 2 · 人物卡（半静态）",
                tiers.get("tier2", 0), theme.semantic_color("info")),
            bar("Tier 3 · 章级动态（时间线/Canon/细纲）",
                tiers.get("tier3", 0), theme.semantic_color("warning")),
            theme.divider(),
            ft.Row([ft.Text("合计", size=theme.SIZE_SM,
                            weight=theme.W_SEMIBOLD, color=theme.TEXT),
                    ft.Container(expand=True),
                    theme.metric_text(f"{tiers.get('total', 0)} / {budget} tok",
                                      size=theme.SIZE_SM, color=theme.TEXT)]),
            ft.Text("缓存策略：跨章续写 Tier1/2 前缀命中，"
                    "同章重试/精修可全量命中", size=theme.SIZE_XS,
                    color=theme.TEXT_FAINT),
        ], spacing=theme.SPACE_MD, tight=True, width=420)
        self.page.show_dialog(ft.AlertDialog(
            modal=False, title=ft.Text("上下文透视", size=theme.SIZE_LG,
                                       weight=theme.W_SEMIBOLD),
            content=content,
            actions=[ft.TextButton("关闭",
                                   on_click=lambda e: self.page.pop_dialog())],
        ))

    async def on_pov_change(self, pov_name: str) -> None:
        if not self.current:
            return
        await db.update_chapter(self.current["id"], pov=pov_name)
        self.current = await db.get_chapter(self.current["id"])
        self.editor.update_pov_options(self.current, pov_name)
        self.append_log(f"✓ 本章视角设为：{pov_name or '默认'}"
                        + ("（生成时将注入该角色已知信息边界）" if pov_name else ""))

    async def on_finalize_click(self) -> None:
        if not self.current or self.generating:
            return
        if self.editor.dirty:
            await self.editor.save_now()
        chapter = self.current

        async def confirm(ev=None):
            self.page.pop_dialog()
            self.status_bar.set_state("busy")
            self.gen_btn.disabled = True
            if self.page:
                self.page.update()
            result = await finalize_chapter.finalize(
                self.project, chapter["id"], self.current_model,
                on_log=self.append_log)
            self.gen_btn.disabled = False
            self.status_bar.set_state("ready")
            if result["verdict"] == "BLOCK":
                await self.select_chapter(chapter["id"], force=True)
                self.append_log("⛔ 定稿被 Canon Gate 阻断（见诊断页 error 项）")
                return
            await self.reload_chapters()
            await self.select_chapter(chapter["id"], force=True)
            if result["post"] and not result["post"]["ok"]:
                self.append_log("⚠️ 后处理部分失败：可在日志重跑定稿后处理")

        async def cancel(ev=None):
            self.page.pop_dialog()

        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("定稿本章"),
            content=ft.Text(
                f"将第{chapter['number']}章《{chapter['title']}》置为 finalized？\n"
                "流程：Canon Gate → 状态变更 → 抽取写回（摘要/时间线/伏笔/角色状态）"
                " → .txt 投影。", size=theme.SIZE_SM),
            actions=[ft.TextButton("取消", on_click=cancel),
                     ft.FilledButton("开始定稿", on_click=confirm)],
        ))

    async def on_rollback_click(self) -> None:
        if not self.current:
            return
        chapter = self.current

        async def confirm(ev=None):
            self.page.pop_dialog()
            self.status_bar.set_state("busy")
            result = await rollback.un_finalize(self.project,
                                                chapter["id"])
            self.status_bar.set_state("ready")
            self.append_log(("✓ " if result["ok"] else "✗ ")
                            + result["message"])
            if result["ok"]:
                self.append_log(f"  回滚明细：{result['stats']}")
            await self.reload_chapters()
            await self.select_chapter(chapter["id"], force=True)

        async def cancel(ev=None):
            self.page.pop_dialog()

        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("逆向回滚"),
            content=ft.Text(
                f"将第{chapter['number']}章《{chapter['title']}》从 finalized "
                f"回滚为 revised？\nCanon 写回将按快照还原（时间线/摘要/角色状态/"
                f"伏笔/本章新登场角色），正文保留，当前版自动归档。",
                size=theme.SIZE_SM),
            actions=[ft.TextButton("取消", on_click=cancel),
                     ft.FilledButton("确认回滚", on_click=confirm)],
        ))

    async def on_rerun_post_process(self, e=None) -> None:
        """「重跑定稿后处理」入口：only_failed=True 只补跑失败步骤（5.5）。"""
        if not self.current or self.current["status"] != "finalized":
            return
        chapter = self.current
        self.status_bar.set_state("busy")
        try:
            content = await asyncio.to_thread(self._read_chapter_text,
                                              chapter)
            result = await finalize_chapter.run_post_process(
                self.project, chapter, content, self.current_model,
                only_failed=True, on_log=self.append_log)
            self.append_log("✓ 后处理补跑完成" if result["ok"]
                            else "⚠️ 后处理仍有失败步骤")
        except Exception as ex:
            self.append_log(f"✗ 补跑失败：{ex}")
        finally:
            self.status_bar.set_state("ready")

    def _read_chapter_text(self, chapter: dict) -> str:
        path = file_manager.find_chapter_file(self.project,
                                              chapter["number"],
                                              chapter["title"])
        return file_manager.read_text(path)

    def _fill_blueprint(self, chapter: dict) -> None:
        try:
            chars = ", ".join(json.loads(chapter.get("characters") or "[]"))
        except (json.JSONDecodeError, TypeError):
            chars = ""
        self.bp_title.value = chapter["title"]
        self.bp_role.value = chapter["role"] or ""
        self.bp_purpose.value = chapter["purpose"] or ""
        self.bp_events.value = chapter["key_events"] or ""
        self.bp_characters.value = chars
        if self.page:
            self.bp_title.update(), self.bp_role.update()
            self.bp_purpose.update(), self.bp_events.update()
            self.bp_characters.update()
        # 节拍面板（可选模式，P3）
        self.beat_panel.refresh(chapter.get("beats") or "[]")

    async def save_blueprint(self, e=None) -> None:
        if not self.current:
            return
        if self.generating:
            self.append_log("⚠️ 生成中，稍后再保存细纲")
            return
        await commands.chapters.update_blueprint(
            self.project, self.current["id"],
            title=self.bp_title.value.strip() or self.current["title"],
            role=self.bp_role.value.strip(),
            purpose=self.bp_purpose.value.strip(),
            key_events=self.bp_events.value.strip(),
            characters=commands.chapters.parse_characters(
                self.bp_characters.value),
        )
        self.append_log("✓ 细纲已保存（outline.md 已同步）")
        await self.reload_chapters()
        cur = next((c for c in self.chapters
                    if c["id"] == self.current["id"]), None)
        if cur:
            self.current = cur
            self.editor.current_chapter = cur
            self.editor.breadcrumb.value = \
                f"第{cur['number']}章 {cur['title']}"
            if self.page:
                self.editor.breadcrumb.update()

    # ==================== 章节对话框 ====================

    def show_add_chapter_dialog(self, e=None) -> None:
        title = ft.TextField(label="章节标题", autofocus=True)
        role = ft.TextField(label="结构角色（可选）")
        purpose = ft.TextField(label="核心目的（可选）", multiline=True,
                               min_lines=2)

        async def confirm(ev=None):
            name = (title.value or "").strip()
            if not name:
                title.error_text = "标题不能为空"
                self.page.update()
                return
            self.page.pop_dialog()
            ch = await commands.chapters.create_chapter(
                self.project, title=name, role=role.value.strip(),
                purpose=purpose.value.strip())
            self.append_log(f"✓ 新建第{ch['number']}章 {ch['title']}")
            await self.reload_chapters()
            await self.select_chapter(ch["id"], force=True)

        async def cancel(ev=None):
            self.page.pop_dialog()

        dlg = ft.AlertDialog(
            modal=True, title=ft.Text("新建章节"),
            content=ft.Column([title, role, purpose],
                              spacing=theme.SPACE_MD,
                              tight=True, width=360),
            actions=[
                ft.TextButton("取消", on_click=cancel),
                ft.FilledButton("创建", on_click=confirm),
            ],
        )
        self.page.show_dialog(dlg)

    def show_rename_dialog(self, chapter_id: str) -> None:
        chapter = next((c for c in self.chapters if c["id"] == chapter_id),
                       None)
        if not chapter:
            return
        field = ft.TextField(label="新标题", value=chapter["title"],
                             autofocus=True)

        async def confirm(ev=None):
            new_title = (field.value or "").strip()
            if not new_title:
                field.error_text = "标题不能为空"
                self.page.update()
                return
            self.page.pop_dialog()
            await commands.chapters.rename_chapter(self.project, chapter_id,
                                                   new_title)
            self.append_log(f"✓ 已重命名为《{new_title}》")
            await self.reload_chapters()
            if self.current and self.current["id"] == chapter_id:
                cur = next((c for c in self.chapters
                            if c["id"] == chapter_id), None)
                if cur:
                    self.current = cur
                    self.editor.current_chapter = cur
                    self.editor.breadcrumb.value = \
                        f"第{cur['number']}章 {cur['title']}"
                    if self.page:
                        self.editor.breadcrumb.update()

        async def cancel(ev=None):
            self.page.pop_dialog()

        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("重命名章节"),
            content=ft.Column([field], spacing=theme.SPACE_MD, tight=True,
                              width=360),
            actions=[ft.TextButton("取消", on_click=cancel),
                     ft.FilledButton("确定", on_click=confirm)],
        ))

    def show_delete_dialog(self, chapter_id: str) -> None:
        chapter = next((c for c in self.chapters if c["id"] == chapter_id),
                       None)
        if not chapter:
            return

        async def confirm(ev=None):
            self.page.pop_dialog()
            await commands.chapters.delete_chapter(self.project, chapter_id)
            self.append_log(f"✓ 已删除第{chapter['number']}章 "
                            f"{chapter['title']}")
            if self.current and self.current["id"] == chapter_id:
                self.current = None
                self.editor.clear_chapter()
            await self.reload_chapters()

        async def cancel(ev=None):
            self.page.pop_dialog()

        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("删除章节"),
            content=ft.Text(f"确定删除「第{chapter['number']}章 "
                            f"{chapter['title']}」吗？\n正文文件与草稿版本记录将一并删除。"),
            actions=[ft.TextButton("取消", on_click=cancel),
                     ft.FilledButton("删除", on_click=confirm,
                                     color="#FFFFFF",
                                     bgcolor=theme.semantic_color("danger"))],
        ))

    # ==================== 生成流程 ====================

    async def on_generate_click(self, e=None) -> None:
        if self.generating:
            self.append_log("⚠️ 已有生成任务进行中")
            return
        if not self.current:
            self.page.show_dialog(ft.SnackBar(ft.Text("请先选择或新建一个章节")))
            return
        if not self.current_model:
            self.append_log("⚠️ 尚未选择模型：请到设置页刷新并选择模型")
            self.page.show_dialog(
                ft.SnackBar(ft.Text("请先在设置页选择模型（LM Studio 需开启 Server）")))
            return
        await self.editor.save_now()
        self.current = next((c for c in self.chapters
                             if c["id"] == self.current["id"]), self.current)
        self.cancel_event = asyncio.Event()
        self.generating = True
        self.gen_btn.disabled = True
        self.editor.enter_preview_mode()
        self.status_bar.set_state("busy")
        self.append_log(f"▶ 开始生成第{self.current['number']}章草稿…")
        if self.page:
            self.page.update()
        self.gen_task = asyncio.create_task(
            self._run_generation(self.current))

    async def _run_generation(self, chapter: dict) -> None:
        try:
            result = await generate_draft.execute(
                project=self.project,
                chapter=chapter,
                user_guidance=self.guidance.value or "",
                model=self.current_model,
                on_chunk=self.editor.append_gen_chunk,
                cancel_event=self.cancel_event,
                on_log=self.append_log,
            )
            stats = result["stats"]
            self.editor.show_review(stats.text)
            duration = stats.duration or 1e-6
            if stats.usage is not None:
                comp = getattr(stats.usage, "completion_tokens", 0) or 0
                prompt = getattr(stats.usage, "prompt_tokens", 0) or 0
            else:
                prompt, comp = 0, int(len(stats.text) / 1.5)
            self.session_tokens += prompt + comp
            self.status_bar.set_speed(stats.ttft,
                                      comp / duration if comp else None)
            self.status_bar.set_tokens(0, self.session_tokens)
            self.status_bar.set_context(result["tiers"], result["budget"])
            self.status_bar.set_state("ready")
            self.append_log(f"✓ 生成完成：{len(stats.text)} 字，"
                            f"请审阅后选择「写入正文」或「丢弃」")
        except ai_service.GenerationCancelled as c:
            self.editor.show_review(c.partial)
            self.status_bar.set_state("ready")
            self.append_log("■ 已停止：半成品保留在预览区，可采纳或丢弃")
        except Exception as ex:
            self.editor.enter_edit_mode()
            self.status_bar.set_state("offline")
            self.append_log(f"✗ 生成失败：{ex}")
            self.page.show_dialog(ft.SnackBar(
                ft.Text(f"生成失败：{ex}")))
        finally:
            self.generating = False
            self.gen_btn.disabled = False
            if self.page:
                self.page.update()

    def on_generate_stop(self) -> None:
        if self.cancel_event:
            self.cancel_event.set()

    async def on_generate_accept(self, text: str) -> None:
        if not self.current or not text.strip():
            return
        result = await save_draft.execute(self.project, self.current, text)
        self.append_log(f"✓ 已写入正文（v{result['version']}），状态：草稿")
        self.current = await db.get_chapter(self.current["id"])
        text_on_disk = await asyncio.to_thread(self._read_chapter_text,
                                               self.current)
        self.editor.set_chapter(self.current, text_on_disk)
        self.tree.selected_id = self.current["id"]
        await self.reload_chapters()
        self.tree.select(self.current["id"])
        await self.refresh_side_panels(self.current)
        self.status_bar.set_state("ready")
        # 生成后自动跑一次确定性检查（零成本），语义审查可由诊断页手动触发
        await self.run_metadata_scan(self.current)

    async def on_generate_discard(self) -> None:
        if self.current:
            text = await asyncio.to_thread(self._read_chapter_text,
                                           self.current)
            self.editor.set_chapter(self.current, text)
        self.status_bar.set_state("ready")
        self.append_log("■ 已丢弃本次生成内容")

    async def on_generate_regenerate(self) -> None:
        await self.on_generate_click()

    # ==================== 全局交互 ====================

    def on_key(self, e) -> None:
        key = getattr(e, "key", "")
        ctrl = getattr(e, "ctrl", False)
        alt = getattr(e, "alt", False)
        # 设计界面：仅处理 Esc（关设置 / 停 AI 协作）与 Ctrl+S（存当前板块）
        if self.mode == "design":
            if key == "Escape":
                if self.settings_view.visible:
                    self.settings_view.hide()
                elif self.design_view.is_chatting():
                    self.design_view.stop_chat()
            elif key.lower() == "s" and ctrl and not alt:
                asyncio.create_task(self.design_view.save_current())
            return
        # Ghost 优先消费 Enter / Esc（候选可见时，方案 6.3）
        if self.editor.ghost.on_key(e):
            return
        if key == "Escape":
            if self.generating:
                self.on_generate_stop()
            elif self.settings_view.visible:
                self.settings_view.hide()
            elif self.editor.k_pill.visible:
                self.editor._hide_k_pill()
        elif key.lower() == "s" and ctrl and not alt:
            asyncio.create_task(self._ctrl_s())
        elif key.lower() == "k" and ctrl and not alt:
            if not self.generating:
                self.editor.show_k_pill()
        elif key.lower() == "g" and alt and not ctrl:
            self.on_ghost_toggle(not self.editor.ghost.enabled)
        elif key == "/" and alt and not ctrl:
            self.editor.ghost.manual_trigger()
        elif key == "F11":
            self.toggle_focus()

    async def _ctrl_s(self) -> None:
        path = await self.editor.save_now()
        if path:
            self.append_log("✓ 已保存（Ctrl+S）")

    async def on_reorder_chapters(self, drag_id: str, target_id: str) -> None:
        """章节树拖拽重排（P2）。"""
        result = await commands.chapters.move_chapter(self.project, drag_id,
                                                      target_id)
        if result["ok"]:
            self.append_log(f"✓ 拖拽重排：{result['message']}")
        else:
            self.append_log(f"ℹ️ {result['message']}")
        await self.reload_chapters()
        self.tree.select(drag_id)
        if self.current and self.current["id"] == drag_id:
            self.current = next((c for c in self.chapters
                                 if c["id"] == drag_id), None)
            if self.current:
                self.editor.current_chapter = self.current
                self.editor.breadcrumb.value = \
                    f"第{self.current['number']}章 {self.current['title']}"
                if self.page:
                    self.editor.breadcrumb.update()

    def on_ghost_toggle(self, enabled: bool) -> None:
        """状态栏 ⚡ / Alt+G：Ghost 开关（回写 config 记忆）。"""
        self.editor.ghost.set_enabled(enabled)
        self.status_bar.set_ghost(enabled)
        self.append_log(("⚡ 行内补全已开启（停顿 1.5s 或 Alt+/ 触发）"
                         if enabled else "⚡ 行内补全已关闭"))

    async def on_refine_request(self, instruction: str,
                                sel: tuple[int, int]) -> None:
        """Ctrl+K 精修：产出进 Diff 对比卡，绝不直接覆盖。"""
        if not self.current or self.generating:
            return
        if not self.current_model:
            self.append_log("⚠️ 未选择模型，无法精修")
            return
        if self.editor.dirty:
            await self.editor.save_now()

        def runner(instruction_, old, before, after, full):
            self.gen_task = asyncio.create_task(self._run_refine(
                instruction_, old, before, after, full))

        self.editor.start_refine(instruction, sel, runner)

    async def _run_refine(self, instruction: str, old: str, before: str,
                          after: str, full: bool) -> None:
        try:
            self.status_bar.set_state("busy")
            new_text = await refine_draft.refine(
                self.project, old, instruction, model=self.current_model,
                full_context=full, context_before=before,
                context_after=after)
            self.status_bar.set_state("ready")
            self.editor.show_diff(old, new_text)
            self.append_log(f"✓ 精修完成：请逐块采纳（不允许一键覆盖）")
        except Exception as ex:
            self.status_bar.set_state("ready")
            self.editor.show_hint(f"精修失败：{ex}", error=True)
            self.append_log(f"✗ 精修失败：{ex}")

    async def on_rebuild_rag(self, e=None) -> None:
        """全量重建 RAG 知识库索引（统计页签）。"""
        if not rag.rag_ready():
            self.append_log("✗ sqlite-vec 不可用（pip install sqlite-vec）")
            self.rag_status_text.value = "RAG：sqlite-vec 不可用"
            self.rag_status_text.color = theme.semantic_color("danger")
            if self.page:
                self.rag_status_text.update()
            return
        self.rag_status_text.value = "RAG：索引中…"
        self.rag_status_text.color = theme.TEXT_MUTED
        self.rag_spinner.visible = True
        self.rag_rebuild_btn.disabled = True
        if self.page:
            self.update()
        try:
            result = await rag.rebuild_index(self.project)
            if result.get("ok"):
                self.append_log(f"✓ 知识库重建完成：{result['chunks']} 块")
            else:
                self.append_log(f"⚠️ 知识库重建失败：{result.get('reason')}")
                self.rag_status_text.value = \
                    f"RAG：重建失败（{result.get('reason')}）"
                self.rag_status_text.color = theme.semantic_color("danger")
        except Exception as ex:
            self.append_log(f"⚠️ 知识库重建失败：{ex}")
            self.rag_status_text.value = f"RAG：重建失败（{ex}）"
            self.rag_status_text.color = theme.semantic_color("danger")
        finally:
            self.rag_spinner.visible = False
            self.rag_rebuild_btn.disabled = False
        await self.refresh_usage()

    async def refresh_usage(self) -> None:
        """统计页签：用量聚合 + RAG 状态。"""
        rows = await db.usage_by_purpose()
        self.usage_view.controls = []
        total_cost = 0.0
        for r in rows:
            total_cost += r["cost"]
            metrics = (f"{r['calls']} 次 · ↑{r['prompt_tokens']} tok "
                       f"↓{r['completion_tokens']} tok · 均 {r['avg_ms']:.0f}ms"
                       + (f" · ¥{r['cost']:.4f}" if r["cost"] else ""))
            self.usage_view.controls.append(theme.tile_card(ft.Column([
                ft.Row([
                    ft.Text(r["purpose"], size=theme.SIZE_SM,
                            weight=theme.W_SEMIBOLD, color=theme.TEXT),
                    ft.Container(expand=True),
                    ft.Text(f"{r['model'][:28]}", size=theme.SIZE_XXS,
                            color=theme.TEXT_FAINT, max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS)]),
                theme.metric_text(metrics, size=theme.SIZE_XS),
            ], spacing=theme.SPACE_XXS)))
        if not rows:
            self.usage_view.controls.append(theme.empty_state(
                ft.Icons.BAR_CHART, "暂无调用记录",
                "开始生成后，这里会按用途×模型汇总用量"))
        stats = await rag.index_stats()
        if stats.get("ready"):
            self.rag_status_text.value = (
                f"RAG：已索引 {stats.get('chunks', 0)} 块"
                + (f" · 模型 {stats.get('model', '')}" if stats.get("model")
                   else " · 尚未索引"))
        else:
            self.rag_status_text.value = "RAG：sqlite-vec 不可用"
        if total_cost:
            self.usage_view.controls.insert(0, theme.metric_text(
                f"累计费用估算：¥{total_cost:.4f}", size=theme.SIZE_SM,
                color=theme.TEXT))
        if self.page:
            self.usage_view.update(), self.rag_status_text.update()

    def on_external_files_changed(self, changed_paths: set[str]) -> None:
        """watchdog 回调（事件循环内）：外部修改提示（方案 5.6）。"""
        names = ", ".join(os.path.basename(p) for p in
                          sorted(changed_paths)[:3])
        self.append_log(f"⚠️ 检测到外部文件修改：{names}")
        # 未脏且是当前章 → 自动同步加载；否则仅提示（点保存前不会被覆盖）
        if self.current and not self.editor.dirty:
            for p in changed_paths:
                if os.path.basename(p).startswith(
                        f"第{self.current['number']}章 "):
                    text = file_manager.read_text(p)
                    self.editor.text_field.value = text
                    self.editor.word_label.value = f"{len(text)} 字"
                    if self.page:
                        self.editor.text_field.update()
                        self.editor.word_label.update()
                    self.append_log("✓ 已同步加载外部修改（当前章无未保存改动）")
                    break
        else:
            self.page.show_dialog(ft.SnackBar(ft.Text(
                "检测到外部文件修改；编辑器有未保存改动，未自动同步")))

    # ==================== P3：节拍 / 批量 / 导出 / 专注 ====================

    async def save_beats(self, beats: list[dict]) -> None:
        if not self.current:
            return
        await db.update_chapter(
            self.current["id"],
            beats=json.dumps(beats, ensure_ascii=False))
        self.current["beats"] = json.dumps(beats, ensure_ascii=False)

    async def on_generate_next_beat(self) -> None:
        """逐节拍生成（方案 7-③.5）：只推演一拍 600~800 字，经 Diff 确认写入。"""
        if not self.current or self.generating:
            return
        if not self.current_model:
            self.append_log("⚠️ 未选择模型，无法生成节拍")
            return
        beats = self.beat_panel._parse(self.current.get("beats") or "[]")
        beat = next((b for b in beats if not b.get("done")), None)
        if beat is None:
            self.append_log("ℹ️ 所有节拍已完成；可在细纲面板添加新节拍")
            return
        if self.editor.dirty:
            await self.editor.save_now()
        self.status_bar.set_state("busy")
        self.gen_btn.disabled = True
        if self.page:
            self.page.update()
        try:
            canon_ctx = await canon.build_canon_context(self.current["id"])
            text = self.editor.text_field.value or ""
            from core import prompt_builder as pb
            system = await pb.build_system_text(self.project)
            beats_left = " → ".join(
                f"{'✓' if b.get('done') else '○'}{b['title']}"
                for b in beats)
            user = (
                f"【本轮任务类型：节拍创作（{beat['title']}）】\n"
                f"本章节拍序列：{beats_left}\n"
                f"当前节拍：「{beat['title']}」\n\n"
                f"{canon_ctx}\n\n"
                f"【已写正文结尾（供衔接）】\n{text[-1200:] or '（正文为空，从开篇写起）'}\n\n"
                f"【要求】\n"
                f"1. 只写本节拍内容，600~800 字，直接输出正文\n"
                f"2. 与已写正文自然衔接；严格遵循 Canon 设定\n"
                f"3. 不要输出标题、编号或解释\n"
            )
            new_text = await ai_service.call_llm_text(
                self.current_model,
                [{"role": "system", "content": system},
                 {"role": "user", "content": user}],
                purpose="beats", temperature=0.85)
            self.status_bar.set_state("ready")
            self.gen_btn.disabled = False
            # Diff 卡插入到文末：采纳后标记本拍完成
            text_now = self.editor.text_field.value or ""
            self.editor._refine_target = ("", len(text_now), len(text_now))
            self.editor._refine_full = False
            self.editor.show_diff("", new_text)
            self.editor.on_diff_applied_cb = self._mark_current_beat_done
            self.append_log(f"✓ 节拍「{beat['title']}」已生成，"
                            f"请在 Diff 卡确认写入")
        except Exception as ex:
            self.status_bar.set_state("ready")
            self.gen_btn.disabled = False
            self.append_log(f"✗ 节拍生成失败：{ex}")
            if self.page:
                self.page.update()

    async def _mark_current_beat_done(self) -> None:
        beats = self.beat_panel._parse(self.current.get("beats") or "[]")
        beat = next((b for b in beats if not b.get("done")), None)
        if beat:
            beat["done"] = True
            await self.save_beats(beats)
            self.beat_panel.refresh(self.current.get("beats") or "[]")
            self.append_log(f"✓ 节拍「{beat['title']}」已完成")

    def show_batch_dialog(self, e=None) -> None:
        """批量生成对话框（P3）：从当前章起连续生成 N 章草稿。"""
        if not self.current:
            self.page.show_dialog(ft.SnackBar(ft.Text("请先选择起始章节")))
            return
        count = ft.TextField(label="连续生成章数", value="3",
                             keyboard_type=ft.KeyboardType.NUMBER)
        guidance = ft.TextField(label="每章统一指导（可选）", multiline=True,
                                min_lines=2)

        async def confirm(ev=None):
            try:
                n = max(1, min(50, int(count.value.strip() or "3")))
            except ValueError:
                n = 3
            self.page.pop_dialog()
            self.cancel_event = asyncio.Event()
            self.generating = True
            self.gen_btn.disabled = True
            self.status_bar.set_state("busy")
            self.append_log(f"▶ 批量生成开始：自第{self.current['number']}章 "
                            f"起 {n} 章（Esc 中止）")
            if self.page:
                self.page.update()
            result = await batch_generate.run_batch(
                self.project, self.current["id"], n, self.current_model,
                guidance=guidance.value or "",
                cancel_event=self.cancel_event, on_log=self.append_log)
            self.generating = False
            self.gen_btn.disabled = False
            self.status_bar.set_state("ready")
            self.append_log(f"■ 批量生成结束：完成 {result['done']}"
                            f"{'（中止）' if result['cancelled'] else ''}")
            await self.reload_chapters()
            await self.select_chapter(self.current["id"], force=True)

        async def cancel(ev=None):
            self.page.pop_dialog()

        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("批量生成（P3）"),
            content=ft.Column([
                ft.Text(f"从当前章（第{self.current['number']}章 "
                        f"《{self.current['title']}》）开始，连续生成草稿。\n"
                        "每章自动过确定性 Gate 并落盘（状态=草稿，不自动定稿）；"
                        "生成期间可按 Esc 中止。", size=theme.SIZE_SM),
                count, guidance],
                spacing=theme.SPACE_MD, tight=True, width=380),
            actions=[ft.TextButton("取消", on_click=cancel),
                     ft.FilledButton("开始批量生成", on_click=confirm)],
        ))

    async def on_export(self, e=None) -> None:
        """全本导出（P3）。"""
        result = await export_book.export_book(self.project)
        if result["ok"]:
            self.append_log(f"✓ 导出完成：{result['path']}"
                            f"（{result['chapters']} 章，{result['words']} 字）")
            self.page.show_dialog(ft.SnackBar(ft.Text(
                f"已导出：{os.path.basename(result['path'])}")))
        else:
            self.append_log(f"⚠️ 导出失败：{result.get('message')}")

    def toggle_focus(self, e=None) -> None:
        """专注模式（P3，方案 6.5）：隐藏全部侧栏与悬浮件。"""
        self.focus_mode = not getattr(self, "focus_mode", False)
        if self.focus_mode:
            self._focus_restore = (self.left_collapsed, self.right_collapsed)
            self.left_panel.visible = False
            self.right_panel.visible = False
            self.left_resizer.visible = False
            self.right_resizer.visible = False
            self.editor.k_pill.visible = False
            self.editor.ghost_bar.hide()
        else:
            self.left_panel.visible = True
            self.right_panel.visible = True
            self.left_resizer.visible = True
            self.right_resizer.visible = True
            self.left_collapsed = self.right_collapsed = False
        self.page.update()

    def _select_tab(self, index: int) -> None:
        self._right_tab_index = index
        self.tab_title.value = self._tab_titles[index]
        for i, view in enumerate(self._tab_views):
            view.visible = i == index
        for i, btn in enumerate(self._tab_buttons):
            btn.selected = i == index
        if index == 3:  # 统计页签：打开时刷新
            asyncio.create_task(self.refresh_usage())
        if self.page:
            self.page.update()

    def _switch_mode(self, mode: str) -> None:
        """底部状态栏切换写作 / 设计界面（控件常驻，仅 visible 切换）。"""
        if mode == self.mode:
            return
        if mode == "design" and self.generating:
            self.append_log("⚠️ 正文生成中，请先停止再切换到设计界面")
            return
        if mode == "write" and self.editor.dirty:
            asyncio.create_task(self.editor.save_now())
        self.mode = mode
        is_design = mode == "design"
        self.write_layer.visible = not is_design
        self.design_view.visible = is_design
        self.status_bar.set_mode(mode)
        if is_design:
            asyncio.create_task(self.design_view.load())
            self.append_log("🎨 已进入设计界面：大纲 / 世界观 / 人物 + AI 协作台")
        if self.page:
            self.page.update()

    def toggle_left(self, e=None) -> None:
        self.left_collapsed = not self.left_collapsed
        visible = not self.left_collapsed
        self.left_panel.visible = visible
        self.left_resizer.visible = visible
        self.page.update()

    def toggle_right(self, e=None) -> None:
        self.right_collapsed = not self.right_collapsed
        visible = not self.right_collapsed
        self.right_panel.visible = visible
        self.right_resizer.visible = visible
        self.page.update()

    # ==================== 分栏拖拽宽度 ====================

    def _set_left_width(self, w: float) -> None:
        self.left_panel.width = w
        if self.page:
            try:
                self.left_panel.update()
            except Exception:
                pass

    def _set_right_width(self, w: float) -> None:
        self.right_panel.width = w
        if self.page:
            try:
                self.right_panel.update()
            except Exception:
                pass

    def _save_layout_widths(self) -> None:
        """拖拽结束后把左右栏宽度写回 config.json（跨会话记忆）。"""
        config.set_key("ui_left_width", int(self.left_panel.width or 0))
        config.set_key("ui_right_width", int(self.right_panel.width or 0))
        config.save_config()

    def open_settings(self, e=None) -> None:
        self.settings_view.show()

    def append_log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_view.controls.append(
            ft.Row([
                theme.metric_text(stamp, size=theme.SIZE_XS,
                                  color=theme.TEXT_FAINT),
                ft.Text(message, size=theme.SIZE_XS, selectable=True,
                        color=theme.TEXT_MUTED, expand=True),
            ], spacing=theme.SPACE_SM,
                vertical_alignment=ft.CrossAxisAlignment.START))
        if len(self.log_view.controls) > 200:
            self.log_view.controls = self.log_view.controls[-200:]
        if self.page:
            self.log_view.update()
