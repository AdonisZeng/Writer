"""ai_service.py 离线测试（零网络依赖）：防御工具 / 思考映射 / 用量估算 / 取消异常。"""
import pytest

from core import ai_service as ai


def test_strip_thinking_tags():
    raw = "<think>推理过程…</think>\n正文内容。"
    assert ai.strip_thinking_tags(raw) == "正文内容。"
    assert ai.strip_thinking_tags("无思考") == "无思考"
    assert "<think>" in raw  # 原串不被修改


def test_strip_thinking_tags_multiline():
    raw = "<think>\n多行\n思考\n</think>\n答案"
    assert ai.strip_thinking_tags(raw) == "答案"


def test_parse_llm_json():
    assert ai.parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert ai.parse_llm_json('前置说明 {"a": {"b": 2}} 后置说明') == {"a": {"b": 2}}
    assert ai.parse_llm_json("完全不是 JSON") == {}
    assert ai.parse_llm_json("坏的", default=[]) == []


def test_reasoning_effort_map():
    # 方案 5.2.4：正文关思考保吞吐，审查/抽取开思考保逻辑
    assert ai.REASONING_EFFORT["draft"] == "none"
    assert ai.REASONING_EFFORT["ghost"] == "none"
    assert ai.REASONING_EFFORT["review"] == "medium"
    assert ai.REASONING_EFFORT["extract"] == "xhigh"


def test_reasoning_extra_toggle_model(novel_root, monkeypatch):
    """Qwen3/3.5 一类只支持 on/off 的模型：不得再发 low~xhigh 分级值。"""
    from core import config
    monkeypatch.setitem(config.CONFIG, "reasoning_mode", "auto")
    ai.reset_reasoning_cache()
    qwen = "qwen/qwen3.5-9b"
    assert ai.reasoning_mode(qwen) == "toggle"
    # 开思考：不干预（发分级值会被 LM Studio 告警并强制回退 on）
    assert ai.reasoning_extra("review", qwen) == {"enable_thinking": True}
    assert ai.reasoning_extra("extract", qwen) == {"enable_thinking": True}
    # 关思考：reasoning_effort='none' 才是 llama.cpp 唯一认的写法
    assert ai.reasoning_extra("draft", qwen) == \
        {"reasoning_effort": "none", "enable_thinking": False}
    assert ai.reasoning_extra("ghost", qwen) == \
        {"reasoning_effort": "none", "enable_thinking": False}
    # 形态各异的模型名都要命中
    for name in ("Qwen3-8B-Q4_K_M", "qwen3.5-9b-mlx", "qwen3-32b"):
        assert ai.reasoning_mode(name) == "toggle"
    # 非 Qwen 系不受影响
    assert ai.reasoning_mode("qwen2.5-7b-instruct") == "levels"


def test_reasoning_extra_levels_model(novel_root, monkeypatch):
    """支持分级的模型：发标准档位，xhigh 收拢为 high。"""
    from core import config
    monkeypatch.setitem(config.CONFIG, "reasoning_mode", "auto")
    ai.reset_reasoning_cache()
    assert ai.reasoning_mode("gpt-oss-20b") == "levels"
    assert ai.reasoning_extra("review", "gpt-oss-20b") == \
        {"reasoning_effort": "medium"}
    assert ai.reasoning_extra("extract", "gpt-oss-20b") == \
        {"reasoning_effort": "high"}
    assert ai.reasoning_extra("draft", "gpt-oss-20b") == \
        {"reasoning_effort": "none", "enable_thinking": False}


def test_describe_reasoning_matches_sent_params(novel_root, monkeypatch):
    """生成日志里打印的说明必须与「真正发出的参数」同源一致。"""
    from core import config
    monkeypatch.setitem(config.CONFIG, "reasoning_mode", "auto")
    monkeypatch.setitem(config.CONFIG, "api_base", "http://127.0.0.1:1234/v1")
    ai.reset_reasoning_cache()
    qwen = "qwen/qwen3.5-9b"
    # toggle 模型：开思考=不干预，关思考=reasoning_effort none
    assert ai.describe_reasoning("review", qwen) == \
        "推理参数：不干预（auto→toggle，沿用模型默认档位）"
    assert ai.describe_reasoning("draft", qwen) == \
        "推理参数：关闭思考（reasoning_effort=none，auto→toggle）"
    # 分级模型：说明里出现收拢后的真实档位（xhigh → high）
    assert ai.describe_reasoning("extract", "gpt-oss-20b") == \
        "推理参数：reasoning_effort=high（auto→levels）"
    # 显式 none 模式：什么都不发
    monkeypatch.setitem(config.CONFIG, "reasoning_mode", "none")
    assert ai.describe_reasoning("review", qwen) == "推理参数：不发送（none）"


