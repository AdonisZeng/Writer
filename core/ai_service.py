"""极简 AI 服务层：流式生成 + 模型发现 + 任务级参数 + 用量记录（方案 5.2）。

不设 Provider 继承体系：LM Studio（本地）与云端厂商统一走 OpenAI 协议，
切换只是 config.json 里 api_base/api_key/model 三个字段。
"""
import asyncio
import json
import re
import time
from typing import Callable, NamedTuple, Optional

import httpx
from openai import AsyncOpenAI

from core import config, db

REASONING_EFFORT = {             # 思考粒度按任务路由（方案 5.2.4）
    "draft": "none", "refine": "none", "ghost": "none",   # 关思考满血吐字
    "outline": "medium", "beats": "medium",               # 情节因果推演
    "review": "medium", "extract": "xhigh",               # 严格逻辑检查
    "compression": "medium",                              # 长期记忆压缩（P3）
    "style": "medium",                                    # 文风自学习（P3）
    "design": "medium",                                   # 设计界面协作（世界观/人物/内核）
    "design_outline": "high",                             # 结构大纲编排（全局结构推演）
    "design_extract": "low",                              # 采纳提炼（字段化改写，求快）
}

# ---- 推理参数适配 ----------------------------------------------------------
# 各模型/后端对「思考控制」的支持粒度并不一致（实测 LM Studio 0.4 + qwen3.5-9b）：
#   · reasoning_effort 只接受 none/minimal/low/medium/high/xhigh，发 on/off 直接 400
#   · 只支持 开/关 两档的模型收到 low~xhigh 会打 WARN 并回退 'on'（思考开关失控）
#   · enable_thinking 被 llama.cpp 直接忽略（根本关不掉思考），仅 vLLM/DashScope 认
#   · 实测唯一能真正关掉思考的是 reasoning_effort='none'（响应 2.4s → 0.1s）
# 因此按「模型能力」分流：
#   levels —— 支持 low/medium/high 分级（OpenAI o 系 / gpt-oss 等）
#   toggle —— 只支持 开/关（LM Studio 下的 Qwen3 / Qwen3.5 / QwQ 等）
#   none   —— 不发送任何推理参数（普通模型 / 参数被拒后自动降级到此档）
REASONING_MODES = ("auto", "levels", "toggle", "none")
# 已知「仅开关档」的推理模型（按模型名小写匹配）
_REASONING_TOGGLE_RE = re.compile(
    r"(qwen[\W_]*3|qwq|glm[\W_-]*4(\.\d)?|kimi[\W_-]*k2|magistral)", re.I)
# 档位强弱（0 = 关闭），用于把任务档位收拢到模型实际支持的取值上
_EFFORT_RANK = {"none": 0, "off": 0, "minimal": 1, "low": 1,
                "medium": 2, "on": 2, "high": 3, "xhigh": 4}
# 非标准档位收拢到通用三档（xhigh 只有极少数模型认）
_LEVEL_ALIASES = {"xhigh": "high", "minimal": "low"}
# 推理参数被拒时的降级链
_DOWNGRADE_CHAIN = {"levels": "toggle", "toggle": "none"}
# 后端报错里常直接给出合法取值，形如 "Supported values: none, minimal, low..."
_SUPPORTED_RE = re.compile(r"supported (?:values|settings)\s*:?\s*([^\n.]+)",
                           re.I)
# 模型 → 实际生效模式 / 合法取值（仅记录运行时探测结果，配置显式指定时优先）
_reasoning_mode_cache: dict[str, str] = {}
_reasoning_allowed_cache: dict[str, tuple[str, ...]] = {}

_client: Optional[AsyncOpenAI] = None
_client_base: str = ""


def _model_key(model: str) -> str:
    return (model or "").strip().lower()


def reasoning_mode(model: str = "") -> str:
    """判定该模型应使用的推理参数模式：levels / toggle / none。

    优先级：config.json 的 reasoning_mode（非 auto 时）> 运行时探测记忆 > 模型名规则。
    """
    override = str(config.get("reasoning_mode", "auto") or "auto").lower()
    if override in ("levels", "toggle", "none"):
        return override
    cached = _reasoning_mode_cache.get(_model_key(model))
    if cached:
        return cached
    if _REASONING_TOGGLE_RE.search(_model_key(model)):
        return "toggle"
    return "levels"          # 未知模型按标准分级发（保持兼容既有接入方）


