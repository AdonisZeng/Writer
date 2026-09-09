"""定稿抽取 Canon 增量（方案 5.4.4，extractor 层）。

Qwen3.8-27B 结构化指令遵循能力足够强，合并为 2 次结构化调用（原 7B/14B
方案为每类数据一次独立微调用，小模型可退化至此）。全部走 JSON Schema
约束输出（方案 5.2.4），不支持 schema 的接入方回落 parse_llm_json。

**实体对齐白名单（防僵尸实体，硬前置）**：已知实体字典注入 Prompt；
仅允许对白名单内角色提取状态 delta；确信的新角色必须显式打
`is_new_character: true`（入库时记录 created_at_chapter_id，供回滚清理）。
"""
import json
from typing import Callable, Optional

PIPELINE_A_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string",
                    "description": "本章剧情要点，150~300 字，"
                                   "涵盖因果链与人物动向"},
        "timeline": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "seq": {"type": "integer", "description": "章内顺序，从 1 递增"},
                    "location": {"type": "string"},
                    "summary": {"type": "string", "description": "事件一句话"},
                    "impact": {"type": "string", "description": "对主线/人物的影响"},
                },
                "required": ["seq", "location", "summary", "impact"],
            },
        },
        "plot_lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "剧情线名称"},
                    "status": {"type": "string",
                               "enum": ["active", "resolved"]},
                    "description": {"type": "string"},
                },
                "required": ["name", "status", "description"],
            },
        },
    },
    "required": ["summary", "timeline", "plot_lines"],
}

PIPELINE_B_SCHEMA = {
    "type": "object",
    "properties": {
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string",
                             "description": "必须是白名单内角色，"
                                            "或显式标记的新角色"},
                    "is_new_character": {"type": "boolean",
                                         "description": "仅白名单外且确信的"
                                                        "新角色才置 true"},
                    "location": {"type": "string"},
                    "level": {"type": "string", "description": "修为/职级"},
                    "physical": {"type": "string", "description": "身体状态"},
                    "mental": {"type": "string", "description": "心理状态"},
                    "state": {"type": "string",
                              "description": "存活/受伤/死亡等"},
                    "items": {"type": "string", "description": "本章结束时关键道具"},
                    "knowledge_add": {"type": "array", "items": {"type": "string"},
                                      "description": "本章新获知的信息"},
                },
                "required": ["name", "is_new_character", "location", "level",
                             "state", "items", "knowledge_add"],
            },
        },
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "statement": {"type": "string",
                                  "description": "自此章起成立的客观事实"},
                },
                "required": ["category", "statement"],
            },
        },
    },
    "required": ["characters", "facts"],
}


def _whitelist_text(whitelist: list[str]) -> str:
    if whitelist:
        return "、".join(whitelist)
    return "（当前项目暂无已知角色——除非确信，请勿登记新角色）"


