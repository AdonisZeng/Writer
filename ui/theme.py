"""设计令牌与主题工厂（唯一视觉事实来源 / Single Source of Truth）。

设计方向：**暖中性编辑风（Editorial Warm Neutral）**——以「纸与墨」为隐喻，
把写作工具做成一沓安静的稿纸：米白纸面、墨黑文字、表面色差与留白表达层级，
单一低饱和强调色只在可交互与状态处出现，杜绝 Material 默认 indigo 的「通用 AI 脸」。

三层表面模型：
- 画布（canvas，最亮纸面）：承载正文与设定；
- 面板（panel，略暖工具栏底色）：承载章节树 / 名册 / 伏笔 / 诊断；
- 覆盖层（overlay）：设置页，纸面卡片浮于半透明遮罩。

本模块为纯表现层，不含任何业务逻辑，可独立单测。
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Optional

import flet as ft

# ====================================================================
# 字体
# ====================================================================

FONT_UI = "Noto Sans SC"          # UI 无衬线（界面 chrome）
FONT_SERIF = "Noto Serif SC"      # 正文衬线（阅读区）
FONT_MONO = "Noto Sans Mono"      # 数值等宽（状态栏 / 统计）

# 字体缺失或回退时的系统字体链（跨平台近似）
FONT_UI_FALLBACK = ["Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
                    "Hiragino Sans GB", "Noto Sans CJK SC", "sans-serif"]
FONT_SERIF_FALLBACK = ["SimSun", "Songti SC", "STSong", "Noto Serif CJK SC",
                       "serif"]
FONT_MONO_FALLBACK = ["Cascadia Mono", "Consolas", "SF Mono", "Menlo",
                      "DejaVu Sans Mono", "monospace"]

# 内置字体文件名（由 tools/fetch_fonts.py 下载到 assets/fonts/）
FONT_FILES = {
    FONT_UI: "NotoSansSC-VF.ttf",
    FONT_SERIF: "NotoSerifSC-VF.ttf",
    FONT_MONO: "NotoSansMono-VF.ttf",
}


def fonts_dir() -> str:
    from core import paths
    return os.path.join(paths.ASSETS_DIR, "fonts")


def fonts_map(fonts_dir_path: Optional[str] = None) -> dict[str, str]:
    """返回「仅包含实际存在文件」的 page.fonts 注册表。

    字体缺失时返回空表——应用照常启动并回落到系统字体，绝不因缺字体而失败。
    """
    base = fonts_dir_path or fonts_dir()
    out: dict[str, str] = {}
    for family, filename in FONT_FILES.items():
        if os.path.isfile(os.path.join(base, filename)):
            out[family] = f"fonts/{filename}"
    return out


def register_fonts(page: ft.Page, fonts_dir_path: Optional[str] = None) -> dict:
    """把存在的内置字体注册到 page.fonts，返回实际注册表。"""
    mapping = fonts_map(fonts_dir_path)
    if mapping:
        page.fonts = mapping
    return mapping


def _family(registered: dict, family: str) -> Optional[str]:
    """已注册才返回家族名，否则 None（交回系统默认字体）。"""
    return family if family in registered else None


# ====================================================================
# 色板（暖中性）
# ====================================================================

# 设计准则给定 / 派生的基础色（浅色模式）
PAPER = "#FBF9F5"          # 画布纸面
PANEL = "#F7F4EE"          # 侧栏面板
SURFACE = "#FFFFFF"        # 卡片
SURFACE_ALT = "#F4F1EA"    # 次级表面
SURFACE_HIGH = "#EFEBE2"
SURFACE_HIGHEST = "#EAE5DA"
BORDER = "#E7E1D6"
SHADOW = "#B9AE9C"         # 暖色阴影（与纸面同调，避免生硬纯黑投影）
INK = "#2B2724"            # 墨黑正文
INK_MUTED = "#6B6459"      # 暖灰二级
INK_FAINT = "#9A9288"      # 极淡说明

# 深色模式（暖中性暗色，非纯黑）
# 层级约定与浅色一致：画布最深 → 面板 → 卡片最亮（抬起）
PAPER_DARK = "#211E1B"       # 画布
PANEL_DARK = "#262320"       # 侧栏面板
SURFACE_DARK = "#2A2723"     # 次级表面
SURFACE_DARK_HIGH = "#322E29"
SURFACE_DARK_HIGHEST = "#38332E"
CARD_DARK = "#2E2A26"        # 卡片（最亮）
BORDER_DARK = "#3A3631"
INK_DARK = "#EDE8E0"
INK_DARK_MUTED = "#A9A199"
INK_DARK_FAINT = "#7C746B"

# 语义色（去饱和柔软色阶，浅/深两套；供状态、诊断、Diff 等使用）
SEMANTIC: dict[str, dict[str, str]] = {
    "success": {"light": "#5B7A5B", "dark": "#8FB08F"},
    "warning": {"light": "#B98A3C", "dark": "#D6B072"},
    "danger": {"light": "#B4544B", "dark": "#D98880"},
    "info": {"light": "#5A7186", "dark": "#93AEC4"},
    "neutral": {"light": "#9A9288", "dark": "#7C746B"},
}

# 强调色种子：暖赭（默认）+ 同调低饱和备选；每项 = (浅色, 深色)
SEEDS: dict[str, tuple[str, str]] = {
    "ochre": ("#8A5A3B", "#C89272"),     # 暖赭（默认）
    "celadon": ("#3A5A5C", "#7FA6A8"),   # 黛青
    "umber": ("#5E4A3A", "#B79B84"),     # 玄褐
    "pine": ("#4E6B4E", "#8FB08F"),      # 松绿
    "plum": ("#6B4A5C", "#B490A4"),      # 绛紫
}
DEFAULT_SEED = "ochre"

# 强调色中文名（设置页下拉）
SEED_LABELS: dict[str, str] = {
    "ochre": "暖赭", "celadon": "黛青", "umber": "玄褐",
    "pine": "松绿", "plum": "绛紫",
}


def seed_options() -> list[tuple[str, str]]:
    """设置页可选强调色 [(key, 中文名)]，默认项标注「默认」。"""
    out = []
    for key in SEEDS:
        label = SEED_LABELS.get(key, key)
        out.append((key, f"{label}（默认）" if key == DEFAULT_SEED else label))
    return out


def normalize_seed(seed) -> str:
    """把历史/非法种子收拢到当前合法集合（兼容旧的 indigo 等取值）。"""
    return seed if seed in SEEDS else DEFAULT_SEED

# ====================================================================
# 刻度（字号 / 间距 / 圆角）
# ====================================================================

# 字号梯度（统一原 10/11/12/13/15/16 的散落取值）
SIZE_XL = 20        # 品牌 / 展示
SIZE_LG = 15        # 面板 / 页面标题
SIZE_MD = 13        # 区块标题 & 正文
SIZE_SM = 12        # 次要文本 / 标签
SIZE_XS = 11        # 说明 / 空态提示
SIZE_XXS = 10       # 极微标签

SIZE_BRAND = 17
SIZE_SECTION = SIZE_MD

# 间距刻度
SPACE_XXS = 2
SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 24

# 圆角刻度
RADIUS_XS = 6
RADIUS_SM = 8
RADIUS_MD = 12
RADIUS_LG = 16
RADIUS_PILL = 999

# 字重
W_REGULAR = ft.FontWeight.W_400
W_MEDIUM = ft.FontWeight.W_500
W_SEMIBOLD = ft.FontWeight.W_600
W_BOLD = ft.FontWeight.W_700

# 图标尺寸（三档：小 14 / 行内 16 / 主操作 18）
ICON_SMALL = 14
ICON_INLINE = 16
ICON_ACTION = 18

# 动效（克制：只做状态微过渡，不做花哨位移）
ANIM_FAST = ft.Animation(160, ft.AnimationCurve.EASE_OUT)
ANIM_BASE = ft.Animation(240, ft.AnimationCurve.EASE_OUT)

# 正文阅读行高
LINE_HEIGHT_READ = 1.75


# ====================================================================
# 颜色工具
# ====================================================================


def _to_rgb(color: str) -> tuple[int, int, int]:
    h = color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) == 8:      # AARRGGBB → 取后 6 位
        h = h[2:]
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _to_hex(rgb: Iterable[float]) -> str:
    r, g, b = (max(0, min(255, round(v))) for v in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def mix(color_a: str, color_b: str, t: float) -> str:
    """在 a、b 之间线性插值（t=0 → a，t=1 → b），返回 #RRGGBB。

    用于从单一强调色派生其容器色调（primary_container 等），
    避免为每个种子手写两套取色。
    """
    t = max(0.0, min(1.0, t))
    a, b = _to_rgb(color_a), _to_rgb(color_b)
    return _to_hex(a[i] + (b[i] - a[i]) * t for i in range(3))


def seed_pair(seed: str) -> tuple[str, str]:
    return SEEDS.get(seed or DEFAULT_SEED, SEEDS[DEFAULT_SEED])


def semantic_color(name: str, dark: bool = False) -> str:
    entry = SEMANTIC.get(name, SEMANTIC["neutral"])
    return entry["dark"] if dark else entry["light"]


# ====================================================================
# 状态 / 严重度映射（合并原 chapter_tree 与 editor 的重复色表）
# ====================================================================

STATUS_LABELS: dict[str, str] = {
    "outlined": "细纲", "drafted": "草稿",
    "revised": "精修", "finalized": "定稿",
}
_STATUS_SEMANTIC = {
    "outlined": "neutral", "drafted": "info",
    "revised": "warning", "finalized": "success",
}


def status_color(status: str, dark: bool = False) -> str:
    return semantic_color(_STATUS_SEMANTIC.get(status, "neutral"), dark)


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


SEVERITY_ORDER = ("error", "warning", "info")
_SEVERITY_SEMANTIC = {"error": "danger", "warning": "warning", "info": "info"}


def severity_color(severity: str, dark: bool = False) -> str:
    return semantic_color(_SEVERITY_SEMANTIC.get(severity, "info"), dark)


# ====================================================================
# ColorScheme / Theme
# ====================================================================

# 主题感知的语义角色（跟随浅/深自动切换，widget 代码统一引用这些名字）
CANVAS = ft.Colors.SURFACE                    # 画布纸面
PANEL_BG = ft.Colors.SURFACE_CONTAINER_LOW    # 侧栏面板
CARD_BG = ft.Colors.SURFACE_CONTAINER_LOWEST  # 卡片（最亮）
BORDER_COLOR = ft.Colors.OUTLINE_VARIANT      # 分割线 / 细边框
TEXT = ft.Colors.ON_SURFACE                   # 主文字（墨）
TEXT_MUTED = ft.Colors.ON_SURFACE_VARIANT     # 二级文字（暖灰）
TEXT_FAINT = ft.Colors.OUTLINE                # 三级说明（极淡）
ACCENT = ft.Colors.PRIMARY                    # 唯一强调色
ACCENT_SOFT = ft.Colors.PRIMARY_CONTAINER     # 强调色浅底
ON_ACCENT_SOFT = ft.Colors.ON_PRIMARY_CONTAINER


def build_color_scheme(seed: str = DEFAULT_SEED, dark: bool = False) -> ft.ColorScheme:
    """由单一强调色种子派生完整 ColorScheme（暖中性）。

    surface 系列与文字色为显式暖中性取色，保证观感确定；
    强调色容器由 mix() 派生，随种子自动生成成对色调。
    """
    light_seed, dark_seed = seed_pair(seed)
    primary = dark_seed if dark else light_seed
    base = PAPER_DARK if dark else PAPER
    on_accent = "#241209"

    if not dark:
        return ft.ColorScheme(
            primary=primary,
            on_primary="#FFFFFF",
            primary_container=mix(primary, "#FFFFFF", 0.86),
            on_primary_container=mix(primary, "#000000", 0.42),
            secondary=mix(primary, PANEL, 0.45),
            on_secondary="#FFFFFF",
            secondary_container=mix(primary, "#FFFFFF", 0.90),
            on_secondary_container=mix(primary, "#000000", 0.40),
            tertiary=mix(primary, INK_MUTED, 0.40),
            on_tertiary="#FFFFFF",
            tertiary_container=mix(primary, "#FFFFFF", 0.92),
            on_tertiary_container=mix(primary, "#000000", 0.42),
            error=SEMANTIC["danger"]["light"],
            on_error="#FFFFFF",
            error_container=mix(SEMANTIC["danger"]["light"], "#FFFFFF", 0.88),
            on_error_container=mix(SEMANTIC["danger"]["light"], "#000000", 0.42),
            surface=PAPER,
            on_surface=INK,
            on_surface_variant=INK_MUTED,
            outline=INK_FAINT,
            outline_variant=BORDER,
            shadow=SHADOW,
            scrim="#2B2724",
            inverse_surface=INK,
            on_inverse_surface=PAPER,
            inverse_primary=mix(primary, "#FFFFFF", 0.35),
            surface_tint=primary,
            surface_bright=SURFACE,
            surface_container_lowest=SURFACE,
            surface_container_low=PANEL,
            surface_container=SURFACE_ALT,
            surface_container_high=SURFACE_HIGH,
            surface_container_highest=SURFACE_HIGHEST,
        )
    return ft.ColorScheme(
        primary=primary,
        on_primary=on_accent,
        primary_container=mix(primary, base, 0.72),
        on_primary_container=mix(primary, "#FFFFFF", 0.62),
        secondary=mix(primary, base, 0.45),
        on_secondary="#FFFFFF",
        secondary_container=mix(primary, base, 0.76),
        on_secondary_container=mix(primary, "#FFFFFF", 0.55),
        tertiary=mix(primary, base, 0.55),
        on_tertiary="#FFFFFF",
        tertiary_container=mix(primary, base, 0.80),
        on_tertiary_container=mix(primary, "#FFFFFF", 0.58),
        error=SEMANTIC["danger"]["dark"],
        on_error="#241209",
        error_container=mix(SEMANTIC["danger"]["dark"], base, 0.72),
        on_error_container=mix(SEMANTIC["danger"]["dark"], "#FFFFFF", 0.60),
        surface=PAPER_DARK,
        on_surface=INK_DARK,
        on_surface_variant=INK_DARK_MUTED,
        outline=INK_DARK_FAINT,
        outline_variant=BORDER_DARK,
        shadow="#000000",
        scrim="#000000",
        inverse_surface=INK_DARK,
        on_inverse_surface=PAPER_DARK,
        inverse_primary=mix(primary, base, 0.35),
        surface_tint=primary,
        surface_bright=SURFACE_DARK_HIGH,
        surface_container_lowest=CARD_DARK,
        surface_container_low=PANEL_DARK,
        surface_container=SURFACE_DARK,
        surface_container_high=SURFACE_DARK_HIGH,
        surface_container_highest=SURFACE_DARK_HIGHEST,
    )


def _text_theme(ui_family: Optional[str], serif_family: Optional[str],
                dark: bool = False) -> ft.TextTheme:
    """文本层级：标题走衬线建立编辑排版气质，正文走 UI 无衬线。"""
    def style(size: int, weight, height: float, family=None) -> ft.TextStyle:
        return ft.TextStyle(size=size, weight=weight, height=height,
                            font_family=family,
                            font_family_fallback=FONT_UI_FALLBACK)

    return ft.TextTheme(
        headline_small=style(SIZE_XL, W_SEMIBOLD, 1.3, serif_family),
        title_large=style(SIZE_LG + 2, W_SEMIBOLD, 1.35, serif_family),
        title_medium=style(SIZE_LG, W_SEMIBOLD, 1.35, ui_family),
        body_medium=style(SIZE_MD, W_REGULAR, 1.5, ui_family),
        body_small=style(SIZE_SM, W_REGULAR, 1.5, ui_family),
        label_medium=style(SIZE_SM, W_MEDIUM, 1.4, ui_family),
        label_small=style(SIZE_XS, W_MEDIUM, 1.4, ui_family),
    )


def build_theme(seed: str = DEFAULT_SEED, dark: bool = False,
                registered: Optional[dict] = None) -> ft.Theme:
    """构建 ft.Theme（浅色或深色）。registered 为已注册字体表。"""
    registered = fonts_map() if registered is None else registered
    ui_family = _family(registered, FONT_UI)
    serif_family = _family(registered, FONT_SERIF)
    cs = build_color_scheme(seed, dark)

    kwargs: dict[str, Any] = {
        "color_scheme": cs,
        "text_theme": _text_theme(ui_family, serif_family, dark),
        "use_material3": True,
        "divider_color": cs.outline_variant,
        "hint_color": cs.outline,
        "canvas_color": cs.surface,
        "scaffold_bgcolor": cs.surface,
        "card_bgcolor": cs.surface_container_lowest,
        "splash_color": cs.primary,
        "hover_color": cs.primary,
        "focus_color": cs.primary,
        "highlight_color": cs.primary,
        "unselected_control_color": cs.on_surface_variant,
        "disabled_color": cs.on_surface,
        "secondary_header_color": cs.on_surface_variant,
    }
    if ui_family:
        kwargs["font_family"] = ui_family
    theme = ft.Theme(**kwargs)
    return theme


def mono_style(size: int = SIZE_SM, color: Optional[str] = None,
               weight=None) -> ft.TextStyle:
    """数值等宽文本样式（消除实时数字宽度抖动）。"""
    return ft.TextStyle(
        size=size, color=color, weight=weight or W_REGULAR,
        font_family=FONT_MONO, font_family_fallback=FONT_MONO_FALLBACK,
    )


# ====================================================================
# UI 工厂（通用视觉组件）
# ====================================================================


def section_header(icon: str, label: str, *, accent: bool = False,
                   trailing: Optional[ft.Control] = None,
                   tooltip: Optional[str] = None) -> ft.Control:
    """区块标题：小图标 + 标题文字（+ 可选右侧控件）。"""
    title = ft.Text(label, size=SIZE_SECTION, weight=W_SEMIBOLD,
                    color=ft.Colors.PRIMARY if accent else TEXT,
                    tooltip=tooltip)
    controls: list[ft.Control] = [
        ft.Icon(icon, size=ICON_INLINE,
                color=ft.Colors.PRIMARY if accent else TEXT_MUTED),
        title,
    ]
    if trailing is not None:
        controls.append(ft.Container(expand=True))
        controls.append(trailing)
    return ft.Row(controls, spacing=SPACE_XS,
                  vertical_alignment=ft.CrossAxisAlignment.CENTER)


def tile_card(content: ft.Control, *, padding: int = SPACE_SM,
              radius: int = RADIUS_SM, bgcolor=None,
              on_click=None) -> ft.Container:
    """通用小卡片：靠表面色差而非边框表达层级。"""
    return ft.Container(
        content=content,
        padding=padding,
        border_radius=radius,
        bgcolor=bgcolor or ft.Colors.with_opacity(0.035, ft.Colors.ON_SURFACE),
        on_click=on_click,
        ink=on_click is not None,
    )


def status_dot(status: str, *, size: int = 8) -> ft.Container:
    """章节/条目状态圆点（替代 emoji 严重度标记）。"""
    return ft.Container(width=size, height=size, border_radius=size / 2,
                        bgcolor=status_color(status))


def severity_dot(severity: str, *, size: int = 8) -> ft.Container:
    return ft.Container(width=size, height=size, border_radius=size / 2,
                        bgcolor=severity_color(severity))


def chip(label: str, *, on_click=None, active: bool = False,
         tooltip: Optional[str] = None) -> ft.Container:
    """轻量标签/胶囊（章节徽标、快捷提示、角色名等）。"""
    return ft.Container(
        content=ft.Text(label, size=SIZE_XS,
                        color=ON_ACCENT_SOFT if active else TEXT_MUTED),
        padding=ft.Padding(SPACE_SM, 3, SPACE_SM, 3),
        border_radius=RADIUS_PILL,
        bgcolor=(ACCENT_SOFT if active
                 else ft.Colors.with_opacity(0.05, ft.Colors.ON_SURFACE)),
        tooltip=tooltip,
        ink=on_click is not None,
        on_click=on_click,
    )


def empty_state(icon: str, title: str, hint: str = "",
                action: Optional[ft.Control] = None,
                *, compact: bool = True) -> ft.Control:
    """组合式空状态：图标 + 一句引导（+ 可选主操作）。

    「空屏是一次邀请」，不是一句灰字。
    """
    controls: list[ft.Control] = [
        ft.Icon(icon, size=26 if compact else 34, color=TEXT_FAINT),
        ft.Text(title, size=SIZE_SM, color=TEXT_MUTED),
    ]
    if hint:
        controls.append(ft.Text(hint, size=SIZE_XS, color=TEXT_FAINT,
                                text_align=ft.TextAlign.CENTER))
    if action is not None:
        controls.append(ft.Container(height=SPACE_XXS))
        controls.append(action)
    return ft.Container(
        content=ft.Column(controls, spacing=SPACE_XS, tight=True,
                          horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        alignment=ft.Alignment(0, 0),
        padding=ft.Padding(SPACE_SM, SPACE_LG, SPACE_SM, SPACE_LG),
    )


def metric_text(value: str, *, size: int = SIZE_SM,
                color: str = TEXT_MUTED, tooltip: Optional[str] = None) -> ft.Text:
    """实时数值文本（等宽，避免宽度抖动）。"""
    return ft.Text(value, style=mono_style(size, color), tooltip=tooltip)


def inline_loader(size: int = 14, tooltip: Optional[str] = None) -> ft.Control:
    """内联加载指示（替代无反馈的等待）。"""
    return ft.ProgressRing(width=size, height=size, stroke_width=2,
                           color=ft.Colors.PRIMARY, tooltip=tooltip)


def inline_error(message: str) -> ft.Control:
    """内联错误条（错误就近呈现，不只写进日志）。"""
    return ft.Row([
        ft.Icon(ft.Icons.ERROR_OUTLINE, size=ICON_INLINE,
                color=semantic_color("danger")),
        ft.Text(message, size=SIZE_XS, color=semantic_color("danger"),
                expand=True, selectable=True),
    ], spacing=SPACE_XS, vertical_alignment=ft.CrossAxisAlignment.START)


def paper_shadow(blur: int = 24, dy: int = 4,
                 opacity: float = 0.10) -> ft.BoxShadow:
    """暖色柔和投影（与纸面同色相，暗示单一光源，避免生硬纯黑）。"""
    return ft.BoxShadow(
        blur_radius=blur, spread_radius=0,
        color=ft.Colors.with_opacity(opacity, SHADOW),
        offset=ft.Offset(0, dy),
    )


def paper_card(content: ft.Control, *, width: Optional[int] = None,
               padding: int = SPACE_XL, radius: int = RADIUS_LG) -> ft.Container:
    """抬起为「一张稿纸」的卡片：柔和暖色阴影，无硬边框。"""
    return ft.Container(
        content=content,
        width=width,
        padding=padding,
        border_radius=radius,
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
        shadow=paper_shadow(blur=28, dy=6, opacity=0.14),
    )


def divider(height: int = 1) -> ft.Divider:
    return ft.Divider(height=height, thickness=1, color=BORDER_COLOR)
