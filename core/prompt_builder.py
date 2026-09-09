"""Prompt 模板渲染 + 缓存友好排序 + Token 预算（方案 5.3）。

三级覆盖机制：内置 prompts/ → 用户级 ~/.writer/prompts/ → 项目级 .writer/prompts/，
同名即覆盖，删除即恢复默认。

上下文装配纪律：
- 所有任务共用同一段 System 前缀（字节级一致），任务差异一律后置到 User 消息；
- 按「稳定前缀 → 可变后缀」三层前缀金字塔排序，最大化 KV Cache 命中率；
- 历史要点截断必须以章节块为单位逆序装填，绝不拦腰截断。
"""
import asyncio
import json
import os
import re
import shutil

from core import db, file_manager, paths


# ==================== 模板加载（三级覆盖） ====================

def release_builtin_prompts() -> None:
    """首次运行把内置模板复制到用户级目录，便于用户修改覆盖。"""
    os.makedirs(paths.USER_PROMPTS_DIR, exist_ok=True)
    if not os.path.isdir(paths.BUILTIN_PROMPTS_DIR):
        return
    for fn in os.listdir(paths.BUILTIN_PROMPTS_DIR):
        dst = os.path.join(paths.USER_PROMPTS_DIR, fn)
        if not os.path.exists(dst):
            shutil.copy2(os.path.join(paths.BUILTIN_PROMPTS_DIR, fn), dst)


def load_template(name: str, project: str = "") -> str:
    """按 项目级 → 用户级 → 内置 顺序解析（高优先级覆盖低优先级）。"""
    chain = []
    if project:
        chain.append(paths.project_prompts_dir(project))
    chain.append(paths.USER_PROMPTS_DIR)
    chain.append(paths.BUILTIN_PROMPTS_DIR)
    for d in chain:
        p = os.path.join(d, name)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
    return ""


