"""设计协作的多轮消息装配。

结构：System（外置模板，跨轮字节级一致以命中缓存）→ 持久化历史（按 token 预算
从最旧裁剪）→ 最新 user 消息（内嵌「当轮设定快照」+ 作者诉求 + 引导步骤）。

这样既保留多轮记忆，又保证每轮都能看到最新设定（快照不落历史，避免陈旧）。
"""
from __future__ import annotations

from core import config, db, prompt_builder

# 历史占用上下文的比例（其余留给当轮快照与模型输出）
_HISTORY_BUDGET_RATIO = 0.4

# 互动协议兜底（与 prompts/design_protocol.md 一致）：需要作者决策时输出 choice 块
_FALLBACK_PROTOCOL = (
    "【互动协议】需要作者做决定时，在回复末尾追加一个独立的 choice 代码块，"
    "内容为 JSON：question 与 options 数组，每个 option 含 id/title/detail；"
    "options 至少 2 个；正文照常给建议，只把需要拍板的部分结构化；"
    "无需作者选择时不输出该块。\n"
    "【本步收尾】分步引导中，若聚焦块给出收尾约定：本步设计完成时输出带 "
    "step_done（当前步骤 key）的 choice 块，选项带 action（adopt=直接采纳并"
    "进入下一步 / discuss=还要调整），收尾轮正文包含本步完整最终方案；"
    "世界观步再给出 sections 数组（每项 label + 该分节完整内容）。"
)

# 模板缺失时的兜底 System（与 prompts/design_system.md 保持一致口径）
_FALLBACK_SYSTEM = (
    "你是一位资深的小说策划与设定顾问，与作者协作设计作品的整体框架"
    "（故事前提、类型定位、世界观、人物弧光、主线大纲）。\n"
    "工作方式：\n"
    "1. 先理解作者已有设定与本轮诉求，给出具体、可落地、有创造力且自洽的建议；\n"
    "2. 输出结构清晰（可用小标题与要点），聚焦作者当前正在设计的板块，"
    "信息密度高、篇幅克制；\n"
    "3. 绝不与已有设定冲突；如需引入新假设，明确标注「（待定）」供作者取舍；\n"
    "4. 只做设计层的策划文本，不写章节正文，不复述作者原话，不空洞客套。\n"
    "作者会在你的产出中挑选片段「采纳」进设定库，因此请让每段建议都能独立成立。"
)


async def build_snapshot(project: str) -> str:
    """当前作品设定快照（内核 + 篇幅 + 世界观 + 主要角色），供当轮 prompt 使用。"""
    from core.design import world
    proj = await db.get_project() or {}
    chars = await db.list_characters()
    char_lines = "\n".join(
        f"- {c['name']}（{c.get('role') or '配角'}）："
        f"{(c.get('personality') or c.get('background') or '（暂无描述）')[:80]}"
        for c in chars[:30]) or "（暂无角色）"
    sections = await world.load_sections(project)
    world_text = world.render_worldbuilding(sections) or "（未定）"
    return (
        f"【类型 / 题材】{proj.get('genre') or '（未定）'}\n"
        f"【一句话前提】{proj.get('premise') or '（未定）'}\n"
        f"【故事梗概】\n{(proj.get('synopsis') or '（未定）')[:1200]}\n"
        f"【文风】{proj.get('writing_style') or '（未定）'}\n"
        f"【全局指导 / 禁忌】{proj.get('global_guidance') or '（未定）'}\n"
        f"【预计篇幅】共 {proj.get('total_chapters') or 100} 章，"
        f"每章约 {proj.get('words_per_chapter') or 3000} 字\n"
        f"【世界观设定】\n{world_text[:2000]}\n"
        f"【主要角色】\n{char_lines}"
    )


def trim_history(history: list[dict], budget_tokens: int) -> list[dict]:
    """按 token 预算保留最近的历史消息（从最旧整条丢弃，绝不拦腰截断）。"""
    picked: list[dict] = []
    total = 0
    for m in reversed(history):
        cost = prompt_builder.estimate_tokens(m.get("content") or "")
        if picked and total + cost > budget_tokens:
            break
        picked.append(m)
        total += cost
    return list(reversed(picked))


def assemble_messages(history: list[dict], author_text: str, snapshot: str, *,
                      system_text: str, focus: str = "",
                      tail: str = "") -> list[dict]:
    """纯函数：把 System / 历史 / 当轮诉求装配成 messages（便于单测）。

    tail：追加到最后一条 user 消息末尾的检查尾注（尾部锚定，见
    finish_tail_note）——收尾格式要求离生成点越近越不容易被遗忘。
    """
    msgs: list[dict] = [{"role": "system", "content": system_text}]
    for m in history:
        content = (m.get("content") or "").strip()
        role = "assistant" if m.get("role") == "assistant" else "user"
        if content:
            msgs.append({"role": role, "content": content})
    prefix = f"{focus.rstrip()}\n\n" if focus.strip() else ""
    user = (f"{prefix}【作品当前设定快照】\n{snapshot}\n\n"
            f"【作者本轮诉求】\n{author_text}")
    if tail.strip():
        user = f"{user}\n\n{tail.strip()}"
    msgs.append({"role": "user", "content": user})
    return msgs


# 收尾约定：仅注入已支持「直接采纳」收尾的步骤（structure / cast 仍走手动标记）。
# 内置在代码里（不依赖外置模板升级）——用户级旧版 design_guide.md 也能生效。
_FINISH_STEPS = ("core", "world")

