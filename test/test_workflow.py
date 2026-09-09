"""workflow.py 测试：步骤执行 / 关键失败中止 / 取消 / 进度与日志。"""
import asyncio

from core import workflow


async def test_run_all_steps_success():
    logs, progress = [], []
    executed = []

    async def step_a(ctx):
        executed.append("a")
        return {"x": 1}

    async def step_b(ctx):
        executed.append("b")
        assert ctx["results"]["a"].data["x"] == 1  # 上下文可传递
        return {}

    wf = workflow.Workflow("测试流",
                           [workflow.Step("a", "步骤A", step_a),
                            workflow.Step("b", "步骤B", step_b)],
                           on_log=logs.append,
                           on_progress=lambda done, total:
                           progress.append((done, total)))
    assert await wf.run({"seed": 1}) is True
    assert executed == ["a", "b"]
    assert progress == [(1, 2), (2, 2)]
    assert any("工作流完成" in m for m in logs)


async def test_critical_failure_aborts():
    executed = []

    async def bad(ctx):
        raise RuntimeError("爆炸")

    async def never(ctx):
        executed.append("never")

    logs = []
    wf = workflow.Workflow("关键流",
                           [workflow.Step("bad", "会失败", bad, critical=True),
                            workflow.Step("n", "不应执行", never)],
                           on_log=logs.append)
    assert await wf.run() is False
    assert executed == []
    assert any("关键步骤失败" in m for m in logs)


async def test_non_critical_failure_continues():
    async def bad(ctx):
        raise RuntimeError("可容忍")

    async def ok(ctx):
        return {}

    wf = workflow.Workflow("容错流",
                           [workflow.Step("bad", "可失败", bad),
                            workflow.Step("ok", "继续执行", ok)],
                           on_log=lambda m: None)
    assert await wf.run() is True


async def test_cancel_stops_between_steps():
    executed = []

    async def a(ctx):
        ctx["cancel_event"].set()
        return {}

    async def b(ctx):
        executed.append("b")

    wf = workflow.Workflow("取消流",
                           [workflow.Step("a", "置取消", a),
                            workflow.Step("b", "不应执行", b)],
                           on_log=lambda m: None)
    assert await wf.run() is False
    assert executed == []


async def test_step_by_step_confirmation():
    confirmed, executed = [], []

    async def a(ctx):
        executed.append("a")
        return {}

    confirm_log = []
    wf = workflow.Workflow("确认流",
                           [workflow.Step("a", "步骤A", a),
                            workflow.Step("b", "步骤B", a)],
                           on_log=confirm_log.append)

    async def confirm(step):
        confirmed.append(step.key)
        return step.key == "a"  # 只确认第一步

    assert await wf.run(step_by_step=True, confirm=confirm) is True
    assert confirmed == ["a", "b"]
    assert executed == ["a"]  # b 被用户跳过
