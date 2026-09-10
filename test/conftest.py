"""pytest 全局设施：导入路径 + 隔离夹具。

隔离原则：所有测试不触碰真实 novels/、真实 ~/.writer/prompts 与根目录
config.json——统一 monkeypatch 到 pytest tmp_path。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db, paths, project  # noqa: E402


@pytest.fixture
def novel_root(tmp_path, monkeypatch):
    """把所有可写路径重定向到临时目录。"""
    root = tmp_path / "novels"
    root.mkdir()
    monkeypatch.setattr(paths, "NOVELS_DIR", str(root))
    monkeypatch.setattr(paths, "USER_PROMPTS_DIR", str(tmp_path / "user_prompts"))
    monkeypatch.setattr(paths, "CONFIG_FILE", str(tmp_path / "config.json"))
    return root


@pytest.fixture
async def db_project(novel_root):
    """一个已建库的隔离测试项目；用毕关闭全局 db。"""
    name = "_test_project"
    project.create_project(name, title="测试项目")
    await project.init_project_db(name, title="测试项目")
    yield name
    await db.close()


@pytest.fixture(autouse=True)
def block_real_network(request, monkeypatch):
    """安全网：拦截一切真实 HTTP，防止测试「悄悄」调用本机 / 云端模型。

    单元测试必须零网络：被测代码里的 LLM 调用应通过 DI 注入 fake
    （call_fn / extract_a_fn / text_call_fn / embed_fn）。

    - 请求会在此**立刻**抛错，而不是真的跑一遍模型（真跑一次 extract
      级别调用，单是思考就可能耗上几分钟）；
    - 即便调用方把异常吞掉（如 reviewer 的容错分支），用例仍会在收尾时
      判为失败，避免「悄悄联网还假通过」。
    确需真实网络的用例，显式加 @pytest.mark.allow_network。
    """
    if request.node.get_closest_marker("allow_network"):
        yield
        return
    import httpx

    hits: list[str] = []
    message = ("测试禁止真实网络请求（{url}）：请为被测代码注入 fake "
               "（call_fn / extract_a_fn / text_call_fn / embed_fn），"
               "或给用例加 @pytest.mark.allow_network")

    def _blocked_sync(self, url_request, *args, **kwargs):
        hits.append(str(url_request.url))
        raise RuntimeError(message.format(url=url_request.url))

    async def _blocked_async(self, url_request, *args, **kwargs):
        hits.append(str(url_request.url))
        raise RuntimeError(message.format(url=url_request.url))

    monkeypatch.setattr(httpx.Client, "send", _blocked_sync, raising=False)
    monkeypatch.setattr(httpx.AsyncClient, "send", _blocked_async,
                        raising=False)
    yield
    if hits:
        pytest.fail("该用例发起了 " + str(len(hits)) +
                    " 次真实网络请求（异常可能被吞掉）："
                    + ", ".join(hits[:3]) + "；请注入 fake 或标记 allow_network")