def _fit_effort(effort: str, allowed: tuple[str, ...]) -> str:
    """把任务档位收拢到该模型实际支持的取值上（避免被拒或触发后端告警）。"""
    if not allowed or effort in allowed:
        return effort
    if _EFFORT_RANK.get(effort, 2) <= 0:          # 想关思考
        for cand in ("none", "off", "minimal", "low"):
            if cand in allowed:
                return cand
        return min(allowed, key=lambda v: _EFFORT_RANK.get(v, 99))
    levels = [v for v in allowed if _EFFORT_RANK.get(v, 0) > 0]
    if not levels:                                 # 后端只认 on/off
        return "on" if "on" in allowed else allowed[0]
    rank = _EFFORT_RANK.get(effort, 2)
    return min(levels, key=lambda v: abs(_EFFORT_RANK.get(v, 2) - rank))


def reasoning_extra(purpose: str, model: str = "") -> dict:
    """按「任务档位 × 模型能力」生成 extra_body 里的推理控制参数。"""
    effort = REASONING_EFFORT.get(purpose, "medium")
    mode = reasoning_mode(model)
    if mode == "none":
        return {}
    if effort == "none":
        # 关思考：reasoning_effort='none' 是 llama.cpp 唯一认的写法，
        # enable_thinking 兜住 vLLM / DashScope 一类后端
        return {"reasoning_effort": "none", "enable_thinking": False}
    if mode == "toggle":
        # 只支持开关：发任何分级值都会被后端告警并回退，改为不干预（默认即开思考）
        return {"enable_thinking": True}
    level = _fit_effort(effort, _reasoning_allowed_cache.get(
        _model_key(model), ()))
    return {"reasoning_effort": _LEVEL_ALIASES.get(level, level)}


def describe_reasoning(purpose: str, model: str = "") -> str:
    """把本次「实际发出」的推理参数渲染成一句人读说明（供生成日志对账）。

    与 reasoning_extra() 同源，保证日志里写的就是要发出去的值。排查后端推理
    告警（如 LM Studio 的 "Reasoning setting 'high' is not supported"）时，
    按时间戳与后端日志一比即可判定：这是我们发的，还是后端自身的模型设置。
    """
    extra = reasoning_extra(purpose, model)
    override = str(config.get("reasoning_mode", "auto") or "auto").lower()
    src = (override if override in ("levels", "toggle", "none")
           else f"auto→{reasoning_mode(model)}")
    effort = extra.get("reasoning_effort")
    if effort == "none":
        return f"推理参数：关闭思考（reasoning_effort=none，{src}）"
    if effort:
        return f"推理参数：reasoning_effort={effort}（{src}）"
    if extra.get("enable_thinking"):
        return f"推理参数：不干预（{src}，沿用模型默认档位）"
    return f"推理参数：不发送（{src}）"


def note_reasoning_error(model: str, error: Exception) -> bool:
    """推理参数被后端拒绝时：记住合法取值（若报错里给了）或降级一档。

    配置里显式指定了模式时不自动调整（尊重作者选择）。返回是否可立即重试。
    """
    if str(config.get("reasoning_mode", "auto") or "auto").lower() in \
            ("levels", "toggle", "none"):
        return False
    msg = str(error)
    key = _model_key(model)
    m = _SUPPORTED_RE.search(msg)
    if m:
        vals = tuple(v.strip().strip("'\" ").lower()
                     for v in re.split(r"[,，、]+", m.group(1)))
        vals = tuple(v for v in vals if v.isalpha())
        if vals and _reasoning_allowed_cache.get(key) != vals:
            _reasoning_allowed_cache[key] = vals
            return True
    if "reason" not in msg.lower() and "thinking" not in msg.lower():
        return False
    nxt = _DOWNGRADE_CHAIN.get(reasoning_mode(model))
    if not nxt:
        return False
    _reasoning_mode_cache[key] = nxt
    return True


def reset_reasoning_cache() -> None:
    """设置页改了推理参数模式后清空探测/降级记忆。"""
    _reasoning_mode_cache.clear()
    _reasoning_allowed_cache.clear()


def _is_local_base(base: str = "") -> bool:
    """api_base 是否指向本机（LM Studio / 本地推理后端）。"""
    return "127.0.0.1" in base or "localhost" in base


