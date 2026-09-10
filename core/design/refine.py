"""采纳提炼：把 AI 的一段讨论提炼成适合某个目标字段的最终文本。

用于协作台「提炼后采纳」——避免把整条回复原文一股脑塞进「主题立意 /
故事梗概」等字段。非流式小调用（purpose=design_extract）；失败由调用方
降级为「原样采纳」，绝不阻断作者操作。
"""
from __future__ import annotations

from core import ai_service

# 目标字段 → 提炼结果的写作要求（帮模型聚焦）
_TARGET_HINTS: dict[str, str] = {
    "premise": "一句话前提：用一句话讲清「主角 + 目标 + 最大阻碍」，不超过 60 字。",
    "theme": "主题立意：作品想探讨的核心命题，1~3 句，抽象而具体。",
    "synopsis": "故事梗概：主线走向、关键转折与结局设想，300~600 字，连贯成段。",
    "genre": "类型 / 题材：简短标签式描述（如「东方玄幻 + 悬疑」）。",
    "world": "世界观设定：条理化的事实性设定（可保留小标题与要点），去除讨论口吻。",
}

_LABELS: dict[str, str] = {
    "premise": "一句话前提", "theme": "主题立意", "synopsis": "故事梗概",
    "genre": "类型 / 题材", "world": "世界观设定",
}

_SYSTEM = (
    "你是资深小说编辑。作者与 AI 就作品设定进行了讨论，请从中提炼出适合填入"
    "【{label}】的内容。要求：\n"
    "1. 只输出该字段最终应写入的文本，不要解释、不要客套、不要代码围栏；\n"
    "2. 忠实于讨论中已确立的设定，不要新增未经讨论的关键事实；\n"
    "3. 若讨论未涉及该字段，则与现有内容保持一致，仅做最小必要补充；\n"
    "4. {hint}\n"
    "直接输出正文。"
)


def target_label(target: str) -> str:
    return _LABELS.get(target, target)


async def extract_for_target(project: str, target: str, raw_text: str, *,
                             current: str = "", model: str = "",
                             call_fn=None) -> str:
    """提炼讨论文本为指定字段内容；失败抛异常（调用方降级为原样）。

    测试可注入 call_fn(model, messages) -> str 以避免真实网络。
    """
    from core import config
    used_model = model or config.get("model", "")
    label = target_label(target)
    hint = _TARGET_HINTS.get(target, "聚焦该字段本身，去除与字段无关的内容。")
    messages = [
        {"role": "system",
         "content": _SYSTEM.format(label=label, hint=hint)},
        {"role": "user",
         "content": (f"【目标字段】{label}\n"
                     f"【该字段当前内容（可能为空）】\n"
                     f"{current.strip() or '（空）'}\n\n"
                     f"【作者与 AI 的讨论原文】\n{raw_text.strip()}\n\n"
                     f"请输出【{label}】的最终文本。")},
    ]
    if call_fn is not None:
        return (await call_fn(used_model, messages) or "").strip()
    text = await ai_service.call_llm_text(
        used_model, messages, purpose="design_extract", temperature=0.3)
    return (text or "").strip()
