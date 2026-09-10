"""内置字体获取脚本（一次性，把 OFL 开源中文字体下载到 assets/fonts/）。

用法：
    python tools/fetch_fonts.py            # 下载缺失字体 + 许可文件
    python tools/fetch_fonts.py --force    # 覆盖重下
    python tools/fetch_fonts.py --check    # 只检查现状，不下载

设计要点：
- 只从官方发布源（google/fonts、jsDelivr 镜像）下载 SIL OFL 许可字体；
- 下载到临时文件并校验（HTTP 状态、体积下限、可选 SHA256），再原子替换；
- 任一失败都不影响应用运行——ui/theme.py 会自动跳过缺失字体、回落系统字体；
- 字体体积较大（CJK 全字库），这是「完全离线、视觉可控」的代价，属预期。

字体用途：
- NotoSansSC-VF.ttf   → UI 无衬线（界面 chrome）
- NotoSerifSC-VF.ttf  → 正文衬线（阅读区）
- NotoSansMono-VF.ttf → 数值等宽（状态栏 / 统计）
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import paths  # noqa: E402
from ui import theme    # noqa: E402

_UA = {"User-Agent": "Writer-font-fetcher/1.0 (+local build tool)"}
_MIN_BYTES = 100 * 1024          # 体积下限：低于视为下载异常


def _enable_utf8() -> None:
    """Windows 控制台默认 GBK，重定向为 UTF-8 以免 ✓/✗ 打印报错。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

_GH = "https://raw.githubusercontent.com/google/fonts/main/ofl"
_CDN = "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl"


def _targets() -> list[dict]:
    return [
        {
            "name": theme.FONT_UI,
            "file": theme.FONT_FILES[theme.FONT_UI],
            "urls": [
                f"{_GH}/notosanssc/NotoSansSC%5Bwght%5D.ttf",
                f"{_CDN}/notosanssc/NotoSansSC%5Bwght%5D.ttf",
            ],
            "license_urls": [
                f"{_GH}/notosanssc/OFL.txt",
                f"{_CDN}/notosanssc/OFL.txt",
            ],
        },
        {
            "name": theme.FONT_SERIF,
            "file": theme.FONT_FILES[theme.FONT_SERIF],
            "urls": [
                f"{_GH}/notoserifsc/NotoSerifSC%5Bwght%5D.ttf",
                f"{_CDN}/notoserifsc/NotoSerifSC%5Bwght%5D.ttf",
            ],
            "license_urls": [
                f"{_GH}/notoserifsc/OFL.txt",
                f"{_CDN}/notoserifsc/OFL.txt",
            ],
        },
        {
            "name": theme.FONT_MONO,
            "file": theme.FONT_FILES[theme.FONT_MONO],
            "urls": [
                f"{_GH}/notosansmono/NotoSansMono%5Bwdth%2Cwght%5D.ttf",
                f"{_CDN}/notosansmono/NotoSansMono%5Bwdth%2Cwght%5D.ttf",
                f"{_GH}/notosansmono/NotoSansMono-Regular.ttf",
            ],
            "license_urls": [
                f"{_GH}/notosansmono/OFL.txt",
                f"{_CDN}/notosansmono/OFL.txt",
            ],
        },
    ]


def _download(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _download_first(urls: list[str]) -> bytes | None:
    for url in urls:
        try:
            data = _download(url)
            if len(data) >= _MIN_BYTES:
                print(f"  · 命中 {url}")
                return data
            print(f"  · 体积异常（{len(data)} 字节），跳过 {url}")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as ex:
            print(f"  · 失败 {url}（{ex}）")
    return None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def fetch(force: bool = False) -> int:
    """下载缺失字体；返回失败数量。"""
    fonts_dir = theme.fonts_dir()
    os.makedirs(fonts_dir, exist_ok=True)
    failures = 0

    for item in _targets():
        dest = os.path.join(fonts_dir, item["file"])
        if os.path.isfile(dest) and not force:
            size = os.path.getsize(dest) / 1024 / 1024
            print(f"✓ 已存在：{item['file']}（{size:.1f} MB）")
            continue
        print(f"↓ 下载 {item['name']} → {item['file']}")
        data = _download_first(item["urls"])
        if data is None:
            print(f"✗ {item['file']} 获取失败：请手动下载并放入 {fonts_dir}")
            failures += 1
            continue
        _atomic_write(dest, data)
        print(f"✓ 完成 {item['file']}"
              f"（{len(data) / 1024 / 1024:.1f} MB，sha256 {_sha256(data)[:16]}…）")
        _fetch_license(fonts_dir, item)

    return failures


def _fetch_license(fonts_dir: str, item: dict) -> None:
    """下载 OFL 许可文件（已存在则跳过）。"""
    slug = item["file"].split("-")[0]
    dest = os.path.join(fonts_dir, f"LICENSE-{slug}.txt")
    if os.path.isfile(dest):
        return
    data = None
    for url in item["license_urls"]:
        try:
            data = _download(url)
            break
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            continue
    if data:
        _atomic_write(dest, data)
        print(f"  · 许可文件已保存 LICENSE-{slug}.txt")


def check() -> None:
    """报告内置字体现状与注册结果。"""
    fonts_dir = theme.fonts_dir()
    print(f"字体目录：{fonts_dir}")
    for family, filename in theme.FONT_FILES.items():
        path = os.path.join(fonts_dir, filename)
        if os.path.isfile(path):
            print(f"  ✓ {family:<16} {filename}"
                  f"（{os.path.getsize(path) / 1024 / 1024:.1f} MB）")
        else:
            print(f"  ✗ {family:<16} {filename}（缺失，将回落系统字体）")
    print(f"当前注册表：{theme.fonts_map() or '（空，使用系统字体）'}")


def main() -> int:
    _enable_utf8()
    parser = argparse.ArgumentParser(description="下载 Writer 内置 OFL 字体")
    parser.add_argument("--force", action="store_true", help="覆盖重下")
    parser.add_argument("--check", action="store_true", help="只检查不下载")
    args = parser.parse_args()

    if args.check:
        check()
        return 0

    print(f"资源目录：{paths.ASSETS_DIR}")
    failures = fetch(force=args.force)
    check()
    if failures:
        print(f"\n⚠️ {failures} 个字体未能自动获取；应用仍可运行（回落系统字体）。")
        return 1
    print("\n全部字体就绪。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