_FINISH_RULES = (
    "\n\n【本步收尾约定（必须遵守）】当前步骤 key 为「{key}」。当本步设计已基本"
    "完成、适合请作者确认收尾时，按【互动协议】在回复末尾输出包含 "
    "\"step_done\": \"{key}\" 的 choice 块：step_done 必须原样复制 key，"
    "不要翻译或自创名称；选项至少包含 action=\"adopt\"（直接采纳并进入下一步）"
    "与 action=\"discuss\"（还要调整）两类，action 必须是选项对象里的 JSON "
    "字段，不要写进 title 或 detail 的正文文字。收尾轮的正文必须包含本步的"
    "完整最终方案（它将作为采纳来源）。"
)

_WORLD_SECTIONS_RULES = (
    "本步为「世界观」：choice 块中再给出 \"sections\" 数组，每项为 "
    "{\"label\": 分节名, \"content\": 该分节的完整内容}；分节请按作品题材"
    "自由规划（建议 3~6 个，名称可与现有默认分节不同），content 必须是该"
    "分节的完整内容，采纳时将整体覆盖写入对应分节。"
)


def _finish_directive(step_key: str) -> str:
    """当前步骤支持收尾采纳时，返回内置收尾约定（含该步 key）。"""
    if step_key not in _FINISH_STEPS:
        return ""
    text = _FINISH_RULES.replace("{key}", step_key)
    if step_key == "world":
        text += _WORLD_SECTIONS_RULES
    return text


def finish_tail_note(step_key: str) -> str:
    """收尾检查尾注：追加到最后一条 user 消息末尾（尾部锚定）。

    收尾约定原本只出现在 System 与聚焦块（消息头部），隔着大段设定快照与
    作者诉求，长对话时模型容易遗忘；按 recency 原则在生成点前再钉一次，
    要点与打回反馈（retry_feedback）同源。仅收尾支持步骤返回非空。
    """
    if step_key not in _FINISH_STEPS:
        return ""
    text = (f"【收尾自查】若本轮请作者确认收尾：choice 块必须含 "
            f"\"step_done\": \"{step_key}\"（原样复制，不要自创名称）；"
            "action 必须是选项对象的 JSON 字段（adopt=直接采纳 / "
            "discuss=还要调整），不要写进 title/detail 文字。")
    if step_key == "world":
        text += ("且必须给出 sections 数组（每项 {\"label\": 分节名, "
                 "\"content\": 该分节的完整内容}）。")
    return text


# 挂载收尾工具时向 System 追加的说明（仅 finish_tool=True 的会话可见）
# 注意：必须保留文本协议作为退路——部分后端会**静默忽略** tools（不报错），
# 若此时又禁止输出 choice 块，收尾流程会彻底失效且无法打回。
_TOOL_FINISH_HINT = (
    "\n\n【收尾工具】本会话已挂载收尾工具 submit_step_finish：仅当本步设计"
    "完成、需要作者确认收尾时，**优先**调用它提交（step_done / options，"
    "世界观步另给 sections），此时正文照常输出本步完整最终方案、不必再输出 "
    "choice 代码块；若你无法调用该工具（环境不支持），则仍按互动协议输出 "
    "choice 代码块。普通选择（非收尾）一律按互动协议输出 choice 块。")


def _guide_focus(project: str, step: dict) -> str:
    """渲染引导步骤聚焦块（外置模板，三级可覆盖）+ 内置收尾约定。"""
    tpl = prompt_builder.load_template("design_guide.md", project)
    if not tpl:
        tpl = ("【本轮引导步骤】{{step_label}}\n【本步目标】{{step_goal}}\n"
               "请紧扣本步目标循序渐进地推进，给出具体、可落地的方案，"
               "并明确标注哪些片段适合直接采纳进设定库。")
    text = prompt_builder.PromptBuilder(tpl).render(
        step_label=step.get("label", ""), step_goal=step.get("goal", ""),
        step_key=step.get("key", ""))
    return text + _finish_directive(str(step.get("key") or ""))


async def build_chat_messages(project: str, author_text: str, *,
                              mode: str = "free", step: str = "",
                              finish_tool: bool = False) -> list[dict]:
    """装配一轮设计协作消息，并持久化本轮作者消息（返回可直接送模型的 messages）。

    finish_tool：本会话挂载了收尾工具（Function Calling 快路径）时为 True，
    向 System 追加工具使用说明。
    """
    from core.design import guide
    system_text = prompt_builder.load_template("design_system.md", project) \
        or _FALLBACK_SYSTEM
    # 追加机器可解析的互动协议（外置模板，缺省用内置兜底）
    protocol = prompt_builder.load_template("design_protocol.md", project) \
        or _FALLBACK_PROTOCOL
    system_text = f"{system_text.strip()}\n\n{protocol.strip()}"
    if finish_tool:
        system_text += _TOOL_FINISH_HINT
    snapshot = await build_snapshot(project)
    history = await db.list_design_messages()
    budget = int(prompt_builder.resolve_token_budget(config.CONFIG)
                 * _HISTORY_BUDGET_RATIO)
    history = trim_history(history, budget)
    focus = ""
    tail = ""
    if mode == "guide":
        st = guide.step_by_key(step)
        if st:
            focus = _guide_focus(project, st)
            # 作者参与程度：按 config 档位注入提问频率指令（默认高频=原行为）
            focus = (f"{focus}\n\n"
                     + guide.participation_directive(
                         str(config.get("design_participation", "high"))))
            tail = finish_tail_note(step)
    messages = assemble_messages(history, author_text, snapshot,
                                 system_text=system_text, focus=focus,
                                 tail=tail)
    await db.insert_design_message("user", author_text, mode=mode,
                                   step=step or "")
    return messages
