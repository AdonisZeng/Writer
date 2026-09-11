"""AI 主动提问的结构化「选项」协议。

约定：当 AI 需要作者做决定时，在回复末尾输出一个独立围栏代码块，语言标记
为 choice，内容为 JSON（question + options:[{id,title,detail}]）。

分步引导的「本步收尾」在此之上扩展（可选字段，向后兼容）：
- step_done：当前引导步骤 key，标记这是一个收尾确认块；
- option.action：动作语义（adopt=直接采纳并进入下一步 / discuss=还要调整）；
- sections：世界观步的分节计划 [{label, content}]。

本模块负责从回复中解析该块（容错），返回「干净正文 + 结构化选项」。
解析失败一律按原文展示，绝不吞掉内容。
"""
from __future__ import annotations

import json
import re

# 匹配 ```choice ... ```（大小写不敏感，语言标记后允许空白）
_CHOICE_RE = re.compile(r"```[ \t]*choice[ \t]*\r?\n(.*?)```", re.S | re.I)
_CHOICE_MARK = re.compile(r"```[ \t]*choice", re.I)

# 已知的动作语义（未知值丢弃，避免把自由文本当动作执行）
_ACTIONS = ("adopt", "discuss")

# 小模型常把 action 写进 title/detail 文本而非 JSON 字段
# （如 detail="action=\"adopt\"：确认此方案…"），做文本嗅探兜底
_ACTION_TEXT_RE = re.compile(
    r"action\s*[=:：]?\s*[\"'“”「『]?\s*(adopt|discuss)", re.I)


def _sniff_action(*texts: str) -> str:
    for text in texts:
        m = _ACTION_TEXT_RE.search(text or "")
        if m:
            return m.group(1).lower()
    return ""


def has_choice_marker(text: str) -> bool:
    return bool(_CHOICE_MARK.search(text or ""))


def parse_choices(text: str) -> tuple[str, dict | None]:
    """解析回复中的 choice 块。

    返回 (clean_text, choices)。choices 结构：
    {"question": str, "options": [{"id","title","detail","action"?}, ...],
     "step_done"?: str, "sections"?: [{"label","content"}, ...]}，无则 None。
    未命中 → (原文, None)；命中但解析失败 → (原文, None)（不吞内容）。
    """
    raw = text or ""
    m = _CHOICE_RE.search(raw)
    if not m:
        return raw, None
    choices = _parse_block(m.group(1))
    if choices is None:
        return raw, None
    clean = (raw[:m.start()] + raw[m.end():]).strip()
    return clean, choices


def remove_choice_blocks(text: str) -> str:
    """移除正文中的全部 choice 围栏块（含流中断产生的未闭合悬挂块）。"""
    out = _CHOICE_RE.sub("", text or "")
    m = _CHOICE_MARK.search(out)     # 悬挂的 ```choice 开头（无闭合围栏）
    if m:
        out = out[:m.start()]        # 连同其后残缺内容一并丢弃
    return out.strip()


def _parse_block(block: str) -> dict | None:
    data = loads_json(block)
    if not isinstance(data, dict):
        return None
    question = str(data.get("question") or "").strip()
    options: list[dict] = []
    for i, opt in enumerate(data.get("options") or []):
        action = ""
        if isinstance(opt, str):
            title, detail = opt.strip(), ""
            oid = chr(ord("A") + i)
        elif isinstance(opt, dict):
            title = str(opt.get("title") or opt.get("label") or "").strip()
            detail = str(opt.get("detail") or opt.get("desc") or "").strip()
            oid = str(opt.get("id") or chr(ord("A") + i)).strip()
            raw_action = str(opt.get("action") or "").strip().lower()
            action = raw_action if raw_action in _ACTIONS else ""
        else:
            continue
        if not action:
            oid_key = str(oid).strip().lower()
            action = (oid_key if oid_key in _ACTIONS
                      else _sniff_action(title, detail))   # id / 文本兜底
        if title:
            item = {"id": oid, "title": title, "detail": detail}
            if action:
                item["action"] = action
            options.append(item)
    if not question or len(options) < 2:
        return None
    result = {"question": question, "options": options}
    step_done = str(data.get("step_done") or "").strip()
    if step_done:
        result["step_done"] = step_done
    sections = _parse_sections(data.get("sections"))
    if sections:
        result["sections"] = sections
    return result


def _parse_sections(raw) -> list[dict]:
    """容错解析收尾块的分节计划（label 非空才保留）。"""
    out: list[dict] = []
    for item in raw or []:
        if isinstance(item, str):
            label, content = item.strip(), ""
        elif isinstance(item, dict):
            label = str(item.get("label") or item.get("name") or "").strip()
            content = str(item.get("content") or item.get("text") or "").strip()
        else:
            continue
        if label:
            out.append({"label": label, "content": content})
    return out


# ==================== 收尾协议审计（自动打回重试用） ====================

# 正文声称"待作者确认收尾"的措辞信号（命中且无 choice 块 → 打回）
_FINISH_CLAIM_HINTS = ("是否采纳", "是否定稿", "定稿", "进入下一步", "是否进入",
                       "采纳本方案", "是否确认")