def _get_client() -> AsyncOpenAI:
    """懒构建客户端；api_base 变更后自动重建。"""
    global _client, _client_base
    base = config.get("api_base", "http://127.0.0.1:1234/v1")
    if _client is None or _client_base != base:
        kwargs: dict = {
            "base_url": base,
            "api_key": config.get("api_key") or "lm-studio",
        }
        if _is_local_base(base):
            # 本地后端绕开系统代理：httpx 会读 Windows 注册表 / macOS 系统代理
            # （urllib.getproxies()），把发往 127.0.0.1 的请求也塞进 Clash 一类
            # 代理，凭空多一层中间环节——代理抽风时报 502，症状长得像 LM Studio
            # 挂了。云端 base 保持 trust_env 默认值不动：有人正是靠系统代理才能
            # 连上 OpenAI 等。
            # timeout 必须显式给：自定义 http_client 后 SDK 的 timeout 不再生效，
            # 而 httpx 默认只有 5s，会掐断「首 token 前久等思考」的流式生成；
            # 这里与 SDK 默认值对齐（总计 600s / 建连 5s）。
            kwargs["http_client"] = httpx.AsyncClient(
                trust_env=False, timeout=httpx.Timeout(600.0, connect=5.0))
        _client = AsyncOpenAI(**kwargs)
        _client_base = base
    return _client


def rebuild_client() -> None:
    """设置页保存连接信息后调用，强制重建。"""
    global _client
    _client = None


def _provider_tag() -> str:
    base = config.get("api_base", "")
    return "local" if _is_local_base(base) else base


class GenStats(NamedTuple):
    text: str
    thought: str = ""            # 思考流（单独收集，绝不混入正文）
    usage: object | None = None
    ttft: float | None = None    # 首 token 延迟（秒）
    duration: float = 0.0        # 总耗时（秒）


class GenerationCancelled(Exception):
    """用户主动中止生成（Esc / 软停止按钮），携带已生成的半成品。"""

    def __init__(self, partial: str):
        super().__init__("generation cancelled")
        self.partial = partial


_THINKING_RE = re.compile(r"<think>.*?</think>", re.S)


def strip_thinking_tags(text: str) -> str:
    """DeepSeek-R1 / QwQ 等推理模型会把 <think> 块写进 content，强制剥离。"""
    return _THINKING_RE.sub("", text).strip()


def parse_llm_json(text: str, default=None):
    """容错 JSON 解析：剥离 Markdown 围栏 + 截取首尾花括号边界。"""
    clean = re.sub(r"```json?\s*|\s*```", "", text.strip())
    first, last = clean.find("{"), clean.rfind("}")
    if first != -1 and last != -1:
        clean = clean[first:last + 1]
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        return default if default is not None else {}


def _estimate_usage(messages: list[dict], full_text: str) -> tuple[int, int]:
    """usage 兜底估算（中文约 1.5 字符/token），仅用于统计落库。"""
    prompt_chars = sum(len(m.get("content") or "") for m in messages)
    return int(prompt_chars / 1.5), int(len(full_text) / 1.5)


async def _log_call(purpose: str, model: str, usage, ttft: Optional[float],
                    duration: float, success: bool, error_msg: str = "",
                    messages: Optional[list[dict]] = None,
                    full_text: str = "") -> None:
    prompt_tokens = completion_tokens = 0
    if usage is not None:
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    elif messages is not None:
        prompt_tokens, completion_tokens = _estimate_usage(messages, full_text)
    # 云端费用估算（本地恒 0）；费率在 config.json 中配置（元/1k tokens）
    cost = 0.0
    if not _provider_tag().startswith("local") and \
            not ("127.0.0.1" in _provider_tag() or "localhost" in _provider_tag()):
        cost = (prompt_tokens / 1000 * config.get("cost_per_1k_input", 0.0)
                + completion_tokens / 1000
                * config.get("cost_per_1k_output", 0.0))
    try:
        await db.insert_llm_call(
            provider=_provider_tag(), model=model or config.get("model", ""),
            purpose=purpose, prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            ttft_ms=int((ttft or 0) * 1000), duration_ms=int(duration * 1000),
            cost=cost, success=1 if success else 0, error_msg=error_msg[:500])
    except Exception:
        pass  # 统计落库失败不影响业务


