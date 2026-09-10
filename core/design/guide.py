"""起步引导：从「一句话想法」逐步打磨成完整作品框架的四步流程。

四步：故事内核 → 人物 → 世界观 → 结构大纲。每步给出目标与可采纳目标类型，
UI 据此渲染步骤条并切换「采纳」菜单；进度持久化到 design_guide 表（可续做）。
"""
from __future__ import annotations

import json
from typing import Optional

from core import db

# 引导四步：key / 展示名 / 本轮目标 / 可采纳目标类型（UI 采纳菜单据此切换）
GUIDE_STEPS: list[dict] = [
    {
        "key": "core",
        "label": "故事内核",
        "goal": "把一句话想法打磨成清晰的故事核：类型定位、一句话前提、"
                "主题立意与故事梗概",
        "adopt_targets": ["premise", "synopsis", "genre"],
    },
    {
        "key": "cast",
        "label": "人物",
        "goal": "确定主角、对手与关键配角，讲清动机、能力与人物弧光",
        "adopt_targets": ["character"],
    },
    {
        "key": "world",
        "label": "世界观",
        "goal": "搭建世界底层法则、力量或科技体系、势力格局与时代背景",
        "adopt_targets": ["world"],
    },
    {
        "key": "structure",
        "label": "结构大纲",
        "goal": "拆出分卷与章节大纲，形成可以开写的全书骨架",
        "adopt_targets": ["outline"],
    },
]

STEP_KEYS: list[str] = [s["key"] for s in GUIDE_STEPS]
_FIRST_STEP = STEP_KEYS[0]


def step_by_key(key: str) -> Optional[dict]:
    return next((s for s in GUIDE_STEPS if s["key"] == key), None)


def next_step(key: str) -> Optional[str]:
    """返回 key 的下一步；已是最后一步或非法 key 返回 None。"""
    if key not in STEP_KEYS:
        return None
    i = STEP_KEYS.index(key)
    return STEP_KEYS[i + 1] if i + 1 < len(STEP_KEYS) else None


async def load_progress() -> dict:
    """读取引导进度；无记录时返回默认（未开始）。"""
    row = await db.get_design_guide()
    if not row:
        return {"idea": "", "current_step": _FIRST_STEP, "steps_done": []}
    try:
        done = json.loads(row.get("steps_done") or "[]")
    except (json.JSONDecodeError, TypeError):
        done = []
    return {
        "idea": row.get("idea", "") or "",
        "current_step": row.get("current_step") or _FIRST_STEP,
        "steps_done": [k for k in done if k in STEP_KEYS],
    }


async def save_progress(*, idea: str, current_step: str,
                        steps_done: list[str]) -> dict:
    """写入引导进度（全量），返回规范化后的进度。"""
    done = [k for k in dict.fromkeys(steps_done or []) if k in STEP_KEYS]
    cur = current_step if current_step in STEP_KEYS else _FIRST_STEP
    await db.upsert_design_guide(
        idea=idea or "", current_step=cur,
        steps_done=json.dumps(done, ensure_ascii=False))
    return {"idea": idea or "", "current_step": cur, "steps_done": done}


async def mark_done(step_key: str) -> dict:
    """标记某步完成并把进度推进到下一步，返回最新进度。"""
    prog = await load_progress()
    if step_key not in STEP_KEYS:
        return prog
    done = list(dict.fromkeys(prog["steps_done"] + [step_key]))
    nxt = next_step(step_key) or step_key
    return await save_progress(idea=prog["idea"], current_step=nxt,
                               steps_done=done)
