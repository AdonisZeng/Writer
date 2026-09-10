"""AI 主动提问的结构化「选项」协议。

约定：当 AI 需要作者做决定时，在回复末尾输出一个独立围栏代码块，语言标记
为 choice，内容为 JSON（question + options:[{id,title,detail}]）。

本模块负责从回复中解析该块（容错），返回「干净正文 + 结构化选项」。
解析失败一律按原文展示，绝不吞掉内容。
"""
from __future__ import annotations

import json
import re

# 匹配 ```choice ... ```（大小写不敏感，语言标记后允许空白）
_CHOICE_RE = re.compile(r"```[ \t]*choice[ \t]*\r?\n(.*?)```", re.S | re.I)
_CHOICE_MARK = re.compile(r"```[ \t]*choice", re.I)


def has_choice_marker(text: str) -> bool:
    return bool(_CHOICE_MARK.search(text or ""))


def parse_choices(text: str) -> tuple[str, dict | None]:
    """解析回复中的 choice 块。

    返回 (clean_text, choices)。choices 结构：
    {"question": str, "options": [{"id","title","detail"}, ...]}，无则 None。
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


def _parse_block(block: str) -> dict | None:
    data = _loads(block)
    if not isinstance(data, dict):
        return None
    question = str(data.get("question") or "").strip()
    options: list[dict] = []
    for i, opt in enumerate(data.get("options") or []):
        if isinstance(opt, str):
            title, detail = opt.strip(), ""
            oid = chr(ord("A") + i)
        elif isinstance(opt, dict):
            title = str(opt.get("title") or opt.get("label") or "").strip()
            detail = str(opt.get("detail") or opt.get("desc") or "").strip()
            oid = str(opt.get("id") or chr(ord("A") + i)).strip()
        else:
            continue
        if title:
            options.append({"id": oid, "title": title, "detail": detail})
    if not question or len(options) < 2:
        return None
    return {"question": question, "options": options}


def _loads(block: str):
    txt = (block or "").strip()
    txt = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", txt).strip()
    try:
        return json.loads(txt)
    except (json.JSONDecodeError, TypeError):
        first, last = txt.find("{"), txt.rfind("}")
        if first != -1 and last > first:
            try:
                return json.loads(txt[first:last + 1])
            except (json.JSONDecodeError, TypeError):
                return None
    return None
