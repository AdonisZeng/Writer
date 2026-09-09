"""validator.py 离线单测（方案 第八章：与 LLM 解耦，纯算法）。"""
from core.canon import validator
from core.canon.validator import Issue


def _chapters(rows: list[dict]) -> list[dict]:
    base = {"id": "", "order_index": 0, "number": 0, "title": "",
            "role": "", "purpose": "", "key_events": "", "characters": "[]",
            "pov": "", "notes": "", "beats": "[]", "status": "outlined",
            "word_count": 0}
    return [{**base, **r} for r in rows]


def test_issue_fingerprint_stable_and_unique():
    a = Issue("warning", "知识越权", "m", quote="q")
    b = Issue("warning", "知识越权", "m", quote="q")
    c = Issue("warning", "知识越权", "different")
    assert a.fingerprint == b.fingerprint
    assert a.fingerprint != c.fingerprint


def test_metadata_number_continuity():
    chapters = _chapters([
        {"id": "a", "number": 1},
        {"id": "b", "number": 3},          # 跳号
    ])
    issues = validator.check_chapter_metadata(chapters)
    errors = [i for i in issues if i.severity == "error"]
    assert any(i.code == "chapter_number_gap" and i.chapter_id == "b"
               for i in errors)


def test_metadata_invalid_status():
    chapters = _chapters([{"id": "a", "number": 1, "status": "weird"}])
    issues = validator.check_chapter_metadata(chapters)
    assert any(i.code == "status_invalid" for i in issues)


def test_metadata_word_limit():
    chapters = _chapters([{"id": "a", "number": 1, "word_count": 9999}])
    issues = validator.check_chapter_metadata(chapters, word_limit=8000)
    assert any(i.code == "word_limit_exceeded" and i.severity == "error"
               for i in issues)
    # 限制内不报
    chapters[0]["word_count"] = 3000
    assert not validator.check_chapter_metadata(chapters, word_limit=8000)


def test_metadata_finalized_missing_notes():
    chapters = _chapters([{"id": "a", "number": 1, "status": "finalized",
                           "title": "t", "notes": ""}])
    issues = validator.check_chapter_metadata(chapters)
    assert any(i.code == "finalized_metadata_missing"
               for i in issues if i.severity == "error")
    # 有 notes 则不报
    chapters[0]["notes"] = "要点"
    assert not [i for i in validator.check_chapter_metadata(chapters)
                if i.code == "finalized_metadata_missing"]


def test_plot_dormancy_threshold():
    plot_lines = [
        {"id": 1, "name": "神秘残片", "status": "active",
         "last_advanced": "ch1", "description": ""},
        {"id": 2, "name": "已回收线", "status": "resolved",
         "last_advanced": "ch1", "description": ""},
    ]
    number_by_id = {"ch1": 1}
    issues = validator.check_plot_dormancy(plot_lines, number_by_id,
                                           current_number=30, threshold=25)
    assert len(issues) == 1
    assert issues[0].severity == "info"
    assert "休眠 29 章" in issues[0].message
    assert "神秘残片" in issues[0].message
    # 阈值内不报
    assert not validator.check_plot_dormancy(plot_lines, number_by_id,
                                             current_number=20, threshold=25)


def test_entity_count_alias_merge():
    content = "林风拔剑。白衣剑客冷笑。林风不惧。白衣剑客退了。"
    characters = [
        {"name": "林风", "aliases": "[]"},
        {"name": "萧无痕", "aliases": '["白衣剑客"]'},  # 别名归并
    ]
    issues = validator.count_entities(content, characters)
    by_name = {i.message.split("（")[0]: i for i in issues}
    assert "2" in by_name["林风"].message
    assert "2" in by_name["萧无痕"].message  # 别名归并：白衣剑客 ×2


def test_error_level_never_auto_fixed():
    """error 级问题只报告不修改（validator 无任何写接口）。"""
    assert not hasattr(validator, "fix")
    assert not hasattr(validator, "repair")
    chapters = _chapters([{"id": "a", "number": 2}])
    issues = validator.check_chapter_metadata(chapters)
    assert issues and chapters[0]["number"] == 2  # 数据未被改动