def test_reasoning_mode_override_and_error_probe(novel_root, monkeypatch):
    """配置显式指定优先；auto 下会记住后端合法值并自动降级。"""
    from core import config
    ai.reset_reasoning_cache()
    monkeypatch.setitem(config.CONFIG, "reasoning_mode", "none")
    assert ai.reasoning_extra("review", "qwen/qwen3.5-9b") == {}
    assert ai.note_reasoning_error("qwen/qwen3.5-9b",
                                   Exception("reasoning not supported")) is False

    monkeypatch.setitem(config.CONFIG, "reasoning_mode", "auto")
    model = "unknown-model-7b"
    assert ai.reasoning_mode(model) == "levels"
    assert ai.note_reasoning_error(model, Exception("连接超时")) is False
    # 后端直接给出合法取值：记住它，别再重试同样的错
    assert ai.note_reasoning_error(
        model, Exception("Invalid 'reasoning_effort' value: 'xhigh'. "
                         "Supported values: low, medium, high.")) is True
    assert ai.reasoning_extra("extract", model) == \
        {"reasoning_effort": "high"}
    # 已按合法值调整过仍被拒：说明解析不可靠，改为降级模式
    assert ai.note_reasoning_error(
        model, Exception("Invalid 'reasoning_effort' value: 'high'. "
                         "Supported values: low, medium, high.")) is True
    assert ai.reasoning_mode(model) == "toggle"
    assert ai.reasoning_extra("review", model) == {"enable_thinking": True}
    assert ai.note_reasoning_error(model, Exception("reasoning 参数不支持")) is True
    assert ai.reasoning_mode(model) == "none"
    assert ai.reasoning_extra("review", model) == {}
    ai.reset_reasoning_cache()


def test_estimate_usage():
    p, c = ai._estimate_usage([{"role": "user", "content": "字" * 300}],
                              "文" * 150)
    assert p == 200 and c == 100


def test_generation_cancelled_carries_partial():
    exc = ai.GenerationCancelled("半成品文本")
    assert exc.partial == "半成品文本"


def test_provider_tag_local(novel_root, monkeypatch):
    from core import config
    monkeypatch.setitem(config.CONFIG, "api_base",
                        "http://127.0.0.1:1234/v1")
    assert ai._provider_tag() == "local"
    monkeypatch.setitem(config.CONFIG, "api_base", "https://api.deepseek.com/v1")
    assert "deepseek" in ai._provider_tag()


def test_local_base_client_bypasses_system_proxy(novel_root, monkeypatch):
    """本地后端不走系统代理（localhost 请求被塞进 Clash 会凭空多一层中间环节）。"""
    from core import config
    assert ai._is_local_base("http://127.0.0.1:1234/v1")
    assert ai._is_local_base("http://localhost:1234/v1")
    assert not ai._is_local_base("https://api.deepseek.com/v1")

    monkeypatch.setitem(config.CONFIG, "api_base", "http://127.0.0.1:1234/v1")
    ai.rebuild_client()
    local = ai._get_client()
    assert getattr(local._client, "_trust_env", True) is False

    # 云端 base 保持默认：有人正是靠系统代理才能连上 OpenAI 等
    monkeypatch.setitem(config.CONFIG, "api_base", "https://api.deepseek.com/v1")
    ai.rebuild_client()
    cloud = ai._get_client()
    assert getattr(cloud._client, "_trust_env", False) is True
    assert local is not cloud


def test_client_rebuild_on_base_change(novel_root, monkeypatch):
    from core import config
    monkeypatch.setitem(config.CONFIG, "api_base", "http://a:1/v1")
    c1 = ai._get_client()
    monkeypatch.setitem(config.CONFIG, "api_base", "http://b:2/v1")
    c2 = ai._get_client()
    assert c1 is not c2
    assert c2 is ai._get_client()  # 同 base 不重建


async def test_stream_cancelled_before_any_token(novel_root, monkeypatch):
    """连不上后端时不静默吞错：重试耗尽后必须 raise last_err。

    这里注入「必定抛连接错误」的假客户端，而不是依赖某个没人监听的端口：
    openai SDK 的 httpx 会读取系统代理（Windows 注册表，如 127.0.0.1:7897），
    死端口请求会先交给代理、等它超时回 502，用例既慢又取决于代理行为；
    而沿用默认的 127.0.0.1:1234，本机开着 LM Studio 时请求会直接成功。
    """
    import asyncio

    class _DeadCompletions:
        @staticmethod
        async def create(**kwargs):
            raise ConnectionError("connection refused（模拟无服务器）")

    class _DeadChat:
        completions = _DeadCompletions

    class _DeadClient:
        chat = _DeadChat

    monkeypatch.setattr(ai, "_get_client", lambda: _DeadClient())
    ev = asyncio.Event()
    with pytest.raises(Exception):
        await ai.call_llm_stream("m", [{"role": "user", "content": "x"}],
                                 lambda s: None, cancel_event=ev,
                                 max_retries=0)
