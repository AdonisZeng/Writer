"""file_manager.py 测试：文件命名、原子写、rename、归档、find 兜底规则。"""
import os

from core import file_manager, paths


def test_safe_name_strips_invalid():
    # 全角冒号在 Windows 文件名中合法，保留
    assert file_manager.safe_name('第1章：胜利/ surrender?') == "第1章：胜利 surrender"
    assert file_manager.safe_name("") == "未命名"


def test_chapter_filename():
    assert file_manager.chapter_filename(3, "传承") == "第3章 传承.md"


def test_write_read_atomic(novel_root, db_project):
    p = file_manager.chapter_path(db_project, 1, "开局")
    file_manager.write_text(p, "正文内容")
    assert file_manager.read_text(p) == "正文内容"
    assert not os.path.exists(p + ".tmp")  # 原子写无残留


def test_rename_chapter_file(novel_root, db_project):
    old = file_manager.chapter_path(db_project, 2, "旧名")
    file_manager.write_text(old, "内容")
    new = file_manager.rename_chapter_file(db_project, 2, "旧名", 3, "新名")
    assert os.path.basename(new) == "第3章 新名.md"
    assert os.path.exists(new) and not os.path.exists(old)
    assert file_manager.read_text(new) == "内容"


def test_rename_missing_old_is_noop(novel_root, db_project):
    new = file_manager.rename_chapter_file(db_project, 9, "不存在", 10, "不存在")
    assert os.path.basename(new) == "第10章 不存在.md"


def test_rename_target_conflict_suffix(novel_root, db_project):
    src = file_manager.chapter_path(db_project, 1, "甲")
    dst = file_manager.chapter_path(db_project, 2, "乙")
    file_manager.write_text(src, "甲内容")
    file_manager.write_text(dst, "乙内容")  # 目标已占用
    new = file_manager.rename_chapter_file(db_project, 1, "甲", 2, "乙")
    assert new != dst and os.path.exists(new)
    assert os.path.exists(dst)  # 原目标不被覆盖


def test_archive_draft(novel_root, db_project):
    dst = file_manager.archive_draft(db_project, 12, 3, "历史版本")
    assert os.path.basename(dst) == "ch_012_v3.md"
    assert ".writer" in dst
    assert file_manager.read_text(dst) == "历史版本"


def test_find_exact(novel_root, db_project):
    p = file_manager.chapter_path(db_project, 1, "开局")
    file_manager.write_text(p, "")
    assert file_manager.find_chapter_file(db_project, 1, "开局") == p


def test_find_unique_prefix_fallback(novel_root, db_project):
    """外部把文件改名（标题部分变了）→ 前缀唯一时仍可定位。"""
    p = os.path.join(paths.chapters_dir(db_project), "第1章 外部改的名.md")
    file_manager.write_text(p, "")
    assert file_manager.find_chapter_file(db_project, 1, "开局") == p


def test_find_ambiguous_no_guess(novel_root, db_project):
    """同前缀多文件且无精确标题命中 → 不猜测。"""
    cdir = paths.chapters_dir(db_project)
    for name in ("第1章 残留甲.md", "第1章 残留乙.md"):
        file_manager.write_text(os.path.join(cdir, name), "")
    expected = file_manager.chapter_path(db_project, 1, "开局")
    assert file_manager.find_chapter_file(db_project, 1, "开局") == expected


def test_find_ambiguous_prefers_exact_title(novel_root, db_project):
    cdir = paths.chapters_dir(db_project)
    for name in ("第1章 残留甲.md", "第1章 开局.md"):
        file_manager.write_text(os.path.join(cdir, name), "")
    p = file_manager.find_chapter_file(db_project, 1, "开局")
    assert os.path.basename(p) == "第1章 开局.md"


def test_file_mtime_missing():
    assert file_manager.file_mtime("Z:/not/exist.md") == 0.0


def test_sync_outline_md(novel_root, db_project):
    file_manager.sync_outline_md(db_project, [
        {"number": 1, "title": "开局", "status": "finalized",
         "role": "开篇", "purpose": "立主角", "key_events": "拜师",
         "characters": '["林风"]', "notes": "要点A"},
        {"number": 2, "title": "传承", "status": "outlined",
         "role": "", "purpose": "", "key_events": "",
         "characters": "[]", "notes": ""},
    ])
    text = file_manager.read_outline_md(db_project)
    assert "第1章 开局" in text and "- 状态：finalized" in text
    assert "结构角色：开篇" in text and "关键事件：拜师" in text
    assert "第2章 传承" in text
