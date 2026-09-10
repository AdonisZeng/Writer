"""设置页（方案 6.2 / 5.1 / 5.2.3）：API 连接 + 模型下拉 + 连接状态 + 主题 + 项目切换。

全屏覆盖层（非模态弹窗原则：设置走独立页面），保存后应用并返回。
视觉：一张抬起的「稿纸」卡片浮于半透明遮罩之上；分组用区块标题而非粗黑字。
"""
from typing import Optional

import flet as ft

from core import ai_service, config, project
from ui import theme
from ui.components.model_selector import ModelSelector


class SettingsView(ft.Container):
    def __init__(self, app):
        self.app = app

        # ---- 连接配置 ----
        self.api_base = ft.TextField(
            label="API 地址（api_base）",
            value=config.get("api_base"),
            hint_text="http://127.0.0.1:1234/v1 或云端 OpenAI 协议地址",
            expand=True,
        )
        self.api_key = ft.TextField(
            label="API Key（LM Studio 填任意非空字符串）",
            value=config.get("api_key"),
            expand=True,
        )
        self.context_limit = ft.TextField(
            label="上下文窗口（context_limit）",
            value=str(config.get("context_limit")),
            hint_text="建议填 LM Studio 加载模型时实际配置的 Context Length",
            keyboard_type=ft.KeyboardType.NUMBER,
            expand=True,
        )
        self.model_selector = ModelSelector(
            on_change=self._on_model_change,
            model_value=config.get("model"),
        )
        # 推理参数模式：Qwen3 一类只认 on/off 的模型不会收到 medium 档位
        self.reasoning_mode_dd = ft.Dropdown(
            text="推理参数模式",
            value=config.get("reasoning_mode", "auto") or "auto",
            options=[
                ft.DropdownOption(key="auto", text="自动识别（推荐）"),
                ft.DropdownOption(
                    key="levels",
                    text="分级 low/medium/high（仅支持分级的后端）"),
                ft.DropdownOption(key="toggle", text="仅开关 on/off（Qwen3 等）"),
                ft.DropdownOption(key="none", text="不发送推理参数"),
            ],
            expand=True,
            tooltip="LM Studio 后端只有开/关两档：选「分级」会给它发 low~high，"
                    "LM Studio 会打 Reasoning setting 告警并强制回退 on，等于白设。"
                    "云端（OpenAI o 系 / gpt-oss 等）才吃得下分级值。",
        )

        # ---- RAG 知识库（P2）----
        self.rag_enabled_sw = ft.Switch(
            label="启用 RAG 知识库检索（生成时注入相关历史片段）",
            value=bool(config.get("rag_enabled")))
        self.rag_model = ft.TextField(
            label="嵌入模型（rag_model）",
            value=config.get("rag_model"),
            hint_text="LM Studio 中加载的 embedding 模型名（如 bge-m3）",
            expand=True)
        self.rag_top_k = ft.TextField(
            label="检索条数（top_k）", value=str(config.get("rag_top_k", 4)),
            keyboard_type=ft.KeyboardType.NUMBER, expand=True)

        # ---- 排版自定义（P3）----
        self.font_size_dd = ft.Dropdown(
            text="正文字号",
            value=str(config.get("editor_font_size", 15)),
            options=[ft.DropdownOption(key=s, text=f"{s} 号")
                     for s in ("14", "15", "16", "18")],
            expand=True)
        self.editor_width_dd = ft.Dropdown(
            text="正文最大行宽",
            value=str(config.get("editor_width", 780)),
            options=[ft.DropdownOption(key="0", text="不限宽"),
                     ft.DropdownOption(key="680", text="680 px（约 38 字/行）"),
                     ft.DropdownOption(key="780", text="780 px（约 43 字/行）"),
                     ft.DropdownOption(key="900", text="900 px（约 50 字/行）")],
            expand=True)
        self.seed_dd = ft.Dropdown(
            text="强调色",
            value=theme.normalize_seed(config.get("color_seed")),
            options=[ft.DropdownOption(key=k, text=label)
                     for k, label in theme.seed_options()],
            expand=True)
        self.ui_font_size = ft.TextField(
            label="界面字号（基准像素）",
            value=str(config.get("ui_font_size", theme.FONT_BASE_DEFAULT)),
            hint_text=f"{theme.FONT_BASE_MIN}~{theme.FONT_BASE_MAX}，默认 "
                      f"{theme.FONT_BASE_DEFAULT}",
            keyboard_type=ft.KeyboardType.NUMBER,
            expand=True,
            tooltip="统一缩放全部界面文字（面板/按钮/协作台/状态栏等）；"
                    "正文阅读字号由上方「正文字号」单独控制。保存后即时生效。")
        self.extract_on_adopt_sw = ft.Switch(
            label="AI 建议「提炼后采纳」：自动提炼出适合目标字段的内容再写入",
            value=bool(config.get("design_extract_on_adopt", True)))

        # ---- 主题 ----
        self.theme_dropdown = ft.Dropdown(
            text="界面主题",
            value=config.get("theme_mode", "light"),
            options=[
                ft.DropdownOption(key="light", text="浅色"),
                ft.DropdownOption(key="dark", text="深色"),
                ft.DropdownOption(key="system", text="跟随系统"),
            ],
        )

        # ---- 项目切换 ----
        self.project_dropdown = ft.Dropdown(
            text="当前小说项目",
            value=config.get("project"),
            options=[ft.DropdownOption(key=name, text=name)
                     for name in project.list_projects()],
            on_select=self._on_project_change,
        )

        self.save_btn = ft.FilledButton(
            "保存并应用", icon=ft.Icons.SAVE, on_click=self._handle_save)
        self.back_btn = ft.OutlinedButton(
            "返回编辑", icon=ft.Icons.ARROW_BACK, on_click=self._handle_back)
        self.msg = ft.Text("", size=theme.SIZE_SM, visible=False)

        def _group(icon, title: str) -> ft.Control:
            return theme.section_header(icon, title)

        form = ft.Column(
            controls=[
                _group(ft.Icons.CLOUD, "AI 连接（统一 OpenAI 协议）"),
                self.api_base,
                ft.Row([self.api_key, self.context_limit],
                       spacing=theme.SPACE_MD),
                ft.Row([self.model_selector], spacing=theme.SPACE_MD),
                ft.Row([self.reasoning_mode_dd], spacing=theme.SPACE_MD),
                theme.divider(),
                _group(ft.Icons.MANAGE_SEARCH, "RAG 知识库（P2）"),
                self.rag_enabled_sw,
                ft.Row([self.rag_model, self.rag_top_k],
                       spacing=theme.SPACE_MD),
                ft.Text("提示：先在「统计」页签点「重建知识库索引」；"
                        "定稿后会自动索引本章。", size=theme.SIZE_XS,
                        color=theme.TEXT_FAINT),
                theme.divider(),
                _group(ft.Icons.PALETTE, "外观与项目"),
                ft.Row([self.theme_dropdown, self.project_dropdown],
                       spacing=theme.SPACE_MD),
                ft.Row([self.font_size_dd, self.editor_width_dd],
                       spacing=theme.SPACE_MD),
                ft.Row([self.ui_font_size, self.seed_dd],
                       spacing=theme.SPACE_MD),
                self.extract_on_adopt_sw,
                ft.Row([self.save_btn, self.back_btn, self.msg],
                       spacing=theme.SPACE_MD),
            ],
            spacing=theme.SPACE_LG,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        super().__init__(
            content=ft.Container(
                content=theme.paper_card(form, width=720),
                alignment=ft.Alignment(0, 0),
                expand=True,
                bgcolor=ft.Colors.with_opacity(0.45, ft.Colors.SCRIM),
                padding=theme.SPACE_XL,
            ),
            visible=False, expand=True,
        )

    # ==================== 事件 ====================

    def show(self) -> None:
        self.visible = True
        if self.page:
            self.update()

    def hide(self) -> None:
        self.visible = False
        if self.page:
            self.update()

    def _on_model_change(self, model: Optional[str]) -> None:
        self.app.current_model = model or self.app.current_model
        if self.app.current_model:
            self.msg.visible = True
            self.msg.value = f"运行时模型：{self.app.current_model}"
            self.msg.color = theme.TEXT_MUTED
            if self.page:
                self.msg.update()

    def _on_project_change(self, e) -> None:
        pass  # 应用在「保存并应用」时统一处理

    async def _handle_save(self, e=None) -> None:
        self.save_btn.disabled = True
        if self.page:
            self.update()
        try:
            config.set_key("api_base", self.api_base.value.strip())
            config.set_key("api_key", self.api_key.value.strip()
                           or "lm-studio")
            try:
                limit = int(self.context_limit.value.strip() or "8192")
            except ValueError:
                limit = 8192
            config.set_key("context_limit", max(1024, limit))
            if self.model_selector.selected_model:
                config.set_key("model", self.model_selector.selected_model)
            config.set_key("reasoning_mode",
                           self.reasoning_mode_dd.value or "auto")
            ai_service.reset_reasoning_cache()
            # RAG
            config.set_key("rag_enabled", bool(self.rag_enabled_sw.value))
            config.set_key("rag_model", self.rag_model.value.strip())
            try:
                top_k = int(self.rag_top_k.value.strip() or "4")
            except ValueError:
                top_k = 4
            config.set_key("rag_top_k", max(1, min(20, top_k)))
            config.set_key("theme_mode", self.theme_dropdown.value or "light")
            # 排版（P3）
            config.set_key("editor_font_size",
                           int(self.font_size_dd.value or "15"))
            config.set_key("editor_width",
                           int(self.editor_width_dd.value or "780"))
            config.set_key("color_seed",
                           theme.normalize_seed(self.seed_dd.value))
            # 全局界面字号（基准像素，10~20）
            try:
                ui_font = int((self.ui_font_size.value or "").strip()
                              or str(theme.FONT_BASE_DEFAULT))
            except ValueError:
                ui_font = theme.FONT_BASE_DEFAULT
            config.set_key("ui_font_size",
                           max(theme.FONT_BASE_MIN,
                               min(theme.FONT_BASE_MAX, ui_font)))
            config.set_key("design_extract_on_adopt",
                           bool(self.extract_on_adopt_sw.value))
            config.save_config()
            ai_service.rebuild_client()

            # 字号与主题即时生效：先缩放控件文字，再重建主题（按钮等经 text_theme 缩放）
            theme.apply_font_size(config.get("ui_font_size",
                                             theme.FONT_BASE_DEFAULT),
                                  controls=self.app.root)
            self.app.apply_theme()
            self.app.apply_typography()
            # 项目切换
            new_project = self.project_dropdown.value
            if new_project and new_project != config.get("project"):
                await self.app.switch_project(new_project)

            self.msg.visible = True
            self.msg.value = "已保存并应用"
            self.msg.color = theme.semantic_color("success")
        except Exception as ex:
            self.msg.visible = True
            self.msg.value = f"保存失败：{ex}"
            self.msg.color = theme.semantic_color("danger")
        finally:
            self.save_btn.disabled = False
            if self.page:
                self.update()

    async def _handle_back(self, e=None) -> None:
        self.hide()
        await self.app.refresh_all()