def protocol_issues(raw_text: str, choices: dict | None, *,
                    step_key: str) -> list[str]:
    """审计一条 AI 回复是否符合「本步收尾」协议，返回问题列表（空=合规）。

    仅在 step_key 支持收尾（core/world）时调用；UI 据此自动打回让 AI 重输：
    - 有 choice 围栏却解析失败，或正文声称收尾却没有 choice 块；
    - 收尾块的 action 没写成结构化字段、step_done 缺失或与预期不符；
    - 世界观步缺少 sections 分节计划（被动兜底无法还原结构，必须重输）。
    """
    text = raw_text or ""
    if step_key not in ("core", "world"):
        return []
    if choices is None:
        if has_choice_marker(text):
            return ["choice 代码块存在但无法解析（JSON 语法错误或字段不完整）"]
        if _claims_finish(text):
            return ["本条回复在请作者确认收尾，但没有输出 choice 代码块"]
        return []
    options = choices.get("options") or []
    acts = [str(o.get("action") or "") for o in options]
    if not any(a in _ACTIONS for a in acts):
        return []            # 普通选择块（非收尾确认），不干预
    issues: list[str] = []
    if not _has_structured_action(text):
        issues.append("选项的 action 被写进了 title/detail 文字，"
                      "必须作为选项对象的 JSON 字段")
    step_done = str(choices.get("step_done") or "")
    if not step_done:
        issues.append(f'缺少 "step_done" 字段（必须是 "{step_key}"）')
    elif step_done != step_key:
        issues.append(f'"step_done" 必须是 "{step_key}"，当前写成了 '
                      f'"{step_done}"（不要翻译或自创名称）')
    if step_key == "world" and not choices.get("sections"):
        issues.append('缺少 "sections" 分节计划（每项 label + 该分节完整内容）')
    return issues


def _claims_finish(text: str) -> bool:
    """正文是否在请作者确认收尾（带问句 + 收尾措辞）。"""
    if "？" not in text and "?" not in text:
        return False
    return any(k in text for k in _FINISH_CLAIM_HINTS)


def _has_structured_action(text: str) -> bool:
    """原始 choice 块里是否有选项带结构化 action 字段（区别于文本嗅探）。"""
    m = _CHOICE_RE.search(text or "")
    if not m:
        return False
    data = loads_json(m.group(1))
    if not isinstance(data, dict):
        return False
    for opt in data.get("options") or []:
        if isinstance(opt, dict) and \
                str(opt.get("action") or "").strip().lower() in _ACTIONS:
            return True
    return False


def retry_feedback(step_key: str, issues: list[str]) -> str:
    """构造打回消息：要求 AI 重新输出完整且规范的收尾回复。"""
    items = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(issues))
    extra = ""
    if step_key == "world":
        extra = ('\n- "sections"：数组，每项 {"label": 分节名, "content": 该'
                 '分节的完整内容}，按作品题材自由规划 3~6 个分节；')
    return (
        "【格式打回】你上一条回复没有按互动协议输出收尾确认，请重新输出完整"
        "回复（正文照常 + 修正后的 choice 代码块）。需要修正的点：\n"
        f"{items}\n"
        "格式要求：\n"
        f'- 必须包含 "step_done": "{step_key}"（原样复制，不要自创名称）；\n'
        '- 选项必须带结构化 JSON 字段 "action"（"adopt" 表示直接采纳并进入'
        '下一步，"discuss" 表示还要调整），不要写进 title/detail 文字；\n'
        "- 收尾轮正文必须包含本步的完整最终方案；"
        f"{extra}\n"
        "若你本条回复并非请作者确认收尾，请忽略以上要求、照常回复即可。"
    )


def loads_json(block: str):
    """容错 JSON 解析：剥围栏 → 截取花括号边界 → 渐进修复（尾逗号 /
    中文引号边界 / 单引号边界 / 字符串内裸换行）。

    修复按代价从低到高逐级尝试，任一级成功即返回；全部失败返回 None。
    """
    txt = (block or "").strip()
    txt = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", txt).strip()
    candidates = [txt]
    first, last = txt.find("{"), txt.rfind("}")
    if first != -1 and last > first:
        candidates.append(txt[first:last + 1])
    for cand in candidates:
        for variant in _json_variants(cand):
            for strict in (True, False):   # strict=False 容忍字符串内裸换行
                try:
                    return json.loads(variant, strict=strict)
                except (json.JSONDecodeError, TypeError):
                    continue
    return None


def _json_variants(txt: str) -> list[str]:
    """小模型 JSON 常见病灶的渐进修复变体（由保守到激进）。"""
    out = [txt]
    no_trail = re.sub(r",\s*([}\]])", r"\1", txt)   # 尾逗号
    if no_trail != txt:
        out.append(no_trail)
    if "“" in txt or "”" in txt:
        # 中文引号当字符串边界（模型忘了切换输入法）→ 换成 ASCII 引号
        out.append(no_trail.replace("“", '"').replace("”", '"'))
    if "'" in txt:
        out.append(no_trail.replace("'", '"'))   # 最后手段：可能误伤撇号
    return out


