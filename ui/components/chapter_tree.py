"""左栏章节树 + 状态徽标（方案 6.2 左栏，P2：新建/重命名/删除/拖拽重排）。"""
import flet as ft

_STATUS_COLORS = {
    "outlined": ft.Colors.GREY,        # 细纲
    "drafted": ft.Colors.BLUE,         # 草稿
    "revised": ft.Colors.AMBER,        # 精修
    "finalized": ft.Colors.GREEN,      # 定稿
}
_STATUS_TEXT = {
    "outlined": "细纲", "drafted": "草稿",
    "revised": "精修", "finalized": "定稿",
}


class ChapterTree(ft.Column):
    def __init__(self, on_select, on_add, on_rename, on_delete,
                 on_reorder=None):
        self.on_select_cb = on_select
        self.on_add_cb = on_add
        self.on_rename_cb = on_rename
        self.on_delete_cb = on_delete
        self.on_reorder_cb = on_reorder      # (drag_id, target_id)
        self.selected_id = ""
        self._chapters: list[dict] = []

        self.list_view = ft.ListView(expand=True, spacing=2)

        super().__init__(
            controls=[
                ft.Row(
                    controls=[
                        ft.Text("章节目录", size=13,
                                weight=ft.FontWeight.W_600),
                        ft.Container(expand=True),
                        ft.IconButton(icon=ft.Icons.ADD, icon_size=18,
                                      tooltip="新建章节",
                                      on_click=self._handle_add),
                    ],
                    spacing=4,
                ),
                self.list_view,
            ],
            spacing=4,
            expand=True,
        )

    # ---------- 数据刷新 ----------

    def refresh(self, chapters: list[dict]) -> None:
        self._chapters = chapters
        self.list_view.controls = [self._build_tile(c) for c in chapters]
        if self.page:
            self.list_view.update()

    def select(self, chapter_id: str) -> None:
        self.selected_id = chapter_id
        self.refresh(self._chapters)

    def _build_tile(self, ch: dict) -> ft.Container:
        selected = ch["id"] == self.selected_id
        status = ch.get("status", "outlined")
        dot = ft.Container(width=8, height=8, border_radius=4,
                           bgcolor=_STATUS_COLORS.get(status, ft.Colors.GREY))
        title = ft.Text(
            f"第{ch['number']}章 {ch['title']}".strip(),
            size=13, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
            weight=ft.FontWeight.W_500 if selected else ft.FontWeight.NORMAL,
        )
        menu = ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT, icon_size=16,
            tooltip="章节操作",
            items=[
                ft.PopupMenuItem(content=ft.Text("重命名", size=13),
                                 on_click=lambda e, cid=ch["id"]:
                                 self._handle_rename(cid)),
                ft.PopupMenuItem(content=ft.Text("删除", size=13,
                                                 color=ft.Colors.RED),
                                 on_click=lambda e, cid=ch["id"]:
                                 self._handle_delete(cid)),
            ],
        )
        tile = ft.Container(
            content=ft.Row([dot, title, menu], spacing=8),
            bgcolor=ft.Colors.SECONDARY_CONTAINER if selected else None,
            border_radius=8,
            padding=ft.Padding(8, 4, 0, 4),
            on_click=lambda e, cid=ch["id"]: self._handle_select(cid),
            ink=True,
        )
        if self.on_reorder_cb is None:
            return tile
        # P2 拖拽重排：拖到目标章上 → 移动到目标章之前
        draggable = ft.Draggable(group="chapter", data=ch["id"],
                                 content=tile)
        return ft.DragTarget(
            group="chapter", content=draggable,
            on_will_accept=lambda e: True,
            on_accept=lambda e, tid=ch["id"]: self._handle_drop(e, tid),
        )

    def _handle_drop(self, e, target_id: str) -> None:
        drag_id = getattr(getattr(e, "src", None), "data", None)
        if drag_id and self.on_reorder_cb:
            self.on_reorder_cb(drag_id, target_id)

    # ---------- 事件 ----------

    def _handle_select(self, chapter_id: str) -> None:
        self.selected_id = chapter_id
        self.refresh(self._chapters)
        self.on_select_cb(chapter_id)

    def _handle_add(self, e=None) -> None:
        self.on_add_cb()

    def _handle_rename(self, chapter_id: str) -> None:
        self.on_rename_cb(chapter_id)

    def _handle_delete(self, chapter_id: str) -> None:
        self.on_delete_cb(chapter_id)
