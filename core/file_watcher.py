"""外部文件变更监听（P2，方案 5.6）：watchdog 实时监听 chapters/。

- 本应用自己的写入会被抑制（write_text 记录时间戳，1.5s 窗口内忽略）
- 外部修改 → 线程安全回调（loop.call_soon_threadsafe 投递回事件循环）
watchdog 未安装时静默降级为不可用（回退到激活时的 mtime 检查）。
"""
import asyncio
import os
import threading
import time
from typing import Callable, Optional

from core import file_manager

_OWN_WRITE_WINDOW_S = 1.5


def _suppress_write(path: str) -> None:
    """file_manager.write_text 调用：记录本次写入，供监听器忽略。"""
    _OWN_WRITES[os.path.abspath(path)] = time.time()


_OWN_WRITES: dict[str, float] = {}
try:
    # 让 file_manager 在写入时通知本模块（弱耦合：函数注入）
    file_manager.write_text  # noqa: B018
    _orig_write_text = file_manager.write_text

    def write_text_with_suppress(path: str, text: str) -> None:
        _orig_write_text(path, text)
        _suppress_write(path)

    file_manager.write_text = write_text_with_suppress
except Exception:
    pass


class ChapterWatcher:
    """监听 chapters/ 目录的 .md 外部修改。"""

    def __init__(self, loop: asyncio.AbstractEventLoop,
                 on_external_change: Callable[[str], None]):
        self.loop = loop
        self.on_external_change = on_external_change
        self._observer = None
        self._watch_path = ""
        self._debounce_timer: Optional[threading.Timer] = None
        self._pending_paths: set[str] = set()
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        try:
            import watchdog  # noqa: F401
            return True
        except ImportError:
            return False

    def start(self, watch_path: str) -> None:
        self.stop()
        if not self.available or not os.path.isdir(watch_path):
            return
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer

            watcher = self

            class Handler(FileSystemEventHandler):
                def on_modified(self, event):
                    watcher._on_event(event.src_path)

                def on_created(self, event):
                    watcher._on_event(event.src_path)

                def on_moved(self, event):
                    watcher._on_event(event.dest_path)

            self._watch_path = watch_path
            self._observer = Observer(timeout=1.0)
            self._observer.schedule(Handler(), watch_path, recursive=False)
            self._observer.daemon = True
            self._observer.start()
        except Exception as e:
            print(f"watchdog 启动失败（降级为 mtime 检查）：{e}")
            self._observer = None

    def stop(self) -> None:
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=1.0)
            except Exception:
                pass
            self._observer = None

    # ---------- 事件处理 ----------

    def _on_event(self, path: str) -> None:
        if not path or not path.lower().endswith(".md"):
            return
        abspath = os.path.abspath(path)
        t = _OWN_WRITES.get(abspath)
        if t and time.time() - t < _OWN_WRITE_WINDOW_S:
            return  # 本应用自己的写入
        with self._lock:
            self._pending_paths.add(abspath)
            if self._debounce_timer:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(
                0.8, self._fire)
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _fire(self) -> None:
        with self._lock:
            paths = set(self._pending_paths)
            self._pending_paths.clear()
        if not paths:
            return
        # 线程 → 事件循环（flet UI 只能在 loop 线程更新）
        try:
            self.loop.call_soon_threadsafe(self.on_external_change, paths)
        except RuntimeError:
            pass
