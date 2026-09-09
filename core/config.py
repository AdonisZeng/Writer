"""全局配置加载/保存（config.json）。

极简配置设计：模型载入与服务运维交给用户在 LM Studio 界面完成，
代码只管理「单次生成控制」与少量应用状态（当前项目 / 主题）。
"""
import json
import os

from core import paths

DEFAULTS: dict = {
    # ---- API 连接（统一 OpenAI 协议：LM Studio /v1 与云端 API 通吃）----
    "api_base": "http://127.0.0.1:1234/v1",
    "api_key": "lm-studio",
    "model": "local-model",
    "context_limit": 32768,   # 建议填 LM Studio 加载模型时实际配置的 Context Length
    "reasoning_mode": "auto",  # 推理参数模式：auto/levels/toggle/none
    # ---- Ghost 行内补全（P2 实验性，先占位）----
    "ghost_enabled": False,
    "ghost_debounce_ms": 1500,
    "ghost_max_tokens": 25,
    "ghost_model": "",
    # ---- 应用状态 ----
    "project": "",            # 当前打开的小说项目名（novels/ 下的目录名）
    "theme_mode": "light",    # light / dark / system
    # ---- 一致性阈值（P1）----
    "plot_dormant_threshold": 25,   # 伏笔休眠告警章差阈值（方案 5.4.5）
    "chapter_word_limit": 8000,     # 单章字数硬上限（方案 6.3 风险表：建议 ≤5000）
    # ---- RAG 知识库（P2，sqlite-vec）----
    "rag_enabled": False,           # 生成时检索注入（需在嵌入服务可用后开启）
    "rag_model": "",                # 嵌入模型（LM Studio 加载的 embedding 模型名）
    "rag_top_k": 4,                 # 检索注入条数
    "rag_chunk_chars": 500,         # 索引分块大小（按段落边界）
    # ---- 云端费用估算（元/1k tokens；本地恒 0）----
    "cost_per_1k_input": 0.0,
    "cost_per_1k_output": 0.0,
    # ---- 排版自定义（P3）----
    "editor_font_size": 15,     # 正文字号
    "editor_width": 780,        # 正文最大行宽（0 = 不限宽）
    # ---- 界面布局（拖拽分隔条记忆）----
    "ui_left_width": 270,       # 写作界面左栏宽度
    "ui_right_width": 340,      # 写作界面右栏宽度
    "ui_design_nav_width": 196, # 设计界面左栏导航宽度
    "ui_design_ai_width": 400,  # 设计界面 AI 协作台宽度
}

CONFIG: dict = dict(DEFAULTS)


def load_config() -> dict:
    """读取 config.json；缺失/损坏时回退默认值（首次启动由界面保存后生成）。"""
    global CONFIG
    cfg = dict(DEFAULTS)
    try:
        with open(paths.CONFIG_FILE, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
        if isinstance(user_cfg, dict):
            cfg.update(user_cfg)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    CONFIG = cfg
    return CONFIG


def save_config() -> None:
    """原子写入 config.json。"""
    tmp = paths.CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(CONFIG, f, ensure_ascii=False, indent=2)
    os.replace(tmp, paths.CONFIG_FILE)


def get(key: str, default=None):
    return CONFIG.get(key, DEFAULTS.get(key, default))


def set_key(key: str, value) -> None:
    CONFIG[key] = value
