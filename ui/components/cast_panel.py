"""登场名册 + 角色迷你悬浮卡（方案 6.2 左栏）。

数据源：chapters.characters（本章出场）+ characters 表动态状态。
点角色名弹迷你悬浮卡（cs_location / cs_level / cs_items 实时状态），
不跳出写作界面；[+] 手动登记角色（含别名与已知秘密）。
"""
import json
from typing import Callable, Optional

import flet as ft


class CastPanel(ft.Column):
    def __init__(self, app):
        self.app = app
        self.chips_row = ft.Row(wrap=True, spacing=6, run_spacing=6)
        self.empty_hint = ft.Text("（本章暂无出场角色）", size=11,
                                  color=ft.Colors.OUTLINE)
        super().__init__(
            controls=[
                ft.Row(
                    [ft.Text("👥 登场名册", size=13,
                             weight=ft.FontWeight.W_600),
                     ft.Container(expand=True),
                     ft.IconButton(icon=ft.Icons.PERSON_ADD_ALT, icon_size=16,
                                   tooltip="登记角色",
                                   on_click=self._handle_add)],
                    spacing=4),
                ft.Column([self.empty_hint, self.chips_row], spacing=4),
            ],
            spacing=6)

    # ---------- 数据 ----------

    def refresh(self, characters: list[dict], chapter_char_names: list[str]):
        by_name = {c["name"]: c for c in characters}
        self.chips_row.controls = []
        names = chapter_char_names or []
        # 本章出场角色优先；若细纲未填，展示主表中有动态状态的角色
        if not names:
            names = [c["name"] for c in characters
                     if c.get("cs_chapter_id")]
        for name in names:
            info = by_name.get(name)
            label = name if info else f"{name}?"
            role_tag = (info or {}).get("role", "")
            tag = {"protagonist": "主", "antagonist": "敌"}.get(role_tag, "")
            chip = ft.Chip(
                label=ft.Text(f"{label}{'·' + tag if tag else ''}", size=12),
                bgcolor=ft.Colors.SECONDARY_CONTAINER,
                on_click=lambda e, n=name, d=info:
                self._handle_show_card(n, d),
            )
            self.chips_row.controls.append(chip)
        self.empty_hint.visible = not self.chips_row.controls
        if self.page:
            self.update()

    # ---------- 迷你悬浮卡 ----------

    def _handle_show_card(self, name: str, info: Optional[dict]) -> None:
        if not info:
            self.app.show_dialog_text(
                f"{name}（未登记）",
                "该角色尚未录入角色主表——定稿后管线 B 会自动登记，"
                "或点击右上角「登记角色」手动录入。")
            return
        knowledge = []
        try:
            knowledge = json.loads(info.get("cs_knowledge") or "[]")
        except json.JSONDecodeError:
            pass
        rows = [
            ("当前位置", info.get("cs_location") or "未知"),
            ("修为/职级", info.get("cs_level") or "未知"),
            ("身体状态", info.get("cs_physical") or "未知"),
            ("关键道具", info.get("cs_items") or "无"),
            ("状态更新于", info.get("cs_chapter_id") or "—"),
        ]
        card_rows = [ft.Row([ft.Text(f"{k}：", size=12,
                                     weight=ft.FontWeight.W_600),
                             ft.Text(v, size=12, expand=True,
                                     selectable=True)], spacing=4)
                     for k, v in rows]
        if (info.get("personality") or "").strip():
            card_rows.insert(0, ft.Row(
                [ft.Text("性格：", size=12, weight=ft.FontWeight.W_600),
                 ft.Text(info["personality"], size=12, expand=True,
                         selectable=True)], spacing=4))
        if knowledge:
            card_rows.append(ft.Text("已知情报边界：", size=12,
                                     weight=ft.FontWeight.W_600))
            card_rows.extend([ft.Text(f"· {k}", size=11, selectable=True)
                              for k in knowledge])
        self.app.page.show_dialog(ft.AlertDialog(
            modal=False,
            title=ft.Text(f"{name} 的实时状态", size=15),
            content=ft.Column(card_rows, spacing=6, tight=True, width=340),
            actions=[ft.TextButton("关闭", on_click=lambda e:
                                   self.app.page.pop_dialog())],
        ))

    # ---------- 登记角色 ----------

    def _handle_add(self, e=None) -> None:
        name = ft.TextField(label="姓名（唯一）", autofocus=True)
        aliases = ft.TextField(label="别名/代称（顿号分隔）")
        role_dd = ft.Dropdown(
            text="定位",
            value="supporting",
            options=[ft.DropdownOption(key="protagonist", text="主角"),
                     ft.DropdownOption(key="antagonist", text="反派"),
                     ft.DropdownOption(key="supporting", text="配角")])
        personality = ft.TextField(label="性格")
        background = ft.TextField(label="背景")
        knowledge = ft.TextField(label="已知核心秘密（每行一条）",
                                 multiline=True, min_lines=2)

        async def confirm(ev=None):
            n = (name.value or "").strip()
            if not n:
                name.error_text = "姓名不能为空"
                self.app.page.update()
                return
            alias_list = [s.strip() for s in
                          (aliases.value or "").replace("，", "、").split("、")
                          if s.strip()]
            know_list = [s.strip() for s in (knowledge.value or "").split("\n")
                         if s.strip()]
            await self.app.on_character_saved(
                n, aliases=json.dumps(alias_list, ensure_ascii=False),
                role=role_dd.value or "supporting",
                personality=personality.value.strip(),
                background=background.value.strip(),
                cs_knowledge=json.dumps(know_list, ensure_ascii=False))
            self.app.page.pop_dialog()

        async def cancel(ev=None):
            self.app.page.pop_dialog()

        self.app.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("登记角色"),
            content=ft.Column([name, aliases, role_dd, personality,
                               background, knowledge],
                              spacing=10, tight=True, width=380,
                              scroll=ft.ScrollMode.AUTO),
            actions=[ft.TextButton("取消", on_click=cancel),
                     ft.FilledButton("保存", on_click=confirm)],
        ))
