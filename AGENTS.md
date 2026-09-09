# Writer

桌面端 **AI 辅助小说创作工具**（Python + Flet）。本地优先、正文纯文本存储、SQLite 管元数据、零环境依赖打包分发。

**核心目标**：让 AI 能连续创作**百万字级长篇**而不出现人设崩塌、剧情矛盾、时间线错乱。

> 本文档描述**当前实际实现**，是新人 / AI 快速上手项目的入口。原始设计方案 `Writer.md` 已废弃删除，实现细节以代码为准。

---

## 技术栈

| 用途 | 选型 | 说明 |
|---|---|---|
| GUI | `flet` **0.86.2** | 基于 Flutter 的跨平台桌面 UI（注意版本 API 差异，见文末「开发约定」） |
| 大模型 | `openai`（异步 SDK） | LM Studio 本地 `/v1` 与云端 API **统一走 OpenAI 协议**；模型发现用 `client.models.list()` |
| 元数据 | `sqlite3`（标准库） | WAL + 单写者队列，零额外依赖 |
| 向量检索 | `sqlite-vec`（**可选**，P2 RAG） | 未安装时整模块静默降级 |
| 文件热重载 | `watchdog`（**可选**，P2） | 未安装时降级为激活时 mtime 检查 |
| 对外只读接口 | `mcp`（**可选**，P3） | `core/mcp_server.py`，仅只读工具 |
| 打包 | `pyinstaller` | 目录模式，见 `build.ps1` |
| 测试 | `pytest` + `pytest-asyncio` | `asyncio_mode=auto`，`testpaths=test` |

**必需依赖**：`flet`、`openai`。其余（sqlite-vec / watchdog / mcp）缺失均静默降级，不影响主流程。

---

## 快速开始

```bash
python main.py          # 运行应用（首次启动自动生成 novels/示例小说 与 config.json）
python -m pytest        # 运行全部测试（当前 100+ 用例）
powershell -ExecutionPolicy Bypass -File build.ps1   # 打包为 dist/Writer/Writer.exe
```

- 全局配置：`config.json`（api_base / api_key / model / context_limit / 主题 / RAG 等）。
- 用户数据：`novels/<项目名>/`，每个项目 = `settings.md` + `outline.md` + `chapters/` + `.writer/`。

---

## 目录结构（实际）

```text
Writer/
├── main.py                     # 入口：加载配置 → 释放模板 → 打开 flet 窗口 → WriterApp
├── config.json                 # 全局配置
├── build.ps1 / pytest.ini
├── prompts/                    # 【内置】Prompt 模板（首次运行释放到 ~/.writer/prompts）
│   ├── system.md               # 跨任务共用 System 前缀（文风/世界观/禁忌占位）
│   └── next_chapter_draft.md   # 章节草稿 User 模板
├── core/                       # 核心逻辑（纯 Python，完全解耦 UI）
│   ├── config.py / paths.py / project.py    # 配置、路径常量、项目生命周期
│   ├── db.py                   # ★ SQLite 单写者队列 + 建表 + 全部 DAO
│   ├── file_manager.py         # 正文/设定文件读写（原子写 tmp+replace）
│   ├── ai_service.py           # ★ 流式/非流式/JSON 调用 + 模型发现 + 用量记录 + 思考粒度路由
│   ├── prompt_builder.py       # ★ 三级模板覆盖 + Tier 金字塔上下文装配 + Token 预算
│   ├── canon/                  # ★ 叙事一致性模块
│   │   ├── __init__.py         #   门面：run_gate / build_canon_context / build_pov_block
│   │   ├── store.py            #   Canon 五表读写 + 定稿写回/回滚快照
│   │   ├── context.py          #   按优先级渲染 Canon / POV 上下文块
│   │   ├── validator.py        #   确定性校验（纯算法，零 LLM 成本，唯一有 BLOCK 权）
│   │   ├── reviewer.py         #   语义审查（LLM → JSON 诊断，仅提示不改稿）
│   │   └── extractor.py        #   定稿抽取 Canon 增量（宏观/微观双管线 + Schema 约束）
│   ├── commands/               # ★ 命令模式，每个创作动作一个命令
│   │   ├── generate_draft.py   #   组装上下文 → 流式生成草稿
│   │   ├── refine_draft.py     #   Ctrl+K 精修（产出走 Diff）
│   │   ├── save_draft.py       #   预览采纳 → 写入正文 + 归档版本
│   │   ├── finalize_chapter.py #   定稿：Gate → 状态变更 → 后处理管线 → .txt 投影
│   │   ├── rollback.py         #   逆向回滚（按快照还原 Canon）
│   │   ├── batch_generate.py   #   批量连续生成（P3）
│   │   ├── export_book.py      #   全本导出 TXT（P3）
│   │   └── chapters.py         #   章节增删改/重命名/拖拽重排/细纲更新
│   ├── workflow.py             # 步骤/进度/日志/取消引擎（驱动定稿后处理管线）
│   ├── rag.py                  # RAG 检索（P2，可选）
│   ├── diff_utils.py           # 红绿 Diff hunks（P2 逐块采纳）
│   ├── file_watcher.py         # watchdog 热重载（P2，可选）
│   ├── sensitivity.py          # 敏感词预检（P3，info 级，可选）
│   └── mcp_server.py           # MCP 只读服务器（P3，可选）
├── ui/
│   ├── shortcuts.py            # 全局快捷键注册（flet on_keyboard_event 分发）
│   ├── views/
│   │   ├── main_view.py        # ★ WriterApp：三栏工作台 + 写作/设计双界面切换（UI 总入口）
│   │   ├── editor.py           # 编辑画布（编辑 / 预览 / 审阅 三模式，Stack 常驻切换）
│   │   ├── design.py           # 设计界面（大纲/世界观/人物 + AI 协作台）
│   │   └── settings.py         # 设置页（全屏覆盖层：API/模型/RAG/主题/项目切换）
│   └── components/             # status_bar（含模式切换）、model_selector、chapter_tree、
│                               # cast_panel、beat_panel、plot_monitor、diagnostics、
│                               # diff_card、ghost_bar
├── novels/示例小说/            # 用户数据样例
└── test/                       # pytest（conftest 提供 novel_root / db_project 夹具）
```

