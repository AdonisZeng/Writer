"""按优先级渲染 Canon 上下文（方案 5.4.1，context 层）。

渲染为一段文本，强制插在 Prompt 最高优先级位置（{{canon_block}}）。
5.4.1 十三级优先级中，文风/世界观/全局指导属 Tier1（system.md 承担），
本章写作目标属细纲块——此处只渲染 Canon 独有的中段信息。
"""
from typing import Optional


def _trim(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "…"


def render_char_states(characters: dict, char_states: list[dict]) -> str:
    """当前人物状态（最高优先级 · 生成时不得推翻）。"""
    if not char_states:
        return "（暂无结构化角色状态，参考世界观设定）"
    lines = []
    for s in char_states:
        name = s["character"]
        info = characters.get(name, {})
        parts = [f"{name}"]
        role_tag = info.get("role", "")
        if role_tag:
            parts.append(f"（{role_tag}）")
        detail = []
        if s.get("location"):
            detail.append(f"位置：{s['location']}")
        if s.get("level"):
            detail.append(f"修为/职级：{s['level']}")
        if s.get("state"):
            detail.append(f"状态：{s['state']}")
        if s.get("items"):
            detail.append(f"关键道具：{s['items']}")
        lines.append(" ".join(parts) + "：" + "；".join(detail)
                     if detail else " ".join(parts) + "：状态未知")
    return "\n".join(lines)


def render_timeline(timeline: list[dict], number_by_id: dict) -> str:
    """已发生事件时间线（严格单向）。"""
    if not timeline:
        return "（暂无时间线事件）"
    lines = []
    for ev in reversed(timeline):  # 注入时按时间正序展示
        n = number_by_id.get(ev["chapter_id"], "?")
        loc = f"【{ev['location']}】" if ev.get("location") else ""
        lines.append(f"第{n}章 {loc}{_trim(ev['summary'], 80)}")
    return "\n".join(lines)


def render_summaries(summaries: list[dict]) -> str:
    if not summaries:
        return "（暂无章节摘要）"
    lines = [f"第{s['number']}章 {s['title']}：{_trim(s['summary'], 150)}"
             for s in reversed(summaries)]
    return "\n".join(lines)


def render_plot_lines(plot_lines: list[dict]) -> str:
    """未结剧情线（伏笔提醒）。"""
    if not plot_lines:
        return "（暂无未结剧情线）"
    lines = []
    for p in plot_lines:
        desc = f"：{_trim(p['description'], 60)}" if p.get("description") else ""
        lines.append(f"- {p['name']}{desc}")
    return "\n".join(lines)


def render_facts(facts: list[dict]) -> str:
    if not facts:
        return "（暂无关键事实）"
    return "\n".join(f"- 【{f['category']}】{_trim(f['statement'], 80)}"
                     for f in facts)


def render_archives(archives: list[dict]) -> str:
    """长期记忆压缩归档（P3 compression 产物）。"""
    if not archives:
        return ""
    lines = []
    for a in reversed(archives):
        label = a["chapter_id"].replace("archive_", "归档至第 ")
        lines.append(f"[{label} 章前] {_trim(a['summary'], 200)}")
    return "\n".join(lines)


def render_canon_context(bundle: dict, number_by_id: dict) -> str:
    """Canon 块全文（5.4.1 中段优先级顺序）。"""
    sections = [
        ("当前人物状态（最高优先级 · 生成时不得推翻）",
         render_char_states(bundle["characters"], bundle["char_states"])),
        ("已发生事件时间线（严格单向）",
         render_timeline(bundle["timeline"], number_by_id)),
        ("最近章节摘要", render_summaries(bundle["summaries"])),
        ("未结剧情线（伏笔提醒）", render_plot_lines(bundle["plot_lines"])),
        ("关键事实条目", render_facts(bundle["facts"])),
    ]
    if bundle.get("archives"):
        sections.append(("长期记忆（压缩归档）",
                         render_archives(bundle["archives"])))
    parts = [f"【{title}】\n{body}" for title, body in sections if body]
    return "\n\n".join(parts)


def render_pov_block(pov: Optional[dict]) -> str:
    """POV 信息差硬约束（方案 5.4.6）；pov 为空返回空串（默认主角视角）。"""
    if not pov:
        return ""
    aliases = f"（又称：{'、'.join(pov['aliases'])}）" if pov["aliases"] else ""
    knowledge = "\n".join(f"- {k}" for k in pov["knowledge"]) \
        if pov["knowledge"] else "- （暂无已知秘密记录）"
    return (
        f"【视角约束（POV）：{pov['name']}{aliases}】\n"
        f"本章叙述严格限定在「{pov['name']}」的视线与认知内：\n"
        f"- 只能使用该角色在场时所见的情报，禁止全知视角\n"
        f"- 以下为该角色已知信息边界，未列出的秘密不得直接写出：\n{knowledge}"
    )
