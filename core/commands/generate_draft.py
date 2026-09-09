"""生成草稿命令：组装上下文 → Token 预检 → 流式生成（方案 7-④）。

取消时抛 GenerationCancelled（携带半成品），由 UI 预览区保留。
"""
import asyncio
import json
from typing import Callable, Optional

from core import ai_service, db, prompt_builder


async def execute(project: str, chapter: dict, user_guidance: str,
                  model: str, on_chunk: Callable[[str], None],
                  cancel_event: asyncio.Event,
                  on_thought: Optional[Callable[[str], None]] = None,
                  on_log: Optional[Callable[[str], None]] = None):
    """执行生成，返回 GenStats。参数按方案 5.2.4：正文草稿 temperature 0.9、关思考。"""
    built = await prompt_builder.build_draft_messages(
        project, chapter, user_guidance)
    for warn in built["warnings"]:
        if on_log:
            on_log(f"⚠️ {warn}")
    tiers = built["tiers"]
    if on_log:
        on_log(f"上下文装配完成：Tier1 {tiers['tier1']} / Tier2 {tiers['tier2']} "
               f"/ Tier3 {tiers['tier3']} tokens（预算 {built['budget']}）")

    stats = await ai_service.call_llm_stream(
        model=model,
        messages=built["messages"],
        on_chunk=on_chunk,
        purpose="draft",
        cancel_event=cancel_event,
        temperature=0.9,
        on_thought=on_thought,
    )
    return {"stats": stats, "tiers": tiers, "budget": built["budget"]}
