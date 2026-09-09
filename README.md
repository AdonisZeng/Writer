# Writer

基于 Python + [Flet](https://flet.dev) 的桌面端 **AI 辅助小说创作工具**。

> 本地优先 · 正文纯文本存储 · SQLite 管理元数据 · 零环境依赖打包分发

## 这是什么？

Writer 是一款让作者与 AI 协作进行**长篇小说创作**的桌面应用。它的核心目标是：让 AI 能够连续创作**百万字级长篇**，而不出现人设崩塌、剧情矛盾、时间线错乱。

为此，项目围绕「叙事一致性（Canon）」设计了一套机制：

- **三级上下文金字塔**：全书静态设定 → 人物卡 → 章级动态信息，按缓存友好的顺序装配 Prompt，既省 token 又保证上下文完整；
- **Canon Gate 两层校验**：确定性检查（纯算法、零 LLM 成本，拥有落盘阻断权）+ 语义审查（LLM 对照正史给出诊断，仅提示、不改稿，作者可逐条忽略）；
- **定稿后处理管线**：章节定稿后自动抽取时间线、伏笔、角色状态增量并写回 Canon 数据库，支持按快照逆向回滚。

## 主要功能

- **写作工作台**：章节树、登场名册、伏笔监控、编辑 / 预览 / 审阅三模式画布、Canon 诊断、生成日志与统计；
- **设计工作台**：故事大纲、世界观、人物设定管理，以及 AI 协作台——AI 产出经作者「采纳」后才会写入设定库；
- **AI 调用**：统一走 OpenAI 协议，支持 LM Studio 本地模型与云端 API；流式生成、`Esc` 软停止、按用途路由思考粒度；
- **精修与 Diff**：`Ctrl+K` 精修草稿，红绿 Diff 逐块采纳，不存在无预览的「一键替换」；
- **打包分发**：PyInstaller 目录模式打包，可选依赖（sqlite-vec / watchdog / MCP）缺失时自动静默降级。

## 快速开始

```bash
# 安装依赖
pip install flet openai

# 运行（首次启动自动生成 config.json 与 novels/ 示例项目）
python main.py
```

- 全局配置：`config.json`（API 地址 / 密钥 / 模型 / 上下文长度 / 主题 / RAG 等）
- 用户数据：`novels/<项目名>/`，每个项目包含设定、大纲、章节正文与数据库

## 测试与打包

```bash
python -m pytest                                      # 运行全部测试
powershell -ExecutionPolicy Bypass -File build.ps1    # 打包为 dist/Writer/Writer.exe
```

## 状态

项目仍在开发中，尚未正式发布，接口与数据格式可能变动。

## 许可证

[GPL-3.0](LICENSE)
