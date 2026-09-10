"""ui/theme.py 测试：令牌确定性、字体降级、主题与工厂可构建。"""
import os

import flet as ft
import pytest

from ui import theme


@pytest.fixture
def restore_font():
    """用例改过全局字号基准后恢复默认，避免污染其他用例。"""
    yield
    theme.apply_font_size(theme.FONT_BASE_DEFAULT)


# ==================== 全局字号（基准 + 比例 + 即时缩放） ====================

def test_font_roles_derive_from_base(restore_font):
    assert theme.base_font_size() == theme.FONT_BASE_DEFAULT
    assert theme.SIZE_MD == theme.FONT_BASE_DEFAULT
    assert theme.SIZE_SECTION == theme.SIZE_MD
    assert theme.SIZE_XXS < theme.SIZE_XS < theme.SIZE_SM <= theme.SIZE_MD
    assert theme.SIZE_MD < theme.SIZE_LG < theme.SIZE_BRAND < theme.SIZE_XL
    theme.apply_font_size(18)
    assert theme.SIZE_MD == 18 and theme.base_font_size() == 18
    assert theme.SIZE_SM == round(18 * 12 / 13)


def test_apply_font_size_clamped(restore_font):
    assert theme.apply_font_size(999) == theme.FONT_BASE_MAX
    assert theme.apply_font_size(1) == theme.FONT_BASE_MIN


def test_rescale_value_is_idempotent(restore_font):
    assert theme._rescale_value(theme.SIZE_SM, 13, 13) == theme.SIZE_SM
    once = theme._rescale_value(theme.SIZE_SM, 13, 18)
    assert once == round(18 * 12 / 13)          # SM 比例 12/13
    # 同基准再换算不变（幂等，不漂移）
    assert theme._rescale_value(once, 18, 18) == once
    # 回到 13 应还原原始字号
    assert theme._rescale_value(once, 18, 13) == theme.SIZE_SM


def test_rescale_tree_only_touches_text(restore_font):
    text = ft.Text("x", size=theme.SIZE_SM)
    field = ft.TextField(text_size=theme.SIZE_MD)
    icon = ft.Icon(ft.Icons.ADD, size=theme.ICON_INLINE)
    md = ft.Markdown("m", md_style_sheet=theme.chat_markdown_style_sheet())
    root = ft.Column([text, field, icon, md])
    theme.apply_font_size(18, controls=root)
    assert text.size == theme.SIZE_SM
    assert field.text_size == theme.SIZE_MD
    assert icon.size == theme.ICON_INLINE                      # 图标不变
    assert md.md_style_sheet.p_text_style.size == theme.SIZE_SM
    # 幂等：同基准再应用一次不变
    snap = (text.size, field.text_size, md.md_style_sheet.p_text_style.size)
    theme.apply_font_size(18, controls=root)
    assert (text.size, field.text_size,
            md.md_style_sheet.p_text_style.size) == snap


def test_mono_style_and_metric_follow_base(restore_font):
    assert theme.mono_style().size == theme.SIZE_SM
    assert theme.metric_text("x").style.size == theme.SIZE_SM
    theme.apply_font_size(18)
    assert theme.mono_style().size == theme.SIZE_SM
    assert theme.metric_text("x").style.size == theme.SIZE_SM


# ==================== 颜色工具 ====================

def test_mix_boundaries_and_format():
    assert theme.mix("#000000", "#FFFFFF", 0.0) == "#000000"
    assert theme.mix("#000000", "#FFFFFF", 1.0) == "#FFFFFF"
    assert theme.mix("#000000", "#FFFFFF", 0.5) == "#808080"
    # 越界 t 被 clamp
    assert theme.mix("#000000", "#FFFFFF", -1) == "#000000"
    assert theme.mix("#000000", "#FFFFFF", 2) == "#FFFFFF"
    mid = theme.mix("#8A5A3B", "#FBF9F5", 0.3)
    assert mid.startswith("#") and len(mid) == 7


def test_seed_pair_fallback():
    assert theme.seed_pair("ochre") == theme.SEEDS["ochre"]
    # 未知种子回落默认，不抛异常
    assert theme.seed_pair("不存在") == theme.SEEDS[theme.DEFAULT_SEED]
    assert theme.seed_pair("") == theme.SEEDS[theme.DEFAULT_SEED]


def test_semantic_color_fallback():
    assert theme.semantic_color("success") == theme.SEMANTIC["success"]["light"]
    assert theme.semantic_color("danger", dark=True) == \
        theme.SEMANTIC["danger"]["dark"]
    # 未知名字回落 neutral
    assert theme.semantic_color("nope") == theme.SEMANTIC["neutral"]["light"]


# ==================== 状态 / 严重度映射 ====================

def test_status_mapping_is_deterministic():
    for status in ("outlined", "drafted", "revised", "finalized"):
        assert theme.status_color(status) == theme.status_color(status)
        assert theme.status_label(status)
    assert theme.status_color("finalized") == theme.semantic_color("success")
    assert theme.status_color("unknown") == theme.semantic_color("neutral")


