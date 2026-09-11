"""步收尾采纳：把 AI 的「本步完整方案」写入设定库（配合引导收尾 choice）。

两条路径（与 `context` 的收尾约定一致）：
- 世界观步：AI 直接给出分节计划（sections: label + 该节完整内容）→ label 匹配
  已有分节则覆盖写入，未匹配则**新建分节**（key 自动生成）。分节由 AI 按题材
  自由规划，不受默认分节限制；
- 其余步（故事内核）：按该步 adopt_targets 逐字段提炼（design_extract）后写入
  `project_core`——作者点「直接采纳」即视为确认，跳过二次预览。

返回结构化回执（written / skipped），UI 据此渲染"写入了什么"并把引导进度推进
到下一步。提炼失败/为空一律跳过该字段，绝不阻断整个动作。
"""
from __future__ import annotations

import asyncio
import re
import uuid
from typing import Optional

from core import db
from core.design import guide, refine, world

# project_core 中可经「步收尾」采纳的字段（core 步 adopt_targets 子集）
_FIELD_TARGETS = ("premise", "theme", "synopsis", "genre")

# 多字段提炼的并发度：本地后端一般逐请求排队，2 路既省等待又不至于一次
# 压满（4 个请求同时排队反而更容易触发本地后端拒单）
_EXTRACT_CONCURRENCY = 2


async def apply_step_adoption(project: str, step_key: str, text: str, *,
                              sections: Optional[list[dict]] = None,
                              model: str = "", section_key: str = "",
                              call_fn=None) -> dict:
    """执行一步的收尾采纳，返回回执：

    {"kind": "fields"|"sections",
     "written": [{"label", ...}], "skipped": [{"label", ...}]}

    call_fn 供测试注入（透传给 refine.extract_for_target）。
    """
    if step_key == "world":
        return await _adopt_world(project, sections or [], text=text,
                                  model=model, section_key=section_key,
                                  call_fn=call_fn)
    return await _adopt_fields(project, step_key, text, model=model,
                               call_fn=call_fn)


async def _adopt_fields(project: str, step_key: str, text: str, *,
                        model: str = "", call_fn=None) -> dict:
    """故事内核等步：逐字段提炼（B 方案）后覆盖写入 project_core。

    多字段并发提炼（受 _EXTRACT_CONCURRENCY 限流），结果按目标顺序归位；
    单个字段提炼失败/为空只跳过它自己，不影响其余字段。
    """
    st = guide.step_by_key(step_key) or {}
    targets = [t for t in (st.get("adopt_targets") or [])
               if t in _FIELD_TARGETS]
    proj = await db.get_project() or {}
    sem = asyncio.Semaphore(_EXTRACT_CONCURRENCY)

    async def _extract(target: str) -> str:
        async with sem:
            try:
                return await refine.extract_for_target(
                    project, target, text,
                    current=str(proj.get(target) or ""), model=model,
                    call_fn=call_fn)
            except Exception:
                return ""

    values = await asyncio.gather(*(_extract(t) for t in targets),
                                  return_exceptions=True)
    written: list[dict] = []
    skipped: list[dict] = []
    for target, value in zip(targets, values):
        refined = value.strip() if isinstance(value, str) else ""
        label = refine.target_label(target)
        if refined:
            await db.update_project(**{target: refined})
            written.append({"target": target, "label": label})
        else:
            skipped.append({"target": target, "label": label})
    return {"kind": "fields", "written": written, "skipped": skipped}


async def _adopt_world(project: str, sections: list[dict], *, text: str,
                       model: str = "", section_key: str = "",
                       call_fn=None) -> dict:
    """世界观步：按 AI 的分节计划写入（匹配→覆盖，未匹配→新建）。"""
    existing = await world.load_sections(project)
    if not sections:
        return await _adopt_world_fallback(
            project, text, existing, model=model, section_key=section_key,
            call_fn=call_fn)
    written: list[dict] = []
    skipped: list[dict] = []
    # 新建分节排序：接在现有分节之后，避免与默认节占位冲突
    order = max([float(s.get("order_index") or 0) for s in existing] or [0.0])
    for item in sections:
        label = str(item.get("label") or "").strip()
        content = str(item.get("content") or "").strip()
        if not label:
            continue
        if not content:
            skipped.append({"label": label, "reason": "内容为空"})
            continue
        matched = _match_section(existing, label)
        if matched:
            await db.upsert_world_section(matched["section_key"],
                                          content=content)   # 覆盖写入
            written.append({"label": matched.get("label") or label,
                            "action": "updated",
                            "section_key": matched["section_key"]})
        else:
            order += 0.1
            key = "ai_" + uuid.uuid4().hex[:6]
            await db.upsert_world_section(key, label=label, content=content,
                                          order_index=order)
            existing.append({"section_key": key, "label": label})
            written.append({"label": label, "action": "created",
                            "section_key": key})
    await world.sync_settings_file(project)
    return {"kind": "sections", "written": written, "skipped": skipped}


async def _adopt_world_fallback(project: str, text: str,
                                existing: list[dict], *, model: str = "",
                                section_key: str = "", call_fn=None) -> dict:
    """AI 未按协议给出分节计划时的兜底：提炼为世界观文本，追加进目标分节。"""
    target = section_key or (existing[0]["section_key"] if existing else "")
    empty = {"kind": "sections", "written": [],
             "skipped": [{"label": "分节计划", "reason": "无法定位目标分节"}]}
    if not target or not (text or "").strip():
        return empty
    try:
        refined = await refine.extract_for_target(
            project, "world", text, model=model, call_fn=call_fn)
    except Exception:
        refined = ""
    if not refined:
        empty["skipped"] = [{"label": "分节计划", "reason": "提炼结果为空"}]
        return empty
    cur = next((s.get("content") or "" for s in existing
                if s["section_key"] == target), "")
    content = f"{cur.rstrip()}\n\n{refined}" if cur.strip() else refined
    label = next((s.get("label") or "" for s in existing
                  if s["section_key"] == target), "")
    await db.upsert_world_section(target, label=label, content=content)
    await world.sync_settings_file(project)
    return {"kind": "sections",
            "written": [{"label": label or target, "action": "updated",
                         "section_key": target, "fallback": True}],
            "skipped": []}


def _match_section(sections: list[dict], label: str) -> Optional[dict]:
    """按 label 匹配已有分节：规范化后精确 → 互为包含（≥2 字）。"""
    target = _norm(label)
    if not target:
        return None
    for s in sections:
        if _norm(s.get("label") or "") == target:
            return s
    if len(target) >= 2:
        for s in sections:
            cur = _norm(s.get("label") or "")
            if len(cur) >= 2 and (cur in target or target in cur):
                return s
    return None


def _norm(text: str) -> str:
    return re.sub(r"[\s·/、，,（）()]+", "", text or "")