async def call_llm_json(model: str, messages: list[dict], *,
                        purpose: str = "extract",
                        temperature: float = 0.1,
                        json_schema: Optional[dict] = None,
                        cancel_event: Optional[asyncio.Event] = None,
                        max_retries: int = 2,
                        default: Optional[dict] = None) -> dict:
    """非流式结构化输出：response_format=json_schema 约束（LM Studio 在
    token 层做语法掩码，输出 100% 合法 JSON）；不支持 schema 的接入方由
    parse_llm_json 兜底。失败重试后仍失败返回 default 或 {}。
    """
    last_err: Optional[Exception] = None
    used_model = model or config.get("model", "local-model")
    response_format = None
    if json_schema:
        response_format = {"type": "json_schema",
                           "json_schema": {"name": purpose,
                                           "strict": True,
                                           "schema": json_schema}}
    for attempt in range(max_retries + 1):
        t0 = time.time()
        try:
            if cancel_event and cancel_event.is_set():
                raise GenerationCancelled("")
            kwargs = dict(model=used_model, messages=messages,
                          stream=False, temperature=temperature,
                          extra_body=reasoning_extra(purpose, used_model))
            if response_format:
                kwargs["response_format"] = response_format
            resp = await _get_client().chat.completions.create(**kwargs)
            if cancel_event and cancel_event.is_set():
                raise GenerationCancelled("")
            raw = (resp.choices[0].message.content or "") if resp.choices else ""
            duration = time.time() - t0
            await _log_call(purpose, used_model,
                            getattr(resp, "usage", None), None, duration,
                            True, messages=messages, full_text=raw)
            parsed = parse_llm_json(strip_thinking_tags(raw),
                                    default=default if default is not None
                                    else None)
            if parsed is None:
                raise ValueError("返回内容无法解析为 JSON")
            return parsed
        except GenerationCancelled:
            raise
        except Exception as e:
            last_err = e
            await _log_call(purpose, used_model, None, None, time.time() - t0,
                            False, str(e), messages=messages, full_text="")
            if attempt < max_retries:
                if note_reasoning_error(used_model, e):
                    await asyncio.sleep(0.2)      # 参数被拒：降级后立即重试
                else:
                    await asyncio.sleep(1.5 * (attempt + 1))
            continue
    if default is not None:
        return default
    raise last_err if last_err else RuntimeError("结构化调用失败：未知错误")


async def call_llm_text(model: str, messages: list[dict], *,
                        purpose: str = "refine",
                        temperature: float = 0.5,
                        cancel_event: Optional[asyncio.Event] = None,
                        max_retries: int = 2) -> str:
    """非流式文本调用（精修/局部重写）：temperature 0.5 稳定不乱改。"""
    last_err: Optional[Exception] = None
    used_model = model or config.get("model", "local-model")
    for attempt in range(max_retries + 1):
        t0 = time.time()
        try:
            if cancel_event and cancel_event.is_set():
                raise GenerationCancelled("")
            resp = await _get_client().chat.completions.create(
                model=used_model, messages=messages, stream=False,
                temperature=temperature,
                extra_body=reasoning_extra(purpose, used_model))
            if cancel_event and cancel_event.is_set():
                raise GenerationCancelled("")
            raw = (resp.choices[0].message.content or "") if resp.choices else ""
            await _log_call(purpose, used_model,
                            getattr(resp, "usage", None), None,
                            time.time() - t0, True, messages=messages,
                            full_text=raw)
            return strip_thinking_tags(raw)
        except GenerationCancelled:
            raise
        except Exception as e:
            last_err = e
            await _log_call(purpose, used_model, None, None, time.time() - t0,
                            False, str(e), messages=messages, full_text="")
            if attempt < max_retries:
                if note_reasoning_error(used_model, e):
                    await asyncio.sleep(0.2)      # 参数被拒：降级后立即重试
                else:
                    await asyncio.sleep(1.5 * (attempt + 1))
            continue
    raise last_err if last_err else RuntimeError("调用失败：未知错误")


async def get_available_models() -> list[str]:
    """获取当前可用模型列表（兼任连接测试）。失败返回空列表。"""
    try:
        response = await _get_client().models.list()
        return [m.id for m in response.data]
    except Exception as e:
        print(f"获取模型列表失败（LM Studio 是否已开启 Local Server？）：{e}")
        return []


