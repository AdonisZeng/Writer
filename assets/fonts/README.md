# 内置字体（assets/fonts）

Writer 追求**完全离线、视觉可控**的中文排版，界面与正文使用以下 SIL OFL 许可开源字体：

| 文件 | 家族名 | 用途 |
|---|---|---|
| `NotoSansSC-VF.ttf` | Noto Sans SC | UI 无衬线（界面 chrome） |
| `NotoSerifSC-VF.ttf` | Noto Serif SC | 正文衬线（编辑 / 预览 / 审阅） |
| `NotoSansMono-VF.ttf` | Noto Sans Mono | 数值等宽（状态栏 / 统计） |

## 获取方式

```bash
python tools/fetch_fonts.py          # 下载缺失字体与 OFL 许可文件
python tools/fetch_fonts.py --check  # 仅检查现状
```

脚本从官方发布源（`google/fonts` 及其 jsDelivr 镜像）下载并做完整性校验。

## 缺失时的行为

字体文件**不是必需的**：`ui/theme.py` 只注册实际存在的字体，缺失时自动回落系统字体
（Windows 微软雅黑 / macOS PingFang / 思源黑体等），应用照常启动，不会报错。

## 许可

以上字体均采用 SIL Open Font License 1.1，可自由分发与内嵌。
运行 `fetch_fonts.py` 会把各字体的 `OFL.txt` 一并保存为 `LICENSE-*.txt`。

> 说明：为避免用户输入生僻字时缺字，这里使用**完整 CJK 字库**（不做子集化），
> 因此安装包体积会明显增大——这是「完全离线、视觉可控」的预期代价。
