"""起步引导：从「一句话想法」逐步打磨成完整作品框架的四步流程。

四步：故事内核 → 世界观 → 结构大纲 → 人物（人物压轴：等内核、世界、骨架都
定型后再填人，角色才真正服务于故事）。每步给出目标与可采纳目标类型，
UI 据此渲染步骤条并切换「采纳」菜单；进度持久化到 design_guide 表（可续做）。

另定义「作者参与程度」三档（AI 提问频率），分步引导时注入当轮聚焦块。
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
        "adopt_targets": ["premise", "synopsis", "genre", "theme"],
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
    {
        "key": "cast",
        "label": "人物",
        "goal": "确定主角、对手与关键配角，讲清动机、能力与人物弧光",
        "adopt_targets": ["character"],
    },
]

STEP_KEYS: list[str] = [s["key"] for s in GUIDE_STEPS]
_FIRST_STEP = STEP_KEYS[0]

# 作者参与程度（分步引导时 AI 的提问频率）：high=每轮都问（默认）/
# medium=仅重要方向抉择时问 / low=仅本步完成确认时问
PARTICIPATION_LEVELS: dict[str, dict] = {
    "high": {
        "label": "高频 · 每轮讨论都向我提问",
        "directive": "【作者参与程度：高】每轮回复都围绕本步目标向作者提出需要"
                     "回答的关键问题（可配合 choice 选项），通过持续问答共同"
                     "打磨方案；一次最多问 1~2 个问题，避免连环轰炸。",
    },
    "medium": {
        "label": "中频 · 仅重要方向时提问",
        "directive": "【作者参与程度：中】平时自主推进、直接给出成体系的建议，"
                     "不要每轮都提问；仅在需要拍板影响后续多步的重要方向"
                     "（主线走向、世界观根基、结局取舍等）时才向作者提问。",
    },
    "low": {
        "label": "低频 · 仅本步完成时确认",
        "directive": "【作者参与程度：低】尽量不提问，自主给出完整方案供作者"
                     "挑选采纳；仅当本步设计已基本完成、需要作者确认收尾并"
                     "进入下一步时，才向作者提问。",
    },
}


def participation_directive(level: str = "") -> str:
    """按参与程度档位返回注入引导聚焦块的提问频率指令。"""
    return PARTICIPATION_LEVELS.get(level, {}).get(
        "directive", PARTICIPATION_LEVELS["high"]["directive"])


def step_by_key(key: str) -> Optional[dict]:
    return next((s for s in GUIDE_STEPS if s["key"] == key), None)


def next_step(key: str) -> Optional[str]:
    """返回 key 的下一步；已是最后一步或非法 key 返回 None。"""
    if key not in STEP_KEYS:
        return None
    i = STEP_KEYS.index(key)
    return STEP_KEYS[i + 1] if i + 1 < len(STEP_KEYS) else None


# 步骤 key 语义归一：模型可能自造/翻译 key（如 "worldview"、"故事内核"）
_STEP_KEY_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("core", ("core", "story", "premise", "内核", "故事")),
    ("world", ("world", "setting", "世界观", "世界")),
    ("structure", ("structure", "outline", "plot", "结构", "大纲")),
    ("cast", ("cast", "character", "role", "人物", "角色")),
]


def resolve_step_key(raw: str) -> str:
    """把（模型给出的）步骤标记归一到枚举 key；无法识别返回空串。"""
    text = (raw or "").strip().lower()
    if not text:
        return ""
    if text in STEP_KEYS:
        return text
    for key, hints in _STEP_KEY_HINTS:
        if any(h in text for h in hints):
            return key
    return ""


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
    """标记某步完成；仅当它仍是当前步时才把进度推进到下一步。

    回看旧步骤再点「标记完成」不应把进度往回拽（如已完成 core/world、
    正在做 structure 时回到 core 补记，current_step 必须保持 structure）。
    """
    prog = await load_progress()
    if step_key not in STEP_KEYS:
        return prog
    done = list(dict.fromkeys(prog["steps_done"] + [step_key]))
    cur = prog["current_step"]
    nxt = (next_step(step_key) or step_key) if cur == step_key else cur
    return await save_progress(idea=prog["idea"], current_step=nxt,
                               steps_done=done)