async def fetch_ghost_suggestion(prefix_text: str,
                                 cancel_event: asyncio.Event,
                                 call_fn: Optional[Callable] = None) -> str:
    """行内补全专用（方案 5.2.5）：截取极短前文，关思考，极低 token 预算。

    非流式单次取回；失败静默返回空串（仅落库统计），严禁打断写作体验。
    """
    if not prefix_text.strip():
        return ""
    short_context = prefix_text[-400:]     # 仅取光标前 ~400 字
    messages = [
        {"role": "system",
         "content": "你是一个小说续写助手。紧接着作者给出的前文，"
                    "极为自然地续写接下来的半句话或一句话，"
                    "禁止任何解释、开场白或多余标点。"},
        {"role": "user",
         "content": f"前文如下：\n{short_context}\n\n请直接续写："},
    ]
    t0 = time.time()
    try:
        if cancel_event.is_set():
            return ""
        model_name = config.get("ghost_model") or config.get("model",
                                                              "local-model")
        if call_fn is None:
            resp = await _get_client().chat.completions.create(
                model=model_name,
                messages=messages,
                max_tokens=config.get("ghost_max_tokens", 25),
                temperature=0.7,
                stream=False,
                extra_body=reasoning_extra("ghost", model_name),
            )
            if cancel_event.is_set():
                return ""
            raw = (resp.choices[0].message.content
                   or "") if resp.choices else ""
            usage = getattr(resp, "usage", None)
        else:
            raw = await call_fn(model_name, messages)
            usage = None
        if cancel_event.is_set():
            return ""
        await _log_call("ghost", model_name, usage, None, time.time() - t0,
                        True, messages=messages, full_text=raw)
        return strip_thinking_tags(raw).replace("\n", " ").strip()
    except Exception as e:
        try:
            await _log_call("ghost", config.get("ghost_model", ""), None, None,
                            time.time() - t0, False, str(e),
                            messages=messages, full_text="")
        except Exception:
            pass
        return ""          # 静默丢弃


async def call_llm_stream(model: str, messages: list[dict],
                          on_chunk: Callable[[str], None], *,
                          purpose: str = "draft",
                          cancel_event: Optional[asyncio.Event] = None,
                          max_retries: int = 2,
                          on_thought: Optional[Callable[[str], None]] = None,
                          **params) -> GenStats:
    """流式生成：取消 / 退避重试 / 思考流分离 / 用量记录。

    任务差异参数（temperature / response_format 等）由调用方按 purpose 传入。
    """
    last_err: Optional[Exception] = None
    used_model = model or config.get("model", "local-model")
    for attempt in range(max_retries + 1):
        full: list[str] = []
        think: list[str] = []
        usage = None
        t0 = time.time()
        ttft: Optional[float] = None
        try:
            resp = await _get_client().chat.completions.create(
                model=used_model,
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
                extra_body=reasoning_extra(purpose, used_model),
                **params,
            )
            async for chunk in resp:
                if cancel_event and cancel_event.is_set():
                    await resp.close()
                    await _log_call(purpose, used_model, usage, ttft,
                                    time.time() - t0, True,
                                    messages=messages,
                                    full_text="".join(full))
                    raise GenerationCancelled("".join(full))
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:                 # 思考流单独走，不进正文
                    if ttft is None:
                        ttft = time.time() - t0
                    think.append(reasoning)
                    if on_thought:
                        on_thought(reasoning)
                    continue
                if delta.content:
                    if ttft is None:
                        ttft = time.time() - t0
                    full.append(delta.content)
                    on_chunk(delta.content)
        except GenerationCancelled:
            raise
        except Exception as e:
            last_err = e
            await _log_call(purpose, used_model, None, ttft, time.time() - t0,
                            False, str(e), messages=messages,
                            full_text="".join(full))
            if attempt < max_retries:
                if note_reasoning_error(used_model, e):
                    await asyncio.sleep(0.2)   # 参数被拒：降级后立即重试
                else:
                    await asyncio.sleep(1.5 * (attempt + 1))   # 退避重试
            continue
        duration = time.time() - t0
        await _log_call(purpose, used_model, usage, ttft, duration, True,
                        messages=messages, full_text="".join(full))
        return GenStats(text=strip_thinking_tags("".join(full)),
                        thought="".join(think), usage=usage, ttft=ttft,
                        duration=duration)
    raise last_err if last_err else RuntimeError("生成失败：未知错误")