def test_severity_mapping():
    assert theme.severity_color("error") == theme.semantic_color("danger")
    assert theme.severity_color("warning") == theme.semantic_color("warning")
    assert theme.severity_color("info") == theme.semantic_color("info")
    assert theme.SEVERITY_ORDER == ("error", "warning", "info")


# ==================== 字体降级 ====================

def test_fonts_map_empty_when_missing(tmp_path):
    assert theme.fonts_map(str(tmp_path / "nope")) == {}


def test_fonts_map_only_includes_existing(tmp_path):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    (fonts / theme.FONT_FILES[theme.FONT_UI]).write_bytes(b"fake")
    mapping = theme.fonts_map(str(fonts))
    assert mapping == {theme.FONT_UI: f"fonts/{theme.FONT_FILES[theme.FONT_UI]}"}


def test_register_fonts_sets_page_fonts():
    class _Page:
        fonts = {}

    page = _Page()
    mapping = theme.register_fonts(page, fonts_dir_path="__missing__")
    assert mapping == {}
    assert page.fonts == {}


def test_font_fallback_chain_present():
    assert theme.FONT_UI_FALLBACK and theme.FONT_SERIF_FALLBACK
    assert "sans-serif" in theme.FONT_UI_FALLBACK[-1]
    assert theme.mono_style().font_family == theme.FONT_MONO


# ==================== 主题构建 ====================

def test_color_scheme_light_and_dark():
    light = theme.build_color_scheme("ochre", dark=False)
    assert light.primary == theme.SEEDS["ochre"][0]
    assert light.surface == theme.PAPER
    assert light.on_surface == theme.INK
    assert light.outline_variant == theme.BORDER

    dark = theme.build_color_scheme("ochre", dark=True)
    assert dark.primary == theme.SEEDS["ochre"][1]
    assert dark.surface == theme.PAPER_DARK
    assert dark.on_surface == theme.INK_DARK


def test_build_theme_without_fonts_keeps_font_family_unset():
    t = theme.build_theme("ochre", registered={})
    assert isinstance(t, ft.Theme)
    assert t.color_scheme is not None
    assert not t.font_family  # 未注册字体 → 交回系统默认


def test_build_theme_with_fonts_sets_font_family():
    registered = {theme.FONT_UI: "fonts/x.ttf", theme.FONT_SERIF: "fonts/y.ttf"}
    t = theme.build_theme("celadon", registered=registered)
    assert t.font_family == theme.FONT_UI
    assert t.text_theme is not None


def test_text_theme_has_reading_height():
    t = theme.build_theme("ochre", registered={})
    assert t.text_theme.body_medium is not None
    assert t.text_theme.body_medium.height >= 1.4


def test_text_theme_styles_all_have_color():
    """回归：自定义 text_theme 样式缺 color 会被渲染成白色（输入框文字隐形）。"""
    names = ("headline_small", "title_large", "title_medium", "title_small",
             "body_large", "body_medium", "body_small",
             "label_large", "label_medium", "label_small")
    for dark in (False, True):
        t = theme.build_theme("ochre", dark=dark, registered={})
        for name in names:
            st = getattr(t.text_theme, name)
            assert st is not None, f"{name} 未定义"
            assert st.color, f"{name} 缺少 color（会渲染成白色）"


def test_chat_markdown_style_sheet_all_text_colors():
    import dataclasses
    ss = theme.chat_markdown_style_sheet()
    for f in dataclasses.fields(ss):
        val = getattr(ss, f.name)
        if isinstance(val, ft.TextStyle):
            assert val.color, f"Markdown 样式 {f.name} 缺少 color"


# ==================== UI 工厂 ====================

def test_section_header_returns_row_with_trailing():
    header = theme.section_header(ft.Icons.MENU_BOOK, "章节目录",
                                  trailing=ft.IconButton(icon=ft.Icons.ADD))
    assert isinstance(header, ft.Row)
    kinds = [type(c) for c in header.controls]
    assert ft.Icon in kinds and ft.Text in kinds


def test_factories_return_controls():
    assert isinstance(theme.tile_card(ft.Text("x")), ft.Container)
    assert isinstance(theme.status_dot("finalized"), ft.Container)
    assert isinstance(theme.severity_dot("error"), ft.Container)
    assert isinstance(theme.chip("细纲"), ft.Container)
    assert isinstance(theme.metric_text("1.2k"), ft.Text)
    assert isinstance(theme.inline_loader(), ft.ProgressRing)
    assert isinstance(theme.inline_error("失败"), ft.Row)
    assert isinstance(theme.empty_state(ft.Icons.INBOX, "空", "提示"),
                      ft.Container)
    assert isinstance(theme.paper_card(ft.Text("x")), ft.Container)
    assert isinstance(theme.divider(), ft.Divider)
    assert isinstance(theme.paper_shadow(), ft.BoxShadow)


def test_icon_size_scale_is_ordered():
    assert theme.ICON_SMALL < theme.ICON_INLINE < theme.ICON_ACTION


def test_empty_state_with_action_and_hint():
    action = ft.FilledButton("登记")
    state = theme.empty_state(ft.Icons.PERSON_ADD_ALT, "暂无角色", "先登记",
                              action)
    texts = [c for c in state.content.controls if isinstance(c, ft.Text)]
    assert len(texts) == 2