class PromptBuilder:
    """{{var}} 简单占位渲染；缺失变量替换为空串，保证模板可独立预览。"""

    def __init__(self, template: str, system_role: str = ""):
        self.template = template
        self.system_role = system_role

    def render(self, **vars) -> str:
        text = self.template
        for k, v in vars.items():
            text = text.replace("{{" + k + "}}", str(v))
        # 清理未提供的占位符（保持空行整洁）
        text = re.sub(r"\{\{[a-z_0-9]+\}\}", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


# ==================== Token 预算 ====================

def estimate_tokens(text: str) -> int:
    """中文约 1.5 字符/token 的粗估。"""
    return int(len(text) / 1.5)


def resolve_token_budget(config: dict, max_output: int = 4096) -> int:
    """读 config.json 的 context_limit；不填保守取 8192。"""
    limit = config.get("context_limit") or 8192
    return max(4096, int(limit) - max_output)


# ==================== 上下文装配 ====================

def _trim_rag(text: str, n: int) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[:n] + "…"


def config_rag_enabled() -> bool:
    from core import rag as _rag
    from core import config as _cfg
    return bool(_cfg.get("rag_enabled")) and _rag.rag_ready()


async def build_system_text(project: str = "") -> str:
    """跨任务共用 System 前缀（方案 5.3 硬约束：所有任务字节级一致）。

    draft / review / extract 等全部任务都由本函数产出 System，
    任务差异一律后置到 User 消息——保证 Tier 1 缓存命中率。
    """
    from core import config as cfg
    if not project:
        project = cfg.get("project", "")
    proj = await db.get_project()
    sys_builder = PromptBuilder(load_template("system.md", project))
    return sys_builder.render(
        global_guidance=(proj or {}).get("global_guidance", ""),
        writing_style=(proj or {}).get("writing_style", ""),
        worldbuilding=file_manager.read_settings_md(project),
    )


def build_notes_timeline(chapters: list[dict], current: dict,
                         full_window: int = 25, max_chars: int = 12000) -> str:
    """近 N 章完整收录要点，更早期仅保留标题行；超预算按整章块逆序装填。

    chapters 须为按 order_index 升序的完整章节列表。
    """
    blocks = []
    cur_order = current["order_index"]
    for row in chapters:
        if row["id"] == current["id"] or row["order_index"] >= cur_order:
            continue
        head = f"第{row['number']}章 {row['title']}"
        if (cur_order - row["order_index"]) <= full_window and row["notes"]:
            blocks.append(f"{head}\n{row['notes']}")
        else:
            blocks.append(head)
    # 从最近章节往前装填；单章块加入即超预算则停止；至少保住最新一块
    picked, total = [], 0
    for block in reversed(blocks):
        if total + len(block) > max_chars and picked:
            break
        picked.append(block)
        total += len(block)
    return "\n\n".join(reversed(picked))


def _fmt_character_sheets(chars: list[dict]) -> str:
    lines = []
    for c in chars:
        lines.append(f"### {c['name']}（{c['role']}）")
        for field in ("personality", "background", "abilities"):
            if c.get(field):
                lines.append(f"- {field}：{c[field]}")
        lines.append("")
    return "\n".join(lines).strip()


def _fmt_future_outline(chapters: list[dict], current: dict, n: int = 5) -> str:
    lines = []
    count = 0
    for row in chapters:
        if row["order_index"] <= current["order_index"]:
            continue
        lines.append(f"第{row['number']}章 {row['title']}"
                     + (f"：{row['purpose']}" if row["purpose"] else ""))
        count += 1
        if count >= n:
            break
    return "\n".join(lines) if lines else "（暂无后续细纲）"


async def build_draft_messages(project: str, chapter: dict,
                               user_guidance: str = "") -> dict:
    """组装章节草稿生成的 messages（三层前缀金字塔）。

    返回 {"messages": [...], "tiers": {tier1, tier2, tier3, est_tokens},
          "budget": int, "warnings": [...]}
    """
    from core import config as cfg

    proj = await db.get_project()
    chapters = await db.list_chapters()

    # ---- Tier 1：全书静态（跨章节 100% 缓存命中；与 review/extract 共用）----
    system_text = await build_system_text(project)

    # ---- Tier 2：卷级半静态 ----
    chars = await db.list_characters()
    character_sheets = _fmt_character_sheets(chars)

    # ---- Tier 3：章级动态 ----
    timeline = build_notes_timeline(chapters, chapter)
    prev_text = ""
    prev_ch = next((c for c in reversed(chapters)
                    if c["order_index"] < chapter["order_index"]), None)
    if prev_ch:
        prev_path = file_manager.find_chapter_file(
            project, prev_ch["number"], prev_ch["title"])
        prev_text = await asyncio.to_thread(file_manager.read_text,
                                            prev_path)
        prev_text = prev_text[-2000:]

    user_tpl = load_template("next_chapter_draft.md", project)
    user_builder = PromptBuilder(user_tpl)
    try:
        characters = ", ".join(json.loads(chapter.get("characters") or "[]"))
    except json.JSONDecodeError:
        characters = ""

    # Canon 上下文（5.4.1 优先级渲染，强制注入）+ POV 信息差（5.4.6）
    from core.canon import build_canon_context, build_pov_block
    canon_block = await build_canon_context(chapter["id"])
    if canon_block:
        canon_block = f"【正史设定与硬约束（不可违背）】\n{canon_block}"
    pov_block = await build_pov_block(chapter["id"])

    # RAG 知识库检索（P2）：按本章细纲+作者指导查询，top-k 片段注入
    rag_block = ""
    if config_rag_enabled():
        try:
            from core import rag
            query = f"{chapter['title']} {chapter.get('purpose', '')} " \
                    f"{chapter.get('key_events', '')} {user_guidance or ''}"
            hits = await rag.retrieve(query)
            if hits:
                items = "\n".join(f"- {_trim_rag(h['content'], 150)}"
                                  for h in hits)
                rag_block = f"【知识库参考（历史片段检索）】\n{items}\n"
        except Exception as e:
            print(f"RAG 检索失败（已跳过）：{e}")

    render_vars = dict(
        notes_timeline=timeline or "（本章为第一章，尚无前文）",
        canon_block=canon_block,
        pov_block=pov_block,
        previous_ending=prev_text or "（无）",
        number=chapter["number"], title=chapter["title"],
        role=chapter["role"] or "（未设定）",
        purpose=chapter["purpose"] or "（未设定）",
        key_events=chapter["key_events"] or "（未设定）",
        characters=characters or "（未设定）",
        words_per_chapter=(proj or {}).get("words_per_chapter", 3000),
        beats_block="",
        future_outline=_fmt_future_outline(chapters, chapter),
        rag_block=rag_block,
        user_guidance=user_guidance or "（无）",
    )

    user_text = user_builder.render(**render_vars)

    messages = [{"role": "system", "content": system_text},
                {"role": "user", "content": user_text}]

    t1, t2 = estimate_tokens(system_text), estimate_tokens(character_sheets)
    t3 = estimate_tokens(user_text)
    budget = resolve_token_budget(cfg.CONFIG)
    est = t1 + t2 + t3
    warnings = []
    if est > budget:
        warnings.append(
            f"上下文估算 {est} tokens 超出预算 {budget}，已自动压缩历史要点窗口")
        # 压缩：历史要点窗口减半重装（Canon 硬约束与角色当前状态永保留）
        render_vars["notes_timeline"] = build_notes_timeline(
            chapters, chapter, max_chars=max(2000, 12000 // 4)) \
            or "（本章为第一章，尚无前文）"
        render_vars["previous_ending"] = prev_text[-800:] if prev_text else "（无）"
        render_vars["future_outline"] = _fmt_future_outline(chapters, chapter,
                                                            n=3)
        user_text = user_builder.render(**render_vars)
        messages[1]["content"] = user_text
        t3 = estimate_tokens(user_text)

    return {"messages": messages,
            "tiers": {"tier1": t1, "tier2": t2, "tier3": t3, "total": t1 + t2 + t3},
            "budget": budget, "warnings": warnings}
