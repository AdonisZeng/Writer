"""工作流引擎：统一抽象步骤 / 进度 / 日志 / 取消（方案 5.7）。

UI 只关心回调；步骤执行器为 async fn(ctx) -> dict（结果并入 ctx["results"]）。
P1 的定稿后处理管线（post-process）将以本引擎驱动。
"""
import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class Step:
    key: str
    name: str
    executor: Callable            # async fn(ctx) -> dict
    critical: bool = False        # 关键步骤失败则整体失败


@dataclass
class StepResult:
    ok: bool
    data: dict = field(default_factory=dict)


class Workflow:
    def __init__(self, name: str, steps: list[Step],
                 on_log: Optional[Callable[[str], None]] = None,
                 on_progress: Optional[Callable[[int, int], None]] = None):
        self.name = name
        self.steps = steps
        self.on_log = on_log or (lambda msg: print(f"[{name}] {msg}"))
        self.on_progress = on_progress
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    async def run(self, ctx: Optional[dict] = None,
                  step_by_step: bool = False,
                  confirm: Optional[Callable[[Step], Any]] = None) -> bool:
        """顺序执行步骤；step_by_step=True 时每步前等待 confirm(step) 确认。

        返回是否全部成功；关键步骤失败立即中止。
        """
        ctx = ctx or {}
        ctx.setdefault("results", {})
        ctx.setdefault("cancel_event", asyncio.Event())
        total = len(self.steps)
        self.on_log(f"工作流开始：{self.name}")
        for i, step in enumerate(self.steps):
            if self.cancelled or ctx["cancel_event"].is_set():
                self.on_log("工作流已取消")
                return False
            if step_by_step and confirm is not None:
                ok = confirm(step)
                if inspect.isawaitable(ok):
                    ok = await ok
                if not ok:
                    self.on_log(f"用户跳过步骤：{step.name}")
                    continue
            try:
                data = await step.executor(ctx)
                ctx["results"][step.key] = StepResult(True, data or {})
                self.on_log(f"✓ {step.name}")
            except Exception as e:
                ctx["results"][step.key] = StepResult(False, {"error": str(e)})
                self.on_log(f"✗ {step.name}：{e}")
                if step.critical:
                    self.on_log("关键步骤失败，工作流中止")
                    return False
            if self.on_progress:
                self.on_progress(i + 1, total)
        self.on_log(f"工作流完成：{self.name}")
        return True
