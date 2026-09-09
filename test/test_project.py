"""project.py 测试：项目骨架、列举、幂等、库初始化。"""
import os

import pytest

from core import db, paths, project


def test_create_project_skeleton(novel_root):
    project.create_project("测试书", title="测试书")
    pdir = paths.project_dir("测试书")
    assert os.path.isdir(paths.chapters_dir("测试书"))
    assert os.path.isdir(paths.drafts_dir("测试书"))
    assert os.path.isdir(paths.project_prompts_dir("测试书"))
    assert os.path.exists(paths.settings_md("测试书"))
    assert os.path.exists(paths.outline_md("测试书"))
    assert "## 角色卡" in open(paths.settings_md("测试书"),
                              encoding="utf-8").read()


def test_create_project_duplicate(novel_root):
    project.create_project("测试书")
    with pytest.raises(FileExistsError):
        project.create_project("测试书")


def test_create_project_empty_name(novel_root):
    with pytest.raises(ValueError):
        project.create_project("  ")


def test_list_projects(novel_root):
    project.create_project("甲书")
    project.create_project("乙书")
    os.makedirs(paths.project_dir("不是项目", ), exist_ok=True)  # 无 chapters/
    names = project.list_projects()
    assert "甲书" in names and "乙书" in names
    assert "不是项目" not in names


async def test_init_project_db_defaults_only_once(novel_root):
    project.create_project("持久书")
    await project.init_project_db("持久书", title="持久书",
                                  writing_style="简练")
    await db.update_project(writing_style="用户改过的文风")
    # 再次打开（幂等）：用户数据不被默认值覆盖
    await project.init_project_db("持久书", title="持久书",
                                  writing_style="")
    proj = await db.get_project()
    assert proj["writing_style"] == "用户改过的文风"
    await db.close()


def test_ensure_sample_project_idempotent(novel_root):
    project.ensure_sample_project()
    project.ensure_sample_project()
    assert "示例小说" in project.list_projects()
