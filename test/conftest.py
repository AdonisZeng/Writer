"""pytest 全局设施：导入路径 + 隔离夹具。

隔离原则：所有测试不触碰真实 novels/、真实 ~/.writer/prompts 与根目录
config.json——统一 monkeypatch 到 pytest tmp_path。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db, paths, project  # noqa: E402


@pytest.fixture
def novel_root(tmp_path, monkeypatch):
    """把所有可写路径重定向到临时目录。"""
    root = tmp_path / "novels"
    root.mkdir()
    monkeypatch.setattr(paths, "NOVELS_DIR", str(root))
    monkeypatch.setattr(paths, "USER_PROMPTS_DIR", str(tmp_path / "user_prompts"))
    monkeypatch.setattr(paths, "CONFIG_FILE", str(tmp_path / "config.json"))
    return root


@pytest.fixture
async def db_project(novel_root):
    """一个已建库的隔离测试项目；用毕关闭全局 db。"""
    name = "_test_project"
    project.create_project(name, title="测试项目")
    await project.init_project_db(name, title="测试项目")
    yield name
    await db.close()