# ==================== 收尾工具（Function Calling 快路径） ====================
# 模型支持 Tool Call 时，收尾动作经 tool_calls 传输：枚举 / 必填由引擎级
# Schema 强制（从物理层面掐死 step_done 拼错、action 写进文字两类病灶），
# 正文（content）与协议数据在数据流上天然解耦。接收侧把工具参数归一成与
# ```choice 块完全相同的结构（tool_args_to_choices → choice_block 拼回
# 正文），下游审计 / 采纳 / 历史回显零改动。

FINISH_TOOL_NAME = "submit_step_finish"

# 收尾选项缺省合成（模型漏给 / 只给一个选项时补齐）
_DEFAULT_OPTIONS: dict[str, dict] = {
    "adopt": {"id": "adopt", "action": "adopt",
              "title": "直接采纳，进入下一步",
              "detail": "写入设定库并推进引导"},
    "discuss": {"id": "tweak", "action": "discuss",
                "title": "还要调整", "detail": "继续讨论本步"},
}


def _ensure_finish_options(options: list[dict]) -> list[dict]:
    """补齐收尾选项：保证 ≥ 2 项且 adopt / discuss 两类动作齐备。

    下游 parse_choices 要求 options 至少 2 项，少于 2 项时归一出来的
    块根本解析不出来（收尾卡整块丢失、还会触发一次无谓的打回），
    因此在这里兜底而不是等解析层报错。
    """
    if not any(o.get("action") for o in options):
        return [dict(_DEFAULT_OPTIONS[a]) for a in _ACTIONS]
    have = {o["action"] for o in options if o.get("action")}
    out = list(options)
    for act in _ACTIONS:
        if act not in have:
            out.append(dict(_DEFAULT_OPTIONS[act]))
    return out

FINISH_TOOL: dict = {
    "type": "function",
    "function": {
        "name": FINISH_TOOL_NAME,
        "description": (
            "分步引导收尾：当且仅当本步设计已完成、需要作者确认"
            "（直接采纳并进入下一步，或继续调整）时调用。正文 content 照常"
            "给出本步的完整最终方案；非收尾轮不要调用本工具。"),
        "parameters": {
            "type": "object",
            "properties": {
                "step_done": {
                    "type": "string",
                    "enum": ["core", "world"],
                    "description": "当前完成的引导步骤 key，原样使用"
                                   "（core=故事内核，world=世界观）",
                },
                "options": {
                    "type": "array",
                    "minItems": 2,
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["adopt", "discuss"],
                                "description": "adopt=直接采纳并进入下一步；"
                                               "discuss=还要调整",
                            },
                            "title": {"type": "string"},
                            "detail": {"type": "string"},
                        },
                        "required": ["action", "title"],
                    },
                },
                "sections": {
                    "type": "array",
                    "description": "step_done 为 world 时必填：世界观分节计划",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "content": {"type": "string",
                                        "description": "该分节的完整内容"},
                        },
                        "required": ["label", "content"],
                    },
                },
            },
            "required": ["step_done", "options"],
        },
    },
}


def tool_args_to_choices(args, *, step_key: str = "") -> dict | None:
    """把收尾工具参数归一成 choice 块同构 dict（下游零改动）。

    - step_done 经语义归一，无法定位步骤（且调用方未提供）→ None；
    - 选项缺 action 字段时先文本嗅探，仍全部缺失则合成标准
      「采纳 / 调整」两项；只有一项有效选项时补齐另一类动作
      （下游 parse_choices 要求 options ≥ 2，否则收尾卡整块丢失）；
    - args 非法返回 None，由文本协议兜底。
    """
    if not isinstance(args, dict):
        return None
    from core.design.guide import resolve_step_key
    key = resolve_step_key(str(args.get("step_done") or "")) or \
        (step_key if step_key in ("core", "world") else "")
    if not key:
        return None          # 连步骤都定位不了，无法当收尾处理
    options: list[dict] = []
    for i, opt in enumerate(args.get("options") or []):
        if not isinstance(opt, dict):
            continue
        title = str(opt.get("title") or "").strip()
        if not title:
            continue
        detail = str(opt.get("detail") or "").strip()
        action = str(opt.get("action") or "").strip().lower()
        if action not in _ACTIONS:
            action = _sniff_action(title, detail)
        oid = str(opt.get("id") or "").strip() or chr(ord("A") + i)
        item = {"id": oid, "title": title, "detail": detail}
        if action:
            item["action"] = action
        options.append(item)
    choices: dict = {
        "question": str(args.get("question") or "").strip()
        or "本步方案是否采纳并进入下一步？",
        "options": _ensure_finish_options(options),
        "step_done": key,
    }
    sections = _parse_sections(args.get("sections"))
    if sections:
        choices["sections"] = sections
    return choices


def choice_block(choices: dict) -> str:
    """把结构化 choices 渲染回标准 ```choice 代码块（工具 → 文本协议归一）。"""
    return "```choice\n" + json.dumps(choices, ensure_ascii=False) + "\n```"
