"""异步语义审查（方案 5.4.2 第二层，reviewer 层）。

路由至 review 任务（reasoning_effort=medium）让模型逐行对照 POV 情报与
时间线，排查：知识越权 / 地点瞬移 / 时间线倒退 / 关系矛盾 / 道具归属 /
死亡角色重现（区分闪回/幻觉/复活）。
产出结构化 JSON 诊断（quote + paragraph 定位），侧边栏逐条采纳/忽略，
任何情况下严禁机械修改正文。支持注入 fake call_fn 做离线单测。
"""
from typing import Callable, Optional

from core.canon.validator import Issue, split_paragraphs

DIAGNOSTIC_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["knowledge_violation", "location_jump",
                                 "timeline_regression", "relation_conflict",
                                 "item_mismatch", "dead_character_reappear",
                                 "other"],
                    },
                    "quote": {"type": "string",
                              "description": "正文中的原文字面片段，用于定位"},
                    "paragraph": {"type": "integer",
                                  "description": "出现段落序号，从 1 开始"},
                    "message": {"type": "string",
                                "description": "诊断说明，引用具体设定依据"},
                },
                "required": ["type", "quote", "paragraph", "message"],
            },
        }
    },
    "required": ["issues"],
}

_TYPE_LABEL = {
    "knowledge_violation": "知识越权",
    "location_jump": "地点瞬移",
    "timeline_regression": "时间线倒退",
    "relation_conflict": "关系矛盾",
    "item_mismatch": "道具归属",
    "dead_character_reappear": "死亡角色重现",
    "other": "其他矛盾",
}

_REVIEW_INSTRUCTION = """【本轮任务类型：一致性审查】
对照上方正史与状态资料，逐行审查下方正文，输出诊断 issues（无问题输出空数组）。
检查项：
1. 知识越权：角色使用了其已知信息边界之外的秘密（POV 视角约束优先）
2. 地点瞬移：场景切换无交代
3. 时间线倒退/顺序矛盾（注意区分正常的闪回/回忆转场，不算违规）
4. 关系矛盾：人物关系与既定设定冲突
5. 道具归属：物品在错误的持有者手中
6. 死亡角色重现（区分明确的闪回/幻觉，不算违规）
注意：这是文学文本，隐喻与修辞不是违规；证据不足不下结论。
"""


async def build_review_messages(project: str, canon_context: str,
                                pov_block: str, content: str) -> list[dict]:
    """审查消息：System 与创作任务字节级一致（跨任务缓存复用纪律），
    任务差异一律后置在 User。"""
    from core import prompt_builder as pb_module
    system_text = await pb_module.build_system_text(project)
    paragraphs = split_paragraphs(content)
    numbered = "\n".join(f"[段{i}] {p}" for i, p in enumerate(paragraphs, 1))
    user = (f"{canon_context}\n\n{pov_block}\n\n"
            f"{_REVIEW_INSTRUCTION}\n【待审查正文（按段落编号）】\n{numbered}")
    return [{"role": "system", "content": system_text},
            {"role": "user", "content": user}]


async def semantic_review(project: str, chapter_id: str, content: str,
                          canon_context: str, pov_block: str,
                          model: str = "",
                          call_fn: Optional[Callable] = None) -> list[Issue]:
    """执行语义审查，返回诊断列表（全部为 warning 级，绝不机械改稿）。"""
    messages = await build_review_messages(project, canon_context, pov_block,
                                           content)
    if call_fn is None:
        from core import ai_service
        call_fn = ai_service.call_llm_json
    result = await call_fn(model, messages, purpose="review",
                           json_schema=DIAGNOSTIC_SCHEMA, temperature=0.3,
                           default={"issues": []})
    issues: list[Issue] = []
    paragraphs = split_paragraphs(content)
    for item in result.get("issues") or []:
        quote = (item.get("quote") or "").strip()[:60]
        para = item.get("paragraph", 0)
        if not isinstance(para, int) or para < 0 or para > len(paragraphs):
            para = 0
        issues.append(Issue(
            severity="warning",
            code=_TYPE_LABEL.get(item.get("type", ""), "其他矛盾"),
            message=(item.get("message") or "").strip(),
            quote=quote, paragraph=para, chapter_id=chapter_id))
    return issues