**读代码建议顺序**：`main.py` → `ui/views/main_view.py`（UI 全貌）→ `core/db.py`（数据层）→ `core/prompt_builder.py` + `core/ai_service.py`（AI 调用）→ `core/canon/`（一致性）→ `core/commands/`（业务动作）。

---

## 数据模型（`state.db`）

| 表 | 作用 |
|---|---|
| `project_core` | 项目主台账：title / genre / premise / synopsis / worldbuilding / writing_style / global_guidance / 篇幅 |
| `chapters` | 章节：`id` 恒定主键、`order_index` 浮点排序（支持插章）、`number` 仅展示序号（可重算）、`status` |
| `characters` | 角色卡 + 跨章动态状态（`cs_location/cs_level/cs_items/cs_knowledge` 等） |
| `drafts` | 草稿版本链（当前版在 `chapters/`，历史版归档 `.writer/drafts/`） |
| `canon_timeline` / `canon_char_state` / `canon_plot_lines`(+`_snapshots`) / `canon_facts` / `canon_summaries` | **Canon 五表**：时间线 / 角色状态 / 伏笔（含回滚快照）/ 客观事实 / 章节摘要 |
| `llm_calls` | 用量统计（按 用途×模型 聚合，云端费用估算） |
| `post_process_steps` | 定稿后处理各步骤状态（支持单步重试） |
| `diagnostics_dismissed` | 作者「忽略」的诊断指纹（跨会话持久） |
| `rag_chunks` / `rag_meta` | 向量索引（仅 sqlite-vec 可用时创建） |

**章节状态流**：`outlined`（细纲）→ `drafted`（草稿）→ `revised`（精修）→ `finalized`（定稿）。

---

## 核心机制

- **上下文三层金字塔**（`prompt_builder`，缓存友好排序，直接省 token）：
  - Tier 1 全书静态：System 前缀（文风 / 世界观 / 禁忌），**跨任务字节级一致**以命中 KV Cache；
  - Tier 2 半静态：人物卡；
  - Tier 3 章级动态：时间线 / Canon 硬约束 / 本章细纲 / POV 信息差 / RAG 片段。
- **Canon 两层校验（Canon Gate）**：
  - 第一层**确定性检查**（纯算法零成本）——**唯一拥有 BLOCK 权**（字数超限、定稿元数据缺失等 error 级 → 阻断落盘）；
  - 第二层**语义审查**（LLM 对照正史）——**仅产出诊断，一律提示不改稿**，作者可逐条忽略。
