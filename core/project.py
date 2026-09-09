"""小说项目生命周期：创建 / 列举 / 打开（补充模块，方案未单列）。

项目 = novels/ 下的一个目录：settings.md + outline.md + chapters/ + .writer/。
"""
import os

from core import db, file_manager, paths

SETTINGS_TEMPLATE = """# 世界观与角色卡

> 本文件为人类可读的设定源：AI 生成时将作为 Tier 1 全局设定注入，
> 你可以直接用任意编辑器修改，Writer 会读取最新内容。

## 世界观
（描述世界底层法则、力量/科技体系、势力格局、地理与时代背景……）

## 角色卡

### 主角（示例，可整段替换）
- 定位：protagonist
- 性格：
- 背景：
- 能力：
- 别名/代称：
"""

OUTLINE_TEMPLATE = """# 章节细纲

> 本文件由章节表自动同步导出（单向：DB → outline.md），
> 请在 Writer 的「本章细纲」面板中编辑，以便生成时精确注入。
"""


def list_projects() -> list[str]:
    """novels/ 下所有包含 chapters/ 或 .writer/ 的子目录视为项目。"""
    if not os.path.isdir(paths.NOVELS_DIR):
        return []
    result = []
    for name in sorted(os.listdir(paths.NOVELS_DIR)):
        pdir = paths.project_dir(name)
        if not os.path.isdir(pdir):
            continue
        if os.path.isdir(os.path.join(pdir, "chapters")) or \
                os.path.isdir(paths.writer_dir(name)):
            result.append(name)
    return result


def create_project(name: str, *, title: str = "", genre: str = "",
                   total_chapters: int = 100, words_per_chapter: int = 3000,
                   writing_style: str = "", global_guidance: str = "",
                   premise: str = "") -> str:
    """创建项目骨架并初始化 project_core；返回项目目录名。"""
    name = name.strip()
    if not name:
        raise ValueError("项目名不能为空")
    pdir = paths.project_dir(name)
    if os.path.exists(pdir):
        raise FileExistsError(f"项目已存在：{name}")
    os.makedirs(paths.chapters_dir(name), exist_ok=True)
    os.makedirs(paths.drafts_dir(name), exist_ok=True)
    os.makedirs(paths.project_prompts_dir(name), exist_ok=True)
    file_manager.write_settings_md(name, SETTINGS_TEMPLATE)
    file_manager.write_text(paths.outline_md(name), OUTLINE_TEMPLATE)
    return name


async def init_project_db(name: str, *, title: str = "", genre: str = "",
                          total_chapters: int = 100,
                          words_per_chapter: int = 3000,
                          writing_style: str = "", global_guidance: str = "",
                          premise: str = "") -> None:
    """打开项目库并确保 project_core 主行存在（幂等，默认值仅在首建时生效）。"""
    await db.open(paths.state_db(name))
    await db.ensure_project_row(
        title=title or name, genre=genre, total_chapters=total_chapters,
        words_per_chapter=words_per_chapter, writing_style=writing_style,
        global_guidance=global_guidance, premise=premise)


def ensure_sample_project() -> None:
    """首次运行释放「示例小说」骨架（无章节正文，引导用户从新建开始）。"""
    if "示例小说" in list_projects():
        return
    try:
        create_project("示例小说", title="示例小说",
                       genre="", words_per_chapter=3000)
    except (FileExistsError, OSError):
        pass
