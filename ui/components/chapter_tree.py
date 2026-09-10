"""左栏章节树 + 状态徽标（方案 6.2 左栏，P2：新建/重命名/删除/拖拽重排）。

视觉：状态色统一取自 ui.theme（不再本地重复定义）；
选中态用统一的强调色浅底 + 左侧「书脊」细条（结构即信息：条=当前章）。
"""
import flet as ft

from ui import theme


def _spine(selected: bool) -> ft.Container:
    """左侧书脊细条——选中章的标记（本项目的签名视觉元素）。"""
    return ft.Container(
        width=2, height=16, border_radius=1,
        bgcolor=theme.ACCENT if selected else ft.Colors.TRANSPARENT)


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

        self.list_view = ft.ListView(expand=True, spacing=theme.SPACE_XXS)

        add_btn = ft.IconButton(icon=ft.Icons.ADD, icon_size=theme.ICON_INLINE,
                                tooltip="新建章节", on_click=self._handle_add)
        super().__init__(
            controls=[
                theme.section_header(ft.Icons.MENU_BOOK, "章节目录",
                                     trailing=add_btn),
                self.list_view,
            ],
            spacing=theme.SPACE_XS,
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
        dot = theme.status_dot(ch.get("status", "outlined"))
        title = ft.Text(
            f"第{ch['number']}章 {ch['title']}".strip(),
            size=theme.SIZE_MD, max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            color=theme.ON_ACCENT_SOFT if selected else theme.TEXT,
            weight=theme.W_SEMIBOLD if selected else theme.W_REGULAR,
        )
        menu = ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT, icon_size=theme.ICON_INLINE,
            tooltip="章节操作",
            items=[
                ft.PopupMenuItem(content=ft.Text("重命名", size=theme.SIZE_MD),
                                 on_click=lambda e, cid=ch["id"]:
                                 self._handle_rename(cid)),
                ft.PopupMenuItem(
                    content=ft.Text("删除", size=theme.SIZE_MD,
                                    color=theme.semantic_color("danger")),
                    on_click=lambda e, cid=ch["id"]:
                    self._handle_delete(cid)),
            ],
        )
        tile = ft.Container(
            content=ft.Row([_spine(selected), dot, title, menu],
                           spacing=theme.SPACE_SM),
            bgcolor=theme.ACCENT_SOFT if selected else None,
            border_radius=theme.RADIUS_SM,
            padding=ft.Padding(theme.SPACE_SM, 3, 0, 3),
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