- **定稿流程**：强制过 Gate → 置 `finalized` → 后处理管线（摘要/时间线/伏笔抽取 → Canon 写回 → 角色状态更新，`workflow` 驱动，可单步重试）→ 额外投影一份 `.txt` 到项目根目录。
- **逆向回滚**：`finalized → revised`，Canon 按快照还原（时间线/摘要/角色状态/伏笔/本章新登场角色），正文保留、当前版归档。
- **AI 调用**：`ai_service` 按 `purpose` 路由思考粒度（draft/refine/ghost 关思考满血吐字；outline/beats/review/design 中思考；extract 最高）；流式生成支持 `Esc` 软停止（抛 `GenerationCancelled` 携带半成品）。
- **推理参数适配**（`ai_service.reasoning_extra`）：按模型能力分流 `levels` / `toggle` / `none`（设置页 `reasoning_mode`，默认 `auto` 按模型名识别）。Qwen3 / Qwen3.5 一类只支持 开/关 的模型收到 `low~xhigh` 会被 LM Studio 告警并回退 `on`，故开思考时不发分级值；关思考统一发 `reasoning_effort: none`（`enable_thinking` 对 llama.cpp 无效，仅兜住 vLLM / DashScope）。后端报错若给出合法取值会自动记忆并收拢，必要时逐级降级到不发送。

---

## 两个界面（底部状态栏切换）

底部状态栏**最左侧**的分段控件 `✍️ 写作 | 🎨 设计` 切换两大界面（与显示 AI 模型名同一行）。切换用 `ft.Stack` + `visible`，控件常驻不重建。

- **写作界面**（`main_view` 三栏工作台）：
  - 左栏：章节树 + 登场名册 + 伏笔监控；
  - 中栏：编辑画布（编辑 / 预览 / 审阅 三模式）；
  - 右栏：自绘标签页（本章细纲+生成 / 🛡️ Canon 诊断 / 生成日志 / 📊 统计）。
- **设计界面**（`ui/views/design.py`）：与 AI 协作设计作品框架——
  - 左栏板块导航：📖 故事大纲（`project_core`）/ 🌍 世界观（`settings.md`）/ 👥 人物（`characters` 表）；
  - 右栏 **🤖 AI 协作台**：把当前设定作为上下文流式对话，产出经作者点「采纳」才写入对应字段（遵守下方纪律）。

---

## 五条设计原则 & UI 负面清单（**改动前务必遵守**）

**五条设计原则**
1. 正文永远是纯文本 `.md`（用户可用任意编辑器打开，不锁定数据）；
2. 元数据进 SQLite（跨章关联查询，不放 JSON）；
3. Prompt 外置可改（内置 → 用户级 `~/.writer/prompts` → 项目级 `.writer/prompts` 三级覆盖）；
4. 生成必须过闸门（落盘前先过 Canon Gate）；
5. 上下文按缓存友好排序（稳定前置、可变后置）。

**UI 负面清单（硬性禁止）**
1. **禁止模态弹窗打断写作**：诊断走右栏、提示走行内浮层 / SnackBar、设置走独立覆盖页；
2. **禁止生成中锁定编辑区**：随时可滚动、选中、`Esc` 软停止；
3. **禁止不可预测的内容替换**：一切 AI 改写必须先 Diff 对比 + **逐块采纳**，不存在无预览的「一键优化整章」；
4. **禁止 AI 结果静默落盘**：生成内容先进预览 / 对比 / 协作台，**作者确认后才写入**正文或设定库。

---

## 开发约定与注意事项

- **Flet 0.86.2 API 陷阱**（易踩坑）：
  1. 控件**构造期访问 `self.page` 会抛 `RuntimeError`**——须分离「设属性」与「刷新」：先设属性，`page.add()` 之后再 `if self.page: ctrl.update()`；
  2. `ft.FilledButton` / `ft.OutlinedButton` / `ft.TextButton` 的文本是**首个位置参数**，不能用 `text=`（如 `ft.FilledButton("发送", icon=...)`）；
  3. 图标枚举需核对版本，如 `Icons.EDIT_OUTLINE` 不存在，应用 `Icons.EDIT`；
  4. `ft.Colors` / `ft.Icons` 首字母大写（新版命名空间）。
- **界面/模式切换**一律 `ft.Stack` + `visible`，控件常驻**绝不销毁重建**（保滚动位置 / 光标 / Undo 栈）。
- **数据库访问**：写操作走 `db.write(fn, ...)`（单写者队列串行消费），读操作走 `db.read(fn, ...)`（`to_thread` 直连，WAL 并发读安全）；**不要**直接持有/共享连接。
- **章节主键纪律**：`id` 恒定（所有外键锚点），`number` 仅展示序号（插章/重排时应用层重算并同步重命名 `.md`），排序用 `order_index`。
- **文件写入**统一用 `file_manager` 的原子写（tmp + `os.replace`）。
- **新增功能 / DAO 要补 `test/` 单元测试**（pytest `asyncio_mode=auto`，可复用 `db_project` 夹具）。
- 核心层（`core/`）**完全解耦 UI**，可独立测试；UI 只做回调编排，业务逻辑下沉到 `core/commands/`。
