"""敏感词预检（P3，方案 阶段规划表）：确定性文本检查，info 级诊断。

词表来源（并集）：
- 项目级：novels/<项目>/.writer/sensitive_words.txt（一行一词，# 开头为注释）
- 应用级：Writer 根目录 sensitive_words.txt
预检只报告命中，绝不自动改写（UI 负面清单精神）。
"""
import os
from pathlib import Path

from core import paths
from core.canon.validator import Issue


def load_words(project: str = "") -> list[str]:
    words: list[str] = []
    for path in (_project_words_path(project), _app_words_path()):
        try:
            if path and os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        w = line.strip()
                        if w and not w.startswith("#") and w not in words:
                            words.append(w)
        except OSError:
            continue
    return words


def _project_words_path(project: str) -> str:
    if not project:
        return ""
    return str(Path(paths.writer_dir(project)) / "sensitive_words.txt")


def _app_words_path() -> str:
    return os.path.join(paths.ROOT_DIR, "sensitive_words.txt")


def create_project_template(project: str) -> str:
    """在项目 .writer/ 下生成词表模板（幂等）。"""
    path = _project_words_path(project)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("# 敏感词预检词表：一行一词，# 开头为注释\n"
                    "# 命中时会在 Canon 诊断中以 info 级提示\n")
    return path


def check(content: str, words: list[str],
          chapter_id: str = "") -> list[Issue]:
    """检查正文命中；返回 info 级 Issue（含次数）。"""
    issues: list[Issue] = []
    text = content or ""
    if not text:
        return issues
    for w in words:
        n = text.count(w)
        if n:
            issues.append(Issue(
                severity="info", code="sensitive_word",
                message=f"敏感词预检命中：「{w}」出现 {n} 次（请作者复核）",
                quote=w, chapter_id=chapter_id))
    return issues