async def build_pipeline_a_messages(project: str, chapter_number: int,
                                    title: str, content: str) -> list[dict]:
    from core import prompt_builder as pb_module
    system = await pb_module.build_system_text(project)
    user = (
        f"【本轮任务类型：元数据抽取（管线 A · 宏观推进）】\n"
        f"从下方第 {chapter_number} 章《{title}》正文中抽取：\n"
        f"1. summary：本章剧情要点（150~300 字，因果链完整）\n"
        f"2. timeline：按发生顺序抽取关键事件（seq 从 1 递增；"
        f"闪回/回忆内容不进时间线）\n"
        f"3. plot_lines：本章推进或收束的剧情线（伏笔）"
        f"（推进→active，正式回收→resolved）\n"
        f"严格忠实原文，禁止脑补。\n\n【第 {chapter_number} 章正文】\n{content}"
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


async def build_pipeline_b_messages(project: str, chapter_number: int,
                                    title: str, content: str,
                                    whitelist: list[str]) -> list[dict]:
    from core import prompt_builder as pb_module
    system = await pb_module.build_system_text(project)
    user = (
        f"【本轮任务类型：元数据抽取（管线 B · 微观状态）】\n"
        f"当前项目已知实体白名单：{_whitelist_text(whitelist)}\n\n"
        f"硬约束：仅允许对白名单内角色提取状态 delta；若发现确信的新角色，"
        f"必须显式打 is_new_character: true 标记；"
        f"严禁凭正文称谓（如「白衣剑客」「三叔」）自由新建实体。\n\n"
        f"从下方第 {chapter_number} 章《{title}》正文中抽取：\n"
        f"1. characters：每个出场角色在本章结束时的状态"
        f"（位置/修为/身体/心理/存活/道具/新获知信息）\n"
        f"2. facts：本章确立的、自此章起成立的客观事实"
        f"（世界观规则、势力变动、重要约定等）\n"
        f"严格忠实原文，状态只记录本章造成的变化。\n\n"
        f"【第 {chapter_number} 章正文】\n{content}"
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


async def run_pipeline_a(project: str, chapter_number: int, title: str,
                         content: str, model: str = "",
                         call_fn: Optional[Callable] = None) -> dict:
    """管线 A：摘要 + 时间线 + 伏笔推进/收束（extract 任务，xhigh 思考）。"""
    messages = await build_pipeline_a_messages(project, chapter_number,
                                               title, content)
    if call_fn is None:
        from core import ai_service
        call_fn = ai_service.call_llm_json
    result = await call_fn(model, messages, purpose="extract",
                           json_schema=PIPELINE_A_SCHEMA, temperature=0.1,
                           default={"summary": "", "timeline": [],
                                    "plot_lines": []})
    return result


async def run_pipeline_b(project: str, chapter_number: int, title: str,
                         content: str, whitelist: list[str], model: str = "",
                         call_fn: Optional[Callable] = None) -> dict:
    """管线 B：角色状态 delta + 新客观事实（extract 任务，xhigh 思考）。"""
    messages = await build_pipeline_b_messages(project, chapter_number,
                                               title, content, whitelist)
    if call_fn is None:
        from core import ai_service
        call_fn = ai_service.call_llm_json
    result = await call_fn(model, messages, purpose="extract",
                           json_schema=PIPELINE_B_SCHEMA, temperature=0.1,
                           default={"characters": [], "facts": []})
    return result


# ==================== 回退拆分路径（小模型降级，方案 5.4.4）====================

async def run_pipeline_a_decomposed(project: str, chapter_number: int,
                                    title: str, content: str,
                                    model: str = "",
                                    call_fn: Optional[Callable] = None
                                    ) -> dict:
    """7B/14B 以下小模型的降级路径：每类数据一次独立微调用，
    防深层嵌套 JSON 截断/键名漂移。输出结构与 run_pipeline_a 一致。"""
    from core import prompt_builder as pb_module
    if call_fn is None:
        from core import ai_service
        call_fn = ai_service.call_llm_json
    system = await pb_module.build_system_text(project)

    async def micro(instruction: str, schema: dict, default):
        msgs = [{"role": "system", "content": system},
                {"role": "user", "content":
                    f"【本轮任务类型：元数据抽取】\n{instruction}\n\n"
                    f"【第 {chapter_number} 章正文】\n{content}"}]
        return await call_fn(model, msgs, purpose="extract",
                             json_schema=schema, temperature=0.1,
                             default=default)

    summary = await micro(
        "抽取本章剧情要点（summary 字段，150~300 字）",
        {"type": "object",
         "properties": {"summary": {"type": "string"}},
         "required": ["summary"]}, {"summary": ""})
    timeline = await micro(
        "按发生顺序抽取本章关键事件（闪回/回忆不算）",
        PIPELINE_A_SCHEMA["properties"]["timeline"], [])
    plot_lines = await micro(
        "抽取本章推进或收束的剧情线（伏笔）",
        PIPELINE_A_SCHEMA["properties"]["plot_lines"], [])
    return {"summary": summary.get("summary", ""),
            "timeline": timeline if isinstance(timeline, list) else [],
            "plot_lines": plot_lines if isinstance(plot_lines, list) else []}
