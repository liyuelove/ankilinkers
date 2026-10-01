# ankiLinkers

思源笔记闪卡 → Anki 的单向增量同步工具。以思源笔记为唯一事实源。

## 功能

- 增量单向同步：新增 / 更新 / 删除 / 卡组移动，按内容 hash 判定变更
- 映射规则：笔记本 / 文档 → Anki 卡组（文档级 > 笔记本级 > 默认卡组）
- 问答卡 1:1；多空挖空卡 → 单个 Cloze 笔记（`{{c1::}}`…`{{cN::}}` 自动多卡）
- 同步删除前自动备份（思源 riff 数据 + Anki 笔记快照 + 本地映射库）
- PySide6 图形界面：连接检测 → 映射配置 → 同步预览/执行 → 日志（默认折叠）
- 本地 SQLite 映射库（`riffCardID ↔ ankiNoteID + 内容hash`），Anki 侧 tag 兜底

## 前置条件

1. **思源笔记**运行中（内核 127.0.0.1:6806，token 自动从 `~/SiYuan/conf/conf.json` 读取）
2. **Anki** 运行中，且已安装 **AnkiConnect** 插件（工具 → 插件 → 获取插件 → 输入代码 `2055492159` → 重启）
3. Anki 中存在笔记模板（默认，可在配置中改）：
   - `KaTeX and Markdown Basic`（字段 Front / Back）
   - `KaTeX and Markdown Cloze`（字段 Text / Back Extra）

## 使用

```bash
uv sync                       # 安装依赖
uv run ankilinkers            # 启动 GUI
uv run ankilinkers check      # 命令行连接检测
uv run ankilinkers sync --dry-run   # 只预览不执行
uv run ankilinkers sync -y          # 命令行同步
uv run ankilinkers sync --notebook <boxID>   # 只同步某个笔记本
uv run ankilinkers notebooks / rules list    # 查看笔记本与映射规则
```

## 数据位置

默认在**可执行文件同级的 `data/` 目录**（便携模式；不可写时回退 `~/.local/share/ankiLinkers/`，亦可用环境变量 `ANKILINKERS_HOME` 指定）：

- `data/config.json`：配置（映射规则存于 sync.db）
- `data/sync.db`：本地映射库
- `data/backups/<时间戳>/`：备份

## 打包

```bash
sh scripts/build-dist.sh
# 产物：dist/ankilinkers 单文件（NixOS 专用：自动把 libGL/libxkbcommon 等
# /nix/store 系统库一并打入，裸环境可直接运行）
```
