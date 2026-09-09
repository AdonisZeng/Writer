"""项目目录常量集中管理（兼容 PyInstaller 打包后路径）。

打包后（sys.frozen）基准路径为 exe 所在目录；源码运行时为仓库根目录。
"""
import os
import sys


def get_root_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ROOT_DIR = get_root_dir()
CONFIG_FILE = os.path.join(ROOT_DIR, "config.json")
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")

# 内置 Prompt 模板目录（随包分发）
BUILTIN_PROMPTS_DIR = os.path.join(ROOT_DIR, "prompts")

# 用户数据目录：小说项目默认存放在应用根目录 novels/ 下
NOVELS_DIR = os.path.join(ROOT_DIR, "novels")

# 用户级目录（打包后应用根目录可能只读，Prompt 用户级覆盖放这里）
USER_DIR = os.path.join(os.path.expanduser("~"), ".writer")
USER_PROMPTS_DIR = os.path.join(USER_DIR, "prompts")


def ensure_base_dirs() -> None:
    os.makedirs(NOVELS_DIR, exist_ok=True)
    os.makedirs(USER_PROMPTS_DIR, exist_ok=True)


def project_dir(name: str) -> str:
    return os.path.join(NOVELS_DIR, name)


def writer_dir(project: str) -> str:
    return os.path.join(project_dir(project), ".writer")


def state_db(project: str) -> str:
    return os.path.join(writer_dir(project), "state.db")


def drafts_dir(project: str) -> str:
    return os.path.join(writer_dir(project), "drafts")


def project_prompts_dir(project: str) -> str:
    return os.path.join(writer_dir(project), "prompts")


def chapters_dir(project: str) -> str:
    return os.path.join(project_dir(project), "chapters")


def settings_md(project: str) -> str:
    return os.path.join(project_dir(project), "settings.md")


def outline_md(project: str) -> str:
    return os.path.join(project_dir(project), "outline.md")
