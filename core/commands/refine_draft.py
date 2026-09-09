"""精修命令（P2，方案 7-⑤ / 6.1-1 Ctrl+K）：

- 选区局部精修：Ctrl+K 呼出指令胶囊（「精简对话」「更肃杀一点」…）
- 整章全局精修
- temperature 0.5 稳定不乱改（方案 5.2.4）；产出必须经 Diff 对比卡逐块采纳，
  绝不直接覆盖（UI 负面清单 3）
"""
import json

from core import ai_service, config, db


async def build_refine_messages(project: str, target_text: str,
                                instruction: str, *,
                                context_before: str = "",
                                context_after: str = "",
                                full_context: bool = False) -> list[dict]:
    """精修消息：System 与其他任务字节级一致，任务差异后置 User。"""
    from core import prompt_builder as pb
    system = await pb.build_system_text(project)
    if full_context:
        user = (
            f"【本轮任务类型：整章精修】\n"
            f"作者指令：{instruction or '（无——做通用润色，保持原意与节奏）'}\n\n"
            f"【要求】\n"
            f"1. 输出精修后的整章正文，保持原有分段结构\n"
            f"2. 严格保留所有情节事实、人物状态与既定设定，只做文风层面优化\n"
            f"3. 直接输出正文，不要任何解释\n\n"
            f"【原文】\n{target_text}"
        )
    else:
        user = (
            f"【本轮任务类型：选区精修】\n"
            f"作者指令：{instruction or '（无——做通用润色，保持原意与节奏）'}\n\n"
            f"【要求】\n"
            f"1. 只输出改写后的选区文本本身（长度与原文相当），"
            f"不要输出前后文、解释或引号\n"
            f"2. 严格保留情节事实与人物状态，只做文风层面优化\n"
            f"3. 与前后文自然衔接\n\n"
            f"【选区前文（仅供衔接参考，不要输出）】\n{context_before[-400:]}\n\n"
            f"【选区后文（仅供衔接参考，不要输出）】\n{context_after[:400]}\n\n"
            f"【选区原文】\n{target_text}"
        )
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


async def refine(project: str, target_text: str, instruction: str,
                 model: str = "", *, full_context: bool = False,
                 context_before: str = "", context_after: str = "",
                 cancel_event=None) -> str:
    """执行精修，返回改写后的文本（供 Diff 对比卡，不直接落盘）。"""
    messages = await build_refine_messages(
        project, target_text, instruction, full_context=full_context,
        context_before=context_before, context_after=context_after)
    return await ai_service.call_llm_text(
        config.get("model", ""), messages, purpose="refine",
        temperature=0.5, cancel_event=cancel_event)
