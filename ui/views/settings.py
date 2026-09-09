"""设置页（方案 6.2 / 5.1 / 5.2.3）：API 连接 + 模型下拉 + 连接状态 + 主题 + 项目切换。

全屏覆盖层（非模态弹窗原则：设置走独立页面），保存后应用并返回。
"""
from typing import Callable, Optional

import flet as ft

from core import ai_service, config, project
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
                ft.DropdownOption(key="levels", text="分级 low/medium/high"),
                ft.DropdownOption(key="toggle", text="仅开关 on/off（Qwen3 等）"),
                ft.DropdownOption(key="none", text="不发送推理参数"),
            ],
            expand=True,
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
            text="主题色",
            value=config.get("color_seed", "indigo"),
            options=[ft.DropdownOption(key="indigo", text="靛蓝"),
                     ft.DropdownOption(key="teal", text="青碧"),
                     ft.DropdownOption(key="rose", text="玫红"),
                     ft.DropdownOption(key="amber", text="琥珀"),
                     ft.DropdownOption(key="blue", text="蓝")],
            expand=True)

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
        self.msg = ft.Text("", size=12, visible=False)

        form = ft.Column(
            controls=[
                ft.Text("AI 连接（统一 OpenAI 协议）", size=15,
                        weight=ft.FontWeight.W_600),
                self.api_base,
                ft.Row([self.api_key, self.context_limit], spacing=12),
                ft.Row([self.model_selector], spacing=12),
                ft.Row([self.reasoning_mode_dd], spacing=12),
                ft.Divider(height=24),
                ft.Text("RAG 知识库（P2）", size=15,
                        weight=ft.FontWeight.W_600),
                self.rag_enabled_sw,
                ft.Row([self.rag_model, self.rag_top_k], spacing=12),
                ft.Container(
                    content=ft.Text(
                        "提示：先在「统计」页签点「重建知识库索引」；"
                        "定稿后会自动索引本章。", size=11,
                        color=ft.Colors.OUTLINE),
                    margin=ft.Margin(0, 0, 0, 8)),
                ft.Divider(height=24),
                ft.Text("外观与项目", size=15, weight=ft.FontWeight.W_600),
                ft.Row([self.theme_dropdown, self.project_dropdown],
                       spacing=12),
                ft.Row([self.font_size_dd, self.editor_width_dd],
                       spacing=12),
                self.seed_dd,
                ft.Row([self.save_btn, self.back_btn, self.msg], spacing=12),
            ],
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        super().__init__(
            content=ft.Container(
                content=ft.Container(
                    content=form,
                    width=720, padding=32, border_radius=16,
                    bgcolor=ft.Colors.SURFACE,
                    shadow=ft.BoxShadow(
                        blur_radius=24, color=ft.Colors.with_opacity(0.12,
                                                                     ft.Colors.BLACK),
                        offset=ft.Offset(0, 4),
                    ),
                ),
                alignment=ft.Alignment(0, 0),
                expand=True,
                bgcolor=ft.Colors.with_opacity(0.35, ft.Colors.SURFACE_CONTAINER_HIGHEST),
                padding=24,
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
            self.msg.color = ft.Colors.OUTLINE
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
            config.set_key("color_seed", self.seed_dd.value or "indigo")
            config.save_config()
            ai_service.rebuild_client()

            # 主题即时生效
            self.app.apply_theme()
            self.app.apply_typography()
            # 项目切换
            new_project = self.project_dropdown.value
            if new_project and new_project != config.get("project"):
                await self.app.switch_project(new_project)

            self.msg.visible = True
            self.msg.value = "已保存并应用"
            self.msg.color = ft.Colors.GREEN
        except Exception as ex:
            self.msg.visible = True
            self.msg.value = f"保存失败：{ex}"
            self.msg.color = ft.Colors.RED
        finally:
            self.save_btn.disabled = False
            if self.page:
                self.update()

    async def _handle_back(self, e=None) -> None:
        self.hide()
        await self.app.refresh_all()
