"""模型下拉 + 一键刷新（方案 5.2.3，flet 0.86 适配版）。

刷新兼任连接测试：成功填充下拉，失败提示离线。
运行时实际使用的模型以本下拉框实时选择为准。

视觉：刷新期间内联加载指示（不再是「点了没反应」）。
"""
import flet as ft

from core import ai_service
from ui import theme


class ModelSelector(ft.Row):
    def __init__(self, on_change=None, model_value: str = "", width: int = 320):
        self.selected_model = model_value or None
        self.on_change_callback = on_change

        self.dropdown = ft.Dropdown(
            text="当前使用模型",
            options=[],
            value=model_value or None,
            on_select=self._on_select,
            expand=True,
        )
        self.spinner = ft.Container(theme.inline_loader(16), visible=False)
        self.refresh_btn = ft.IconButton(
            icon=ft.Icons.REFRESH, icon_size=theme.ICON_ACTION,
            icon_color=theme.TEXT_MUTED,
            tooltip="从 AI 服务刷新模型列表（兼任连接测试）",
            on_click=self.refresh_models,
        )
        super().__init__(controls=[self.dropdown, self.spinner,
                                   self.refresh_btn],
                         spacing=theme.SPACE_XS, width=width)

    def _set_loading(self, loading: bool) -> None:
        self.spinner.visible = loading
        self.refresh_btn.disabled = loading
        self.dropdown.disabled = loading
        if self.page:
            self.update()

    async def refresh_models(self, e=None) -> None:
        """拉取列表兼任连接测试：成功填充下拉，失败提示离线。"""
        self._set_loading(True)
        try:
            models = await ai_service.get_available_models()
            if models:
                self.dropdown.options = [ft.DropdownOption(key=m, text=m)
                                         for m in models]
                if not self.selected_model or self.selected_model not in models:
                    self.selected_model = models[0]
                self.dropdown.value = self.selected_model
                self.dropdown.error_text = None
            else:
                self.dropdown.options = []
                self.dropdown.value = None
                self.selected_model = None
                self.dropdown.error_text = "未检测到模型，请先启动 AI 服务（LM Studio Server / 云端 API）"
        finally:
            self._set_loading(False)

    def _on_select(self, e) -> None:
        self.selected_model = self.dropdown.value
        if self.on_change_callback:
            self.on_change_callback(self.selected_model)
