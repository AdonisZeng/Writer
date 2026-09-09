"""红绿 Diff 计算（P2，方案 6.1-3 / 6.3）：difflib 求块级 hunks。

红删绿增对照卡：hunk 级 Accept / Reject，逐块写回选区，
绝不允许无预览的一刀切覆盖（UI 负面清单 3）。
"""
import difflib
from dataclasses import dataclass, field


@dataclass
class Hunk:
    action: str            # replace / delete / insert
    a1: int                # 旧文本起止（行号，0 起）
    a2: int
    b1: int                # 新文本起止
    b2: int
    old_text: str = ""
    new_text: str = ""
    accepted: bool = field(default=True)   # UI 默认勾选


def compute_hunks(old: str, new: str) -> list[Hunk]:
    """行级 diff → hunk 列表（等值块跳过）。old/new 按行切分保留换行。"""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    hunks = []
    for tag, a1, a2, b1, b2 in sm.get_opcodes():
        if tag == "equal":
            continue
        old_text = "".join(old_lines[a1:a2])
        new_text = "".join(new_lines[b1:b2])
        hunks.append(Hunk(action=tag, a1=a1, a2=a2, b1=b1, b2=b2,
                          old_text=old_text, new_text=new_text))
    return hunks


def apply_hunks(old: str, hunks: list[Hunk], accepted_idx: set[int]) -> str:
    """把被采纳的 hunk 应用到旧文本；未采纳块保持原文。"""
    old_lines = old.splitlines(keepends=True)
    out: list[str] = []
    pos = 0
    for i, h in enumerate(hunks):
        if pos < h.a1:
            out.extend(old_lines[pos:h.a1])
        if i in accepted_idx:
            if h.action in ("replace", "insert"):
                out.extend(h.new_text.splitlines(keepends=True))
            # delete：跳过旧文
        else:
            out.extend(old_lines[h.a1:h.a2])
        pos = h.a2
    if pos < len(old_lines):
        out.extend(old_lines[pos:])
    return "".join(out)


def hunks_summary(hunks: list[Hunk]) -> str:
    add = sum(len(h.new_text.splitlines()) for h in hunks
              if h.action in ("replace", "insert"))
    rem = sum(len(h.old_text.splitlines()) for h in hunks
              if h.action in ("replace", "delete"))
    return f"+{add} / -{rem} 行"
