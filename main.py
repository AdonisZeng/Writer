"""Writer 应用主入口（方案 三/目录结构）。

启动流程：加载配置 → 释放内置 Prompt 模板 → 确保示例项目 →
打开 flet 窗口 → 初始化三栏工作台（打开当前项目库）。
"""
import os

import flet as ft

from core import config, paths, project
from ui import theme


async def main(page: ft.Page):
    # ---- 窗口 ----
    page.title = "Writer — AI 小说创作工作台"
    page.window.width = 1440
    page.window.height = 900
    page.window.min_width = 1100
    page.window.min_height = 700
    page.window.icon = "icon.ico"  # 解析自 assets_dir（main() 末尾的 ft.run 传入）

    # ---- 初始化 ----
    config.load_config()
    paths.ensure_base_dirs()
    from core import prompt_builder
    prompt_builder.release_builtin_prompts()
    project.ensure_sample_project()
    theme.register_fonts(page)     # 注册内置字体（缺失则回落系统字体）

    from ui.views.main_view import WriterApp
    app = WriterApp(page)
    await app.initialize()


if __name__ == "__main__":
    assets = paths.ASSETS_DIR if os.path.isdir(paths.ASSETS_DIR) else "assets"
    ft.run(main, assets_dir=assets)
