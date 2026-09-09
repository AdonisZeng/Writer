"""章节正文 .md 的增删改查（纯同步函数，调用方用 to_thread / 防抖调度）。

文件归属纪律（方案 5.6）：
- chapters/ 永远只保留当前激活正文；历史草稿归档 .writer/drafts/ch_NN_vN.md
- 文件名是展示序号的派生物：「第N章 标题.md」，number 重算时同步 rename
"""
import os
import re
import time

from core import paths

_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')


def safe_name(title: str) -> str:
    cleaned = _INVALID_CHARS.sub("", (title or "").strip())
    return cleaned[:50] if cleaned else "未命名"


def chapter_filename(number: int, title: str) -> str:
    return f"第{number}章 {safe_name(title)}.md"


def chapter_path(project: str, number: int, title: str) -> str:
    return os.path.join(paths.chapters_dir(project), chapter_filename(number, title))


def find_chapter_file(project: str, number: int, title: str) -> str:
    """精确匹配文件名；外部改名/标题微调时按「第N章 」前缀兜底查找。

    兜底规则：前缀匹配唯一时采用（number 与章节一一对应，可信）；
    多个候选时优先精确标题，仍歧义则不猜测、返回期望路径。
    """
    exact = chapter_path(project, number, title)
    if os.path.exists(exact):
        return exact
    cdir = paths.chapters_dir(project)
    if os.path.isdir(cdir):
        prefix = f"第{number}章 "
        matches = [fn for fn in os.listdir(cdir)
                   if fn.startswith(prefix) and fn.endswith(".md")]
        if len(matches) == 1:
            return os.path.join(cdir, matches[0])
        if len(matches) > 1:
            exact_name = chapter_filename(number, title)
            if exact_name in matches:
                return os.path.join(cdir, exact_name)
    return exact  # 不存在时返回期望路径（供写入）


def read_text(file_path: str) -> str:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, UnicodeDecodeError):
        return ""


def write_text(file_path: str, text: str) -> None:
    """原子写入（tmp + replace），避免外部编辑器读到半截文件。"""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    tmp = file_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, file_path)


def rename_chapter_file(project: str, old_number: int, old_title: str,
                        new_number: int, new_title: str) -> str:
    """number/title 变更时同步重命名 .md（单文件 rename，可预测、可提示）。"""
    old_path = find_chapter_file(project, old_number, old_title)
    new_path = chapter_path(project, new_number, new_title)
    if os.path.exists(old_path) and old_path != new_path:
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        if os.path.exists(new_path):  # 目标名被占（拆章等极端场景）：加后缀避让
            base, ext = os.path.splitext(new_path)
            new_path = f"{base}_1{ext}"
        os.replace(old_path, new_path)
    return new_path


def archive_draft(project: str, number: int, version: int, text: str) -> str:
    """历史版归档到 .writer/drafts/ch_NN_vN.md。"""
    dst = os.path.join(paths.drafts_dir(project), f"ch_{number:03d}_v{version}.md")
    write_text(dst, text)
    return dst


def file_mtime(file_path: str) -> float:
    try:
        return os.path.getmtime(file_path)
    except OSError:
        return 0.0


def finalized_txt_path(project: str, number: int, title: str) -> str:
    """定稿章节投影的纯文本路径（项目根目录，便于备份与外部系统读取）。"""
    return os.path.join(paths.project_dir(project),
                        f"定稿 第{number}章 {safe_name(title)}.txt")


def project_finalized_txt(project: str, number: int, title: str,
                          text: str) -> str:
    dst = finalized_txt_path(project, number, title)
    write_text(dst, text)
    return dst


def remove_finalized_txt(project: str, number: int, title: str) -> None:
    dst = finalized_txt_path(project, number, title)
    if os.path.exists(dst):
        os.remove(dst)


# ==================== 项目级文件 ====================

def write_settings_md(project: str, text: str) -> None:
    write_text(paths.settings_md(project), text)


def read_settings_md(project: str) -> str:
    return read_text(paths.settings_md(project))


def read_outline_md(project: str) -> str:
    return read_text(paths.outline_md(project))


def sync_outline_md(project: str, chapters: list[dict]) -> None:
    """章节表 → outline.md 单向同步（人类可读导出；细纲编辑以数据库为准）。"""
    lines = ["# 章节细纲", ""]
    for ch in chapters:
        lines.append(f"## 第{ch['number']}章 {ch['title']}")
        lines.append(f"- 状态：{ch['status']}")
        if ch.get("role"):
            lines.append(f"- 结构角色：{ch['role']}")
        if ch.get("purpose"):
            lines.append(f"- 核心目的：{ch['purpose']}")
        if ch.get("key_events"):
            lines.append(f"- 关键事件：{ch['key_events']}")
        if ch.get("characters") and ch["characters"] != "[]":
            lines.append(f"- 出场角色：{ch['characters']}")
        if ch.get("notes"):
            lines.append(f"- 定稿要点：{ch['notes']}")
        lines.append("")
    write_text(paths.outline_md(project), "\n".join(lines))
