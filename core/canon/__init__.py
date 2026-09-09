"""Canon 叙事一致性模块门面（方案 5.4）。

- build_canon_context：按 5.4.1 注入优先级渲染 Canon 块（prompt_builder 调用）
- build_pov_block：POV 信息差硬约束块（5.4.6）
- run_gate：两态闸门（5.4.3）——第一层确定性检查唯一拥有 BLOCK 权力；
  第二层语义审查仅产出诊断报告（异步、可跳过），一律提示不修改
"""
from typing import Optional

from core import config, db
from core.canon import context, reviewer, store, validator
from core.canon.validator import Issue


async def build_canon_context(chapter_id: str) -> str:
    """渲染 Canon 块（当前章之前的正史状态）。"""
    chapter = await db.get_chapter(chapter_id)
    if chapter is None:
        return ""
    bundle = await store.get_canon_bundle(chapter)
    chapters = await db.list_chapters()
    number_by_id = {c["id"]: c["number"] for c in chapters}
    return context.render_canon_context(bundle, number_by_id)


async def build_pov_block(chapter_id: str) -> str:
    """POV 信息差块；未设置 POV 返回空串。"""
    chapter = await db.get_chapter(chapter_id)
    if chapter is None:
        return ""
    pov = await store.get_pov_bundle(chapter)
    return context.render_pov_block(pov)


async def build_deterministic_issues(chapter: dict, content: str = "",
                                     scope: str = "gate") -> list[Issue]:
    """确定性检查（纯算法零成本）。

    scope="gate"：只做本章范围的检查（字数上限 / 定稿元数据 / 伏笔休眠 info），
    其他章节的元数据错误不阻断本章；
    scope="scan"：全项目元数据硬检查（供诊断面板扫描）。
    """
    from core import config
    chapters = await db.list_chapters()
    if scope == "scan":
        return validator.check_chapter_metadata(
            chapters, word_limit=config.get("chapter_word_limit", 8000))

    issues: list[Issue] = []
    word_limit = config.get("chapter_word_limit", 8000)
    text_len = len(content) if content else chapter["word_count"]
    if text_len > word_limit:
        issues.append(Issue(
            "error", "word_limit_exceeded",
            f"字数硬上限：本章 {text_len} 字超过上限 {word_limit}——建议拆章",
            chapter_id=chapter["id"]))
    if chapter["status"] == "finalized":
        missing = []
        if not chapter["number"]:
            missing.append("章号")
        if not (chapter.get("title") or "").strip():
            missing.append("标题")
        if not (chapter.get("notes") or "").strip():
            missing.append("定稿要点（notes）")
        if missing:
            issues.append(Issue(
                "error", "finalized_metadata_missing",
                f"定稿关键元数据缺失：{'、'.join(missing)}",
                chapter_id=chapter["id"]))
    number_by_id = {c["id"]: c["number"] for c in chapters}
    issues += validator.check_plot_dormancy(
        await db.list_plot_lines(), number_by_id,
        chapter["number"], config.get("plot_dormant_threshold", 25))
    # 敏感词预检（P3，info 级）：有词表时基于传入内容检查
    if content:
        from core import sensitivity
        words = sensitivity.load_words(config.get("project", ""))
        if words:
            issues += sensitivity.check(content, words,
                                        chapter_id=chapter["id"])
    return issues


async def scan_project_issues() -> list[Issue]:
    """全项目元数据扫描（诊断面板数据源）。"""
    return await build_deterministic_issues({"id": "", "number": 0,
                                             "status": "outlined",
                                             "word_count": 0, "title": ""},
                                            scope="scan")


async def run_gate(project: str, chapter: dict, content: str, *,
                   is_rewrite: bool = False, model: str = "",
                   semantic: bool = True) -> dict:
    """两态闸门（方案 5.4.3）。

    返回 {"verdict": "BLOCK"|"PASS", "issues": [Issue, ...], "report": {...}}
    - BLOCK：存在 error 级问题，新稿不落盘
    - PASS：附带全部 warning/info 诊断供侧边栏逐条采纳/忽略
    """
    deterministic = await build_deterministic_issues(chapter, content)
    if any(i.severity == "error" for i in deterministic):
        return {"verdict": "BLOCK",
                "issues": deterministic,
                "report": {"skipped": True}}
    if not semantic:
        return {"verdict": "PASS", "issues": deterministic,
                "report": {"skipped": True}}

    canon_ctx = await build_canon_context(chapter["id"])
    pov_block = await build_pov_block(chapter["id"])
    report = await reviewer.semantic_review(
        project, chapter["id"], content, canon_ctx, pov_block, model=model)
    return {"verdict": "PASS", "issues": deterministic + report,
            "report": {"skipped": False}}
