"""全局快捷键统一注册（方案 6.3，flet 0.86 事件模型）。

flet 0.86：page.on_keyboard_event 为通知式全局键盘事件（KeyboardEvent：
key/ctrl/alt/shift/meta），不拦截默认行为——适合全局命令型快捷键。
P0：Esc（停止生成 / 放弃候选）、Ctrl+S（立即保存）。
P2 追加：Tab / Enter 采纳、Ctrl+K 行内指令、Alt+G 补全开关、Alt+/ 手动补全。
"""
from typing import Callable, Optional

_handler: Optional[Callable] = None


def register(page, handler: Callable) -> None:
    """注册分发器：handler(event) 由各视图提供。"""
    global _handler
    _handler = handler
    page.on_keyboard_event = _dispatch


def _dispatch(e) -> None:
    if _handler is None:
        return
    try:
        _handler(e)
    except Exception as ex:
        print(f"快捷键处理异常：{ex}")


def describe(e) -> str:
    """调试用：事件转可读描述。"""
    parts = []
    if getattr(e, "ctrl", False):
        parts.append("Ctrl+")
    if getattr(e, "alt", False):
        parts.append("Alt+")
    if getattr(e, "shift", False):
        parts.append("Shift+")
    parts.append(getattr(e, "key", ""))
    return "".join(parts)
