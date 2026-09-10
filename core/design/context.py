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
    "无需作者选择时不输出该块。"
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
                      system_text: str, focus: str = "") -> list[dict]:
    """纯函数：把 System / 历史 / 当轮诉求装配成 messages（便于单测）。"""
    msgs: list[dict] = [{"role": "system", "content": system_text}]
    for m in history:
        content = (m.get("content") or "").strip()
        role = "assistant" if m.get("role") == "assistant" else "user"
        if content:
            msgs.append({"role": role, "content": content})
    prefix = f"{focus.rstrip()}\n\n" if focus.strip() else ""
    user = (f"{prefix}【作品当前设定快照】\n{snapshot}\n\n"
            f"【作者本轮诉求】\n{author_text}")
    msgs.append({"role": "user", "content": user})
    return msgs


def _guide_focus(project: str, step: dict) -> str:
    """渲染引导步骤聚焦块（外置模板，三级可覆盖）。"""
    tpl = prompt_builder.load_template("design_guide.md", project)
    if not tpl:
        tpl = ("【本轮引导步骤】{{step_label}}\n【本步目标】{{step_goal}}\n"
               "请紧扣本步目标循序渐进地推进，给出具体、可落地的方案，"
               "并明确标注哪些片段适合直接采纳进设定库。")
    return prompt_builder.PromptBuilder(tpl).render(
        step_label=step.get("label", ""), step_goal=step.get("goal", ""))


async def build_chat_messages(project: str, author_text: str, *,
                              mode: str = "free",
                              step: str = "") -> list[dict]:
    """装配一轮设计协作消息，并持久化本轮作者消息（返回可直接送模型的 messages）。"""
    from core.design import guide
    system_text = prompt_builder.load_template("design_system.md", project) \
        or _FALLBACK_SYSTEM
    # 追加机器可解析的互动协议（外置模板，缺省用内置兜底）
    protocol = prompt_builder.load_template("design_protocol.md", project) \
        or _FALLBACK_PROTOCOL
    system_text = f"{system_text.strip()}\n\n{protocol.strip()}"
    snapshot = await build_snapshot(project)
    history = await db.list_design_messages()
    budget = int(prompt_builder.resolve_token_budget(config.CONFIG)
                 * _HISTORY_BUDGET_RATIO)
    history = trim_history(history, budget)
    focus = ""
    if mode == "guide":
        st = guide.step_by_key(step)
        if st:
            focus = _guide_focus(project, st)
    messages = assemble_messages(history, author_text, snapshot,
                                 system_text=system_text, focus=focus)
    await db.insert_design_message("user", author_text, mode=mode,
                                   step=step or "")
    return messages
