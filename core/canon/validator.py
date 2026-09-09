"""确定性校验：纯算法零 LLM 成本，只做不依赖语义理解的事（方案 5.4.2 第一层）。

唯一拥有 BLOCK 权力的层；文学表达不在此层判断（时间线倒退等归语义审查层）。
本模块必须保持零外部依赖，配备独立单元测试（方案 第八章）。
"""
import re
from dataclasses import dataclass, field

from core.canon.store import VALID_STATUSES

SEVERITY_ERROR = "error"      # 🔴 确定性阻断
SEVERITY_WARNING = "warning"  # 🟡 语义诊断（第二层产出，本层不产出）
SEVERITY_INFO = "info"        # 🔵 作者提醒


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    quote: str = ""            # 定位：原文片段
    paragraph: int = 0         # 定位：段落序号（1 起；0=章节级）
    chapter_id: str = ""
    fingerprint: str = field(default="")   # 忽略持久化的键（自动生成）

    def __post_init__(self):
        if not self.fingerprint:
            import hashlib
            raw = f"{self.severity}|{self.code}|{self.message}|{self.quote}"
            self.fingerprint = hashlib.md5(raw.encode("utf-8")).hexdigest()


# 章节标题行的展示形式（信息统计用）
_ENTITY_CONTEXT = 12  # 统计词频时取的上下文窗口（字）


def check_chapter_metadata(chapters: list[dict],
                           word_limit: int = 8000) -> list[Issue]:
    """元数据硬检查（error 级）：章号连续性 / status 合法性 / 字数硬上限 /
    定稿关键元数据缺失。"""
    issues: list[Issue] = []
    for i, ch in enumerate(chapters, start=1):
        cid = ch["id"]
        if ch["number"] != i:
            issues.append(Issue(
                SEVERITY_ERROR, "chapter_number_gap",
                f"章号连续性错误：第 {i} 位应为 {i}，实际为 {ch['number']}"
                f"（《{ch['title']}》）", chapter_id=cid))
        if ch["status"] not in VALID_STATUSES:
            issues.append(Issue(
                SEVERITY_ERROR, "status_invalid",
                f"章节状态非法：{ch['status']!r}（《{ch['title']}》）",
                chapter_id=cid))
        if ch["word_count"] > word_limit:
            issues.append(Issue(
                SEVERITY_ERROR, "word_limit_exceeded",
                f"字数硬上限：{ch['word_count']} 字超过上限 {word_limit}"
                f"（《{ch['title']}》）——建议拆章", chapter_id=cid))
        if ch["status"] == "finalized":
            missing = []
            if not ch.get("number"):
                missing.append("章号")
            if not (ch.get("title") or "").strip():
                missing.append("标题")
            if not (ch.get("notes") or "").strip():
                missing.append("定稿要点（notes）")
            if missing:
                issues.append(Issue(
                    SEVERITY_ERROR, "finalized_metadata_missing",
                    f"定稿关键元数据缺失：{'、'.join(missing)}"
                    f"（《{ch['title']}》）", chapter_id=cid))
    return issues


def check_plot_dormancy(plot_lines: list[dict],
                        chapter_number_by_id: dict[str, int],
                        current_number: int,
                        threshold: int = 25) -> list[Issue]:
    """伏笔休眠监测（info 级，方案 5.4.5）：纯数值计算。"""
    issues: list[Issue] = []
    for p in plot_lines:
        if p["status"] != "active":
            continue
        last_n = chapter_number_by_id.get(p["last_advanced"], 0)
        gap = current_number - last_n
        if gap > threshold:
            issues.append(Issue(
                SEVERITY_INFO, "plot_line_dormant",
                f"『{p['name']}』已休眠 {gap} 章（阈值 {threshold}），"
                f"建议近期安排回收或推进", chapter_id=p["last_advanced"]))
    return issues


def count_entities(content: str, characters: list[dict]) -> list[Issue]:
    """实体出现/离场统计（info 级）：正文词频 + aliases 别名字典归并。
    只统计，不判违规（方案 5.4.2）。"""
    issues: list[Issue] = []
    for ch in characters:
        names = [ch["name"]]
        try:
            import json
            aliases = json.loads(ch.get("aliases") or "[]")
            if isinstance(aliases, list):
                names.extend(a for a in aliases if a)
        except (json.JSONDecodeError, TypeError):
            pass
        text = content or ""
        total = sum(text.count(n) for n in names if n)
        if total:
            issues.append(Issue(
                SEVERITY_INFO, "entity_mention",
                f"{ch['name']}（{len(names)} 个称谓）在正文出现 {total} 次",
                quote=names[0], paragraph=0))
    return issues


def split_paragraphs(content: str) -> list[str]:
    """按段落切分（供语义诊断的段落定位回显）。"""
    return [p for p in re.split(r"\n+", content or "") if p.strip()]
