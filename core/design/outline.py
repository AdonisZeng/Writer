"""结构大纲：AI 结构化产出契约、解析与生成。

用 `ai_service.call_llm_json` + `json_schema` 约束模型输出合法 JSON
（LM Studio 在 token 层做语法掩码）；解析后规范化为统一结构，供
`core/commands/apply_outline.py` 批量写入 chapters 表。
"""
from __future__ import annotations

from core import ai_service, db, prompt_builder

# AI 结构化产出契约：分卷 → 章节细纲
OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "volumes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "chapters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "role": {"type": "string"},
                                "purpose": {"type": "string"},
                                "key_events": {"type": "string"},
                                "characters": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["title"],
                        },
                    },
                },
                "required": ["title", "chapters"],
            },
        }
    },
    "required": ["volumes"],
}

_FALLBACK_SYSTEM = (
    "你是一位资深的长篇小说结构策划。请依据作者已确立的设定，输出分卷与章节大纲，"
    "要求：主线清晰、节奏递进、伏笔有埋有收、每章有明确的叙事作用；"
    "只输出结构化结果，不要解释。"
)


def _as_list(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        norm = raw.replace("，", "、").replace(",", "、")
        return [s.strip() for s in norm.split("、") if s.strip()]
    return []


def parse_outline(data: dict) -> dict:
    """规范化 AI 返回的大纲为 {volumes:[{title,summary,chapters:[...]}]}。"""
    volumes: list[dict] = []
    for v in (data or {}).get("volumes", []) or []:
        if not isinstance(v, dict):
            continue
        chapters: list[dict] = []
        for c in v.get("chapters", []) or []:
            if not isinstance(c, dict):
                continue
            title = str(c.get("title") or "").strip()
            if not title:
                continue
            chapters.append({
                "title": title,
                "role": str(c.get("role") or "").strip(),
                "purpose": str(c.get("purpose") or "").strip(),
                "key_events": str(c.get("key_events") or "").strip(),
                "characters": _as_list(c.get("characters")),
            })
        if chapters:
            volumes.append({
                "title": str(v.get("title") or "").strip() or "未命名卷",
                "summary": str(v.get("summary") or "").strip(),
                "chapters": chapters,
            })
    return {"volumes": volumes}


def count_chapters(outline: dict) -> int:
    return sum(len(v.get("chapters", []))
               for v in (outline or {}).get("volumes", []))


async def build_outline(project: str, model: str, *,
                        guidance: str = "") -> dict:
    """调用模型生成结构大纲（已规范化）；失败返回 {"volumes": []}。"""
    from core.design.context import build_snapshot
    snapshot = await build_snapshot(project)
    proj = await db.get_project() or {}
    system = prompt_builder.load_template("design_outline.md", project) \
        or _FALLBACK_SYSTEM
    total = proj.get("total_chapters") or 100
    wpc = proj.get("words_per_chapter") or 3000
    user = (
        f"【作品当前设定】\n{snapshot}\n\n"
        f"【目标篇幅】全书约 {total} 章，每章约 {wpc} 字。\n"
        f"【作者补充要求】\n{guidance or '（无）'}\n\n"
        f"请输出分卷与每一章的细纲：结构角色、核心目的、关键事件、出场角色。"
        f"章节数量以目标篇幅为准，可先用合理规模（如首卷 8~15 章）示意。"
    )
    data = await ai_service.call_llm_json(
        model,
        [{"role": "system", "content": system},
         {"role": "user", "content": user}],
        purpose="design_outline", temperature=0.6,
        json_schema=OUTLINE_SCHEMA, default={})
    return parse_outline(data)
