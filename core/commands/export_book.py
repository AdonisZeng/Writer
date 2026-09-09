"""导出（P3，方案 阶段规划表）：全本合并导出为纯文本。

- 输出：novels/<项目>/导出/<书名>_全本_<日期>.txt
- 结构：书名/流派/导出时间 → 每章「第N章 标题」+ 正文（定稿章在前排序不变）
- 细纲另存 outline.md 已由章节表单向同步（方案 5.6），此处不再重复
正文永远保持纯文本（设计原则 1），导出只是投影。
"""
import asyncio
import os
from datetime import datetime

from core import db, file_manager, paths


async def export_book(project: str, *,
                      only_finalized: bool = False,
                      include_outline: bool = True) -> dict:
    """全本导出。返回 {"ok", "path", "chapters", "words"}。"""
    chapters = await db.list_chapters()
    if only_finalized:
        chapters = [c for c in chapters if c["status"] == "finalized"]
    if not chapters:
        return {"ok": False, "message": "没有可导出的章节",
                "path": "", "chapters": 0, "words": 0}
    proj = await db.get_project()
    title = (proj or {}).get("title") or project
    lines = [
        f"《{title}》",
        f"流派：{(proj or {}).get('genre', '') or '未设定'}"
        if (proj or {}).get("genre") else "",
        f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"章节数：{len(chapters)}"
        + ("（仅定稿）" if only_finalized else ""),
        "",
        "=" * 32,
        "",
    ]
    total_words = 0
    for ch in chapters:
        path = file_manager.find_chapter_file(project, ch["number"],
                                              ch["title"])
        text = await asyncio.to_thread(file_manager.read_text, path)
        text = text.strip()
        lines.append(f"第{ch['number']}章 {ch['title']}")
        lines.append("")
        lines.append(text if text else "（本章暂无正文）")
        lines.append("")
        lines.append("-" * 24)
        lines.append("")
        total_words += len(text)
    if include_outline:
        outline = file_manager.read_outline_md(project)
        if outline.strip():
            lines.append("附录：章节细纲")
            lines.append("")
            lines.append(outline)

    out_dir = os.path.join(paths.project_dir(project), "导出")
    os.makedirs(out_dir, exist_ok=True)
    suffix = "定稿" if only_finalized else "全本"
    out_path = os.path.join(
        out_dir, f"{file_manager.safe_name(title)}_{suffix}_"
                 f"{datetime.now().strftime('%Y%m%d')}.txt")
    content = "\n".join(lines)

    def w():
        tmp = out_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, out_path)
    await asyncio.to_thread(w)
    return {"ok": True, "path": out_path, "chapters": len(chapters),
            "words": total_words}
