"""config.py 测试：缺省回退、读写 roundtrip。"""
from core import config


def test_load_defaults_when_missing(novel_root):
    loaded = config.load_config()
    assert loaded["api_base"] == "http://127.0.0.1:1234/v1"
    assert loaded["context_limit"] == 32768
    assert loaded["ghost_enabled"] is False  # 方案 5.1：Ghost 默认关闭


def test_save_and_reload_roundtrip(novel_root):
    config.load_config()
    config.set_key("api_base", "https://api.example.com/v1")
    config.set_key("theme_mode", "dark")
    config.save_config()

    # 模拟重启：重新加载
    loaded = config.load_config()
    assert loaded["api_base"] == "https://api.example.com/v1"
    assert loaded["theme_mode"] == "dark"
    assert loaded["context_limit"] == 32768  # 未改动项保留默认


def test_corrupted_config_falls_back(novel_root):
    from core import paths
    with open(paths.CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write("{ 这不是合法 JSON ")
    loaded = config.load_config()
    assert loaded["api_base"] == "http://127.0.0.1:1234/v1"
