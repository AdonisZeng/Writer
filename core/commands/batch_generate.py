"""批量生成（P3，方案 阶段规划表）：从当前章起连续生成 N 章草稿。

- 每章走完整生成链（上下文装配 → 流式生成 → 确定性 Gate → 落盘草稿）
- 语义审查在批量模式跳过（逐章手动审查体验更佳，可单独触发）
- 产出直接写入正文并记 drafts（状态 drafted，不自动定稿）
- cancel_event 支持随时中止（Esc / 软停止）
"""
import asyncio
from typing import Callable, Optional

from core import ai_service, canon, prompt_builder
from core.commands import save_draft


async def run_batch(project: str, start_chapter_id: str, count: int,
                    model: str, *, guidance: str = "",
                    cancel_event: Optional[asyncio.Event] = None,
                    on_log: Optional[Callable[[str], None]] = None,
                    on_chapter_done: Optional[Callable[[dict, str], None]]
                    = None) -> dict:
    """连续生成 count 章。返回 {"done": n, "chapters": [...], "cancelled": bool}"""
    log = on_log or (lambda m: None)
    chapters = await db.list_chapters()
    try:
        start_idx = next(i for i, c in enumerate(chapters)
                         if c["id"] == start_chapter_id)
    except StopIteration:
        return {"done": 0, "chapters": [], "cancelled": False}
    targets = chapters[start_idx:start_idx + count]
    done, cancelled = 0, False
    results = []
    for ch in targets:
        if cancel_event and cancel_event.is_set():
            cancelled = True
            log("■ 批量生成已中止")
            break
        log(f"▶ 批量 [{done + 1}/{len(targets)}]：第{ch['number']}章 "
            f"《{ch['title']}》")
        try:
            built = await prompt_builder.build_draft_messages(
                project, ch, guidance)
            buf: list[str] = []
            stats = await ai_service.call_llm_stream(
                model=model, messages=built["messages"],
                on_chunk=buf.append, purpose="draft",
                cancel_event=cancel_event, temperature=0.9)
            text = stats.text
            # 确定性 Gate（批量模式跳过语义审查）
            gate = await canon.run_gate(project, ch, text, semantic=False)
            if gate["verdict"] == "BLOCK":
                log(f"⛔ 第{ch['number']}章被 Gate 阻断："
                    f"{[i.message for i in gate['issues'] if i.severity == 'error']}")
                results.append({"chapter": ch, "ok": False, "blocked": True})
                continue
            await save_draft.execute(project, ch, text)
            log(f"✓ 第{ch['number']}章 草稿已落盘（{len(text)} 字）")
            done += 1
            results.append({"chapter": ch, "ok": True, "text": text})
            if on_chapter_done:
                on_chapter_done(ch, text)
        except ai_service.GenerationCancelled:
            cancelled = True
            log("■ 批量生成已中止（保留已完成章节）")
            break
        except Exception as ex:
            log(f"✗ 第{ch['number']}章 生成失败：{ex}")
            results.append({"chapter": ch, "ok": False, "error": str(ex)})
    return {"done": done, "chapters": results, "cancelled": cancelled}
