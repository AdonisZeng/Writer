"""Writer MCP Server（P3，方案 阶段规划表：MCP）。

以 stdio 方式向外部 AI 助手（Claude Desktop / Cursor 等）暴露小说项目的
只读数据工具，便于在 Writer 之外进行查询与辅助分析。

启动：python -m core.mcp_server
接入示例（Claude Desktop claude_desktop_config.json）：
    "writer": {
        "command": "python",
        "args": ["d:/Development/Python/Writer/core/mcp_server.py"]
    }

只读设计：不提供任何写工具——正文与设定的修改仍以 Writer 为唯一入口，
保证 Canon 写回链路的完整性。
"""
import asyncio
import json
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from core import paths  # noqa: E402

mcp = MCPServer(
    "writer",
    instructions=(
        "Writer 桌面小说创作工具的只读数据接口。"
        "项目存放于 Writer/novels/ 下；每个项目自带 state.db（Canon/版本/用量）。"
    ),
)


def _resolve_project(project: Optional[str]) -> str:
    from core import config
    name = project or config.get("project", "")
    if not name or not os.path.isdir(paths.project_dir(name)):
        raise ValueError(f"项目不存在：{name}（可用 list_projects 查询）")
    return name


def _run(coro):
    return asyncio.run(coro)


@mcp.tool()
def list_projects() -> str:
    """列出 novels/ 下的全部小说项目。"""
    from core import project
    return json.dumps({"projects": project.list_projects()},
                      ensure_ascii=False)


@mcp.tool()
def list_chapters(project: Optional[str] = None) -> str:
    """列出项目的章节（编号/标题/状态/字数/POV/要点）。"""
    from core import db, project as proj_mod

    async def _q():
        name = _resolve_project(project)
        await db.open(paths.state_db(name))
        try:
            chapters = await db.list_chapters()
            return [{"number": c["number"], "title": c["title"],
                     "status": c["status"], "word_count": c["word_count"],
                     "pov": c["pov"], "notes": c["notes"][:120]}
                    for c in chapters]
        finally:
            await db.close()
    return json.dumps({"chapters": _run(_q())}, ensure_ascii=False)


@mcp.tool()
def get_chapter(number: int, project: Optional[str] = None) -> str:
    """读取某一章正文全文（number 为展示章号）。"""
    from core import db, file_manager, paths

    async def _q():
        name = _resolve_project(project)
        await db.open(paths.state_db(name))
        try:
            chapters = await db.list_chapters()
            ch = next((c for c in chapters if c["number"] == number), None)
            if ch is None:
                return {"error": f"第{number}章不存在"}
            text = file_manager.read_text(
                file_manager.find_chapter_file(name, ch["number"],
                                               ch["title"]))
            return {"number": ch["number"], "title": ch["title"],
                    "status": ch["status"], "text": text}
        finally:
            await db.close()
    return json.dumps(_run(_q()), ensure_ascii=False)


@mcp.tool()
def get_canon(project: Optional[str] = None) -> str:
    """读取项目 Canon 状态：角色动态状态 / 活跃伏笔 / 客观事实 / 最近摘要。"""
    from core.canon import store

    async def _q():
        name = _resolve_project(project)
        from core import db, paths
        await db.open(paths.state_db(name))
        try:
            chapters = await db.list_chapters()
            bundle = await store.get_canon_bundle(
                chapters[-1] if chapters else
                {"id": "", "order_index": 0})
            number_by_id = {c["id"]: c["number"] for c in chapters}
            return {
                "characters": [
                    {"name": s["character"],
                     "chapter": number_by_id.get(s["chapter_id"]),
                     "location": s["location"], "level": s["level"],
                     "state": s["state"], "items": s["items"]}
                    for s in bundle["char_states"]],
                "plot_lines": bundle["plot_lines"],
                "facts": bundle["facts"],
                "recent_summaries": [
                    {"chapter": s["number"], "summary": s["summary"]}
                    for s in bundle["summaries"]],
            }
        finally:
            await db.close()
    return json.dumps(_run(_q()), ensure_ascii=False, default=str)


@mcp.tool()
def get_usage(project: Optional[str] = None) -> str:
    """读取项目 LLM 用量统计（按用途×模型聚合）。"""
    from core import db, paths

    async def _q():
        name = _resolve_project(project)
        await db.open(paths.state_db(name))
        try:
            rows = await db.usage_by_purpose()
            return {"usage": rows}
        finally:
            await db.close()
    return json.dumps(_run(_q()), ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
