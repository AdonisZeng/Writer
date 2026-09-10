"""人物：把 AI 建议文本启发式解析为角色卡字段。

用途：「采纳为角色卡」时预填既有角色对话框，**作者确认后才入库**
（不静默落盘）。解析是尽力而为的启发式：识别「标签：内容」行，
未识别的内容并入背景，作者可在对话框里修正。
"""
from __future__ import annotations

import re

# 字段 → 可识别的标签（中英文冒号均可）
_LABELS: dict[str, tuple[str, ...]] = {
    "name": ("姓名", "名称", "角色名", "人物名", "名字"),
    "role": ("定位", "身份", "角色定位", "类别", "类型"),
    "aliases": ("别名", "别称", "代称"),
    "personality": ("性格", "个性", "性情"),
    "background": ("背景", "出身", "来历", "经历"),
    "abilities": ("能力", "特长", "技能", "武学", "功法"),
    "knowledge": ("秘密", "已知", "情报", "底牌"),
}

# 中文定位 → characters.role 取值
_ROLE_MAP = {
    "主角": "protagonist", "男主": "protagonist", "女主": "protagonist",
    "反派": "antagonist", "对手": "antagonist", "大反派": "antagonist",
    "配角": "supporting", "次要角色": "supporting", "辅助": "supporting",
}

_LABEL_LINE = re.compile(
    r"^\s*(?:[-*•]|\d+[.、)]|\#{1,6})?\s*(?P<label>[^：:]{1,8})\s*[：:]\s*"
    r"(?P<value>.*)$")


def _strip_markup(line: str) -> str:
    line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)          # 标题 #
    line = re.sub(r"^\s*(?:[-*•]|\d+[.、)])\s+", "", line)   # 列表符
    return line.strip()


def _split_list(value: str) -> list[str]:
    norm = (value or "").replace("，", "、").replace(",", "、")
    return [s.strip() for s in norm.split("、") if s.strip()]


def parse_character_snippet(text: str) -> dict:
    """把一段角色描述文本解析为角色卡字段字典。

    返回键：name / role / aliases / personality / background / abilities /
    knowledge（list[str]）/ raw（原文）。
    """
    text = text or ""
    fields: dict[str, str] = {}
    extras: list[str] = []
    for raw_line in text.splitlines():
        line = _strip_markup(raw_line)
        if not line:
            continue
        matched = False
        m = _LABEL_LINE.match(line)
        if m:
            label, value = m.group("label").strip(), m.group("value").strip()
            for key, names in _LABELS.items():
                if label in names and value:
                    fields.setdefault(key, value)
                    matched = True
                    break
        if not matched:
            extras.append(line)

    name = fields.get("name", "").strip()
    if not name:
        # 兜底：取第一行「像姓名」的短行，否则默认（避免把整句描述当姓名）
        first = extras[0] if extras else ""
        name = first if 0 < len(first) <= 12 else "新角色"
        if extras and first == name:
            extras = extras[1:]

    role = "supporting"
    role_raw = fields.get("role", "")
    for zh, key in _ROLE_MAP.items():
        if zh in role_raw:
            role = key
            break

    background = fields.get("background", "").strip()
    if extras:
        extra_text = "\n".join(extras)
        background = f"{background}\n{extra_text}".strip() if background \
            else extra_text

    knowledge: list[str] = []
    if fields.get("knowledge"):
        for chunk in re.split(r"[\n;；]", fields["knowledge"]):
            knowledge.extend(_split_list(chunk))
    return {
        "name": name,
        "role": role,
        "aliases": _split_list(fields.get("aliases", "")),
        "personality": fields.get("personality", "").strip(),
        "background": background,
        "abilities": fields.get("abilities", "").strip(),
        "knowledge": knowledge,
        "raw": text.strip(),
    }
