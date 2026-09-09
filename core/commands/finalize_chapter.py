"""定稿命令（方案 7-⑥ / 5.4.3 / 5.5）：

① 强制过 Canon Gate（is_rewrite=False，语义审查不可跳过）→ BLOCK 则不落状态
② 状态置 finalized
③ 触发定稿后处理管线：chapter_notes(✅) / canon_writeback(❌) / character_state(❌)
   每步持久化到 post_process_steps，支持单步重试（only_failed=True 只补跑失败步骤）
④ 定稿章节额外投影一份纯 .txt 到项目根目录（方案 5.6）
"""
import asyncio
from typing import Callable, Optional

from core import ai_service, canon, db, file_manager
from core.canon import extractor, store


# ==================== Gate ====================

async def gate_before_finalize(project: str, chapter: dict, content: str,
                               model: str) -> dict:
    """定稿前强制过 Gate（此时 is_rewrite=False）。"""
    return await canon.run_gate(project, chapter, content,
                                is_rewrite=False, model=model)


# ==================== 定稿后处理管线 ====================

async def _step_chapter_notes(ctx: dict) -> dict:
    """管线 A：摘要/时间线/伏笔抽取；要点写 chapters.notes（critical）。"""
    chapter = ctx["chapter"]
    run_a = ctx.get("extract_a_fn") or extractor.run_pipeline_a
    result = await run_a(ctx["project"], chapter["number"],
                         chapter["title"], ctx["content"], model=ctx["model"],
                         call_fn=ctx.get("call_fn"))
    ctx["pipeline_a"] = result
    await db.update_chapter(chapter["id"], notes=result.get("summary", ""))
    await db.upsert_summary(chapter["id"], result.get("summary", ""))
    return {"summary_len": len(result.get("summary", ""))}


async def _step_canon_writeback(ctx: dict) -> dict:
    """复用管线 A 结果写回 Canon（时间线/伏笔，含快照）。"""
    result = ctx.get("pipeline_a")
    if result is None:
        raise RuntimeError("管线 A 结果缺失（chapter_notes 未成功执行）")
    return await store.writeback_pipeline_a(ctx["chapter"]["id"], result)


async def _step_character_state(ctx: dict) -> dict:
    """管线 B：角色状态 delta + 新客观事实。"""
    chapter = ctx["chapter"]
    whitelist = await store.get_entity_whitelist()
    result = await extractor.run_pipeline_b(
        ctx["project"], chapter["number"], chapter["title"], ctx["content"],
        whitelist, model=ctx["model"], call_fn=ctx.get("call_fn"))
    return await store.writeback_pipeline_b(ctx["chapter"]["id"], result,
                                            whitelist)


# ==================== P3：compression / style_analysis（每 5 章触发）====================

async def _step_compression(ctx: dict) -> dict:
    """长期记忆压缩（P3，方案 5.5 表）：每 5 章把窗口外旧章要点归档为
    一条压缩摘要（canon_summaries archive_NNN 行），控制上下文膨胀。"""
    chapter = ctx["chapter"]
    if chapter["number"] % 5 != 0:
        return {"skipped": True, "reason": "非第 5 章倍数"}
    chapters = await db.list_chapters()
    old = [c for c in chapters
           if c["order_index"] < chapter["order_index"] - 25
           and (c["notes"] or "").strip()]
    if not old:
        return {"skipped": True, "reason": "窗口外无旧章要点"}
    archives = await db.list_archive_summaries(limit=9999)
    from core import prompt_builder as pb
    system = await pb.build_system_text(ctx["project"])
    blob = "\n".join(f"第{c['number']}章 {c['title']}：{c['notes']}"
                     for c in old)
    user = (f"【本轮任务类型：长期记忆压缩】\n"
            f"把以下旧章要点压缩为一段 ≤300 字的长期记忆归档，"
            f"只保留会影响后续剧情的因果事实、人物变动与未回收伏笔，"
            f"删除场景细节。直接输出归档文本。\n\n{blob}")
    fn = ctx.get("text_call_fn")
    summary = (await fn(ctx["model"],
                        [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
                        purpose="compression", temperature=0.3)
               if fn else await ai_service.call_llm_text(
                   ctx["model"],
                   [{"role": "system", "content": system},
                    {"role": "user", "content": user}],
                   purpose="compression", temperature=0.3))
    archive_id = f"archive_{chapter['number']:03d}"
    await db.upsert_summary(archive_id, summary.strip()[:1200])
    return {"archived_chapters": len(old), "archive_id": archive_id}


async def _step_style_analysis(ctx: dict) -> dict:
    """文风自学习（P3，方案 5.5 表）：每 5 章从最近定稿章提炼文风指令，
    更新 project_core.writing_style（Tier 1 注入）。"""
    chapter = ctx["chapter"]
    if chapter["number"] % 5 != 0:
        return {"skipped": True, "reason": "非第 5 章倍数"}
    chapters = [c for c in await db.list_chapters()
                if c["status"] == "finalized"][-5:]
    if not chapters:
        return {"skipped": True, "reason": "无已定稿章节"}
    from core import file_manager, prompt_builder as pb
    samples = []
    for c in chapters:
        path = file_manager.find_chapter_file(ctx["project"], c["number"],
                                              c["title"])
        text = (await asyncio.to_thread(file_manager.read_text,
                                        path))[:1000]
        if text.strip():
            samples.append(f"《{c['title']}》开头：\n{text}")
    if not samples:
        return {"skipped": True, "reason": "无可用样章文本"}
    system = await pb.build_system_text(ctx["project"])
    user = (f"【本轮任务类型：文风自学习】\n"
            f"阅读以下最近定稿章节的开头片段，提炼作者当前文风特征"
            f"（句长节奏 / 叙事人称与视角习惯 / 对话与描写比例 / "
            f"意象与用词偏好），输出一段 ≤200 字的「文风指令」——"
            f"用描述性祈使句写成，供后续创作直接遵循。"
            f"直接输出指令文本，不要分析。\n\n" + "\n\n".join(samples))
    fn = ctx.get("text_call_fn")
    style = (await fn(ctx["model"],
                      [{"role": "system", "content": system},
                       {"role": "user", "content": user}],
                      purpose="style", temperature=0.3)
             if fn else await ai_service.call_llm_text(
                 ctx["model"],
                 [{"role": "system", "content": system},
                  {"role": "user", "content": user}],
                 purpose="style", temperature=0.3))
    style = style.strip()[:600]
    await db.update_project(writing_style=style)
    return {"writing_style_len": len(style)}


async def run_post_process(project: str, chapter: dict, content: str,
                           model: str, only_failed: bool = False,
                           on_log: Optional[Callable[[str], None]] = None,
                           call_fn: Optional[Callable] = None,
                           extract_a_fn: Optional[Callable] = None,
                           text_call_fn: Optional[Callable] = None) -> dict:
    """执行后处理管线。only_failed=True 时跳过已成功步骤（单步重试入口）。

    返回 {"ok", "steps": {key: {"ok","error"}}, "self_check"}。
    call_fn / extract_a_fn / text_call_fn 为测试注入点（DI）。
    """
    log = on_log or (lambda m: None)
    steps = [
        ("chapter_notes", "抽取本章剧情要点", _step_chapter_notes, True),
        ("canon_writeback", "写回时间线与伏笔", _step_canon_writeback, False),
        ("character_state", "写回角色状态与新事实", _step_character_state,
         False),
        ("compression", "长期记忆压缩（每5章）", _step_compression, False),
        ("style_analysis", "文风自学习（每5章）", _step_style_analysis,
         False),
    ]
    ctx = {"project": project, "chapter": chapter, "content": content,
           "model": model, "pipeline_a": None,
           "call_fn": call_fn, "extract_a_fn": extract_a_fn,
           "text_call_fn": text_call_fn}
    results, ok_all = {}, True
    for key, name, executor, critical in steps:
        if only_failed:
            rec = await db.get_pp_step(chapter["id"], key)
            if rec and rec["ok"]:
                continue                                  # 修复模式跳过
        try:
            data = await executor(ctx)
            await db.record_pp_step(chapter["id"], key, ok=True,
                                    critical=critical)
            results[key] = {"ok": True, "error": "", "data": data}
            log(f"✓ 后处理·{name}")
        except Exception as e:
            await db.record_pp_step(chapter["id"], key, ok=False,
                                    critical=critical, error_msg=str(e))
            results[key] = {"ok": False, "error": str(e)}
            ok_all = False
            log(f"✗ 后处理·{name}：{e}")
            if critical:
                log("关键步骤失败，后处理中止（可重跑定稿后处理）")
                break
    # 写回自检：纯 SQL 完整性检查（方案 5.4.2）
    problems = await store.writeback_self_check(chapter["id"])
    if problems:
        log(f"⚠️ 写回自检发现异常：{problems}")
    return {"ok": ok_all, "steps": results, "self_check": problems}


# ==================== 定稿 / 回滚入口 ====================

async def finalize(project: str, chapter_id: str, model: str, *,
                   on_log: Optional[Callable[[str], None]] = None,
                   call_fn: Optional[Callable] = None,
                   extract_a_fn: Optional[Callable] = None) -> dict:
    """定稿主流程：Gate → 状态 → 后处理 → txt 投影。

    返回 {"ok", "verdict", "issues", "post"}；BLOCK 时状态不变。
    call_fn / extract_a_fn 为测试注入点（DI）。
    """
    log = on_log or (lambda m: None)
    chapter = await db.get_chapter(chapter_id)
    if chapter is None:
        return {"ok": False, "verdict": "MISSING", "issues": [], "post": None}
    path = file_manager.find_chapter_file(project, chapter["number"],
                                          chapter["title"])
    content = await asyncio.to_thread(file_manager.read_text, path)

    log("🛡️ 定稿前 Canon Gate 校验中…")
    gate = await gate_before_finalize(project, chapter, content, model)
    if gate["verdict"] == "BLOCK":
        log("⛔ Gate BLOCK：存在确定性错误，定稿中止")
        return {"ok": False, "verdict": "BLOCK", "issues": gate["issues"],
                "post": None}

    await db.update_chapter(chapter_id, status="finalized")
    chapter = await db.get_chapter(chapter_id)
    log("✓ 状态已置为 finalized，开始定稿后处理…")
    post = await run_post_process(project, chapter, content, model,
                                  on_log=log, call_fn=call_fn,
                                  extract_a_fn=extract_a_fn)
    await asyncio.to_thread(file_manager.project_finalized_txt,
                            project, chapter["number"], chapter["title"],
                            content)
    from core.commands import chapters
    await chapters.sync_outline(project)
    # RAG：定稿后自动索引本章（P2，方案 5.3 with_rag_context 数据源）
    from core import config
    if config.get("rag_enabled"):
        try:
            from core import rag
            n = await rag.index_chapter(project, chapter_id, content)
            if n:
                log(f"✓ 知识库索引更新（{n} 块）")
        except Exception as e:
            log(f"⚠️ 知识库索引失败：{e}")
    log("✓ 定稿完成（含 .txt 投影）")
    return {"ok": True, "verdict": "PASS", "issues": gate["issues"],
            "post": post}


async def get_latest_finalized() -> Optional[dict]:
    """最新定稿章（仅允许回滚它，方案 5.5）。"""
    chapters = await db.list_chapters()
    finalized = [c for c in chapters if c["status"] == "finalized"]
    return finalized[-1] if finalized else None
