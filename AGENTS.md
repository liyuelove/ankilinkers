# AGENTS.md — ankiLinkers 项目接管指南

> 面向接手开发/维护的 Agent：读完本文件即可掌握项目全貌。所有结论均经本机真实环境验证，标注「实测」的条目背后都有踩坑记录，不要轻易回退。

## 项目是什么

思源笔记（SiYuan）闪卡 → Anki 的**单向增量**同步工具。思源是唯一事实源；复习状态不回传。Python 3.12+ / PySide6 / httpx / SQLite，uv 管理（项目内 `.venv`，严禁装到全局 Python）。

核心行为：
- 卡片类型：问答卡 1:1；多空挖空卡 = 1 个 Cloze note（`{{c1::}}`~`{{cN::}}` 自动出多卡）
- 映射规则：文档 rootID > 笔记本 box ID > 默认卡组；未命中进默认卡组
- 思源删卡 → Anki 同步删除；删除或变更数 ≥ 阈值（默认 50）先自动备份
- 用户 Anki 模板：`KaTeX and Markdown Basic`(Front/Back)、`KaTeX and Markdown Cloze`(Text/Back Extra)；**字段内容写 Markdown**（模板 JS 自渲染），不自建模板、不动用户存量笔记

## 本机环境（实测基线）

- NixOS（无 `/usr/lib` 兜底，GUI 库必须显式提供，见「打包」节）
- 思源 3.8.2（nix 安装），工作区 `~/SiYuan`，API token 自动读自 `~/SiYuan/conf/conf.json` 的 `api.token`
- Anki 25.09.4，数据 `~/.local/share/Anki2/`，profile「账户 1」
- **AnkiConnect 是手工安装的 master 源码版**（`addons21/2055492159/`）：AnkiWeb 官方下载渠道给的是 PyQt5 旧分支（在 25.x 上必崩），meta.json 的版本上限被手动移除过。升级 AnkiConnect 时用 git.sr.ht 源码，不要从 AnkiWeb 装
- 同步时思源（:6806）与 Anki（:8765）都必须在运行

## 代码地图（src/ankilinkers/）

| 文件 | 职责 |
|---|---|
| `config.py` | Config dataclass + 数据目录解析（见下）+ token 自动发现 |
| `siyuan_client.py` | 思源内核 API：version/lsNotebooks/getRiffCards(分页)/sql/getBlockKramdown/getFile/doc_titles/search_docs |
| `anki_client.py` | AnkiConnect v6：全部 action 封装；`multi` 批处理；错误抛 AnkiConnectError |
| `parser/kramdown.py` | IAL 剥离、超级块解包、块切分、标题/列表识别、**代码区掩码**（占位符 `\x00N\x00`） |
| `parser/markdown.py` | 行级转换：`==x==`→cloze 或 `<mark>`、块引用/嵌入/思源链接清理、assets 收集改写 |
| `parser/__init__.py` | `parse_card(kd_text, block_type)` → ParsedCard；卡型判定入口 |
| `models.py` | SourceCard / Change / Plan（含 notices/skips） |
| `sync_engine.py` | 核心：`build_plan`（枚举→解析→hash diff→路由→**tag 接管**）与 `execute`（分批执行） |
| `storage.py` | SQLite：card_map / deck_map / sync_log |
| `backup.py` | 备份 = 思源 riff/ + 受影响 Anki 笔记 JSON + sync.db |
| `gui/` | PySide6：app（导航+共享状态）/ page_connect / page_mapping / page_sync / widget_log / worker（QThread） |
| `cli.py` | check / notebooks / rules / sync（--dry-run/--yes/--notebook）/ gui |

数据目录解析优先级（`config.py`）：`ANKILINKERS_HOME` 环境变量 > 可执行文件同级 `data/`（便携模式，源码运行时为项目根 `data/`）> platformdirs 兜底。首次运行自动从旧 platformdirs 位置迁移 sync.db 与配置。

## 关键机制（改动前必读）

### 闪卡枚举与内容拉取
- 枚举：`getRiffCards {id:""}` 分页遍历所有卡包（含内置卡包 `20230218211946-2kw8jgx`），响应带 `riffCardID`（同步主键）、box/rootID/hPath/type。**与文档树无关，子文档天然全覆盖**
- **实测**：`getBlockKramdown` 对标题/段落**不含子块**（标题卡的答案会整个丢失）→ `_full_kramdown` 用 SQL 查 children 递归补拉，按「子块 id 是否已出现在父 kramdown」去重（超级块/列表的子块已内联）
- 思源文档块索引有秒级延迟：createDocWithMd 后立刻查 SQL 可能为空，测试脚本要 `sleep`

### 卡片类型判定（parser）
- `NodeHeading`/`NodeSuperBlock` = 结构性问答卡：内部即使有 `==标记==` 也按 QA 处理（转 `<mark>`）。**实测**：标题卡的答案区段可能嵌着另一张独立挖空卡，按内容判型会把两者错误合并成挖空卡
- 其余块（段落/列表等）含 `==`（代码区外）→ 挖空卡，展开 `{{c1::}}`…
- QA 结构：超级块=首子块/其余；标题=标题/子块；列表=首顶层项/其余；段落=自身/子块
- 代码区（围栏+行内）掩码后不做任何转换；源内容含字面 `{{cN::` → 整卡跳过+告警（Anki 会误挖空）

### 增量依据 = 内容 hash，不是 updated
**实测**：容器块的子块被编辑时父块 `updated` 不变 → updated 粗筛不可靠。每次同步全量解析所有卡算 `sha256(类型+各字段)`，与 card_map 比对。读取全量（并发 8 线程，344 卡约 1~2s）、写入增量（只有 hash 变的才动 Anki）。

### 身份与接管
- 权威映射：card_map（riffCardID ↔ ankiNoteID）
- Anki 侧兜底：tag `siyuan-sync` + `syid-<riffCardID>`
- `_adopt_existing`：新增卡先按 tag 查 Anki 已有笔记——字段一致直接登记、不一致转更新、卡组漂移转移动、模板不一致跳过告警。**映射库整体丢失也能自愈**，曾有真实事故（构建脚本误删 data/）

### AnkiConnect 注意事项（都是实测坑）
- 必带 `version: 6`，否则响应无 `error` 字段
- `notesInfo` 对已删除的 note 返回**空对象**（直接 `n["noteId"]` 会 KeyError，先过滤）
- `updateNoteTags` 是整体替换 → 先 notesInfo 读旧 tags 再合并
- `addNotes` 一条错全批失败 → 先 `canAddNotesWithErrorDetail` 预检，失败再逐条降级
- 正在被 Anki 浏览器/编辑器查看的 note，`updateNote` 不生效（GUI 同步页有提示文案）
- 挖空减少编号产生空卡，AnkiConnect **没有删单卡 API** → 检测后提示用户跑 Anki「工具→空卡片」
- 大批量操作卡 Anki 主线程 → 分批（新增 100/批，更新/移动走 `multi` 50/批），批间查取消标志
- 移动卡组：`changeDeck` 操作的是 cards 不是 notes（notesInfo 拿 cards 列表，整 note 一起移）

### Riff API 是内部接口
未进官方 API 文档（但 2022 至今稳定，已核对 3.8.x 源码路由）。封装在 `siyuan_client.py` 单点；思源升级后先跑 `check` 验证。

## GUI 维护要点

- 所有耗时操作走 `Worker(QThread)`；httpx/SQLite 连接**不可跨线程共享**，工作线程用 `MainWindow.make_clients()` 新建
- 连接检测未通过 → `set_connection_state` 锁定同步页与映射预演
- 日志页按 batch_id 分组，**默认折叠**（用户明确要求）
- 同步页：先预览后执行；执行后旧 plan 置空强制重新预览；删除/超阈值先自动备份
- 表格列宽 Interactive 可拖拽 + 悬停 tooltip（用户要求）

## 开发/验证工作流

```bash
uv run ankilinkers check            # 连接检测（改动客户端后必跑）
uv run ankilinkers sync --dry-run   # 只出计划不写入
uv run ankilinkers sync --notebook <boxID> -y   # 限定笔记本的端到端验证
```

GUI 离屏验证（NixOS 缺 GUI 库时）：
```bash
# /tmp/ankilinkers-qt-libs.txt 可能已被清理，重建方法见 build-dist.sh 的包列表，
# 或：nix-shell -p libglvnd libxkbcommon fontconfig freetype glib dbus zlib xorg.libX11 --run 'echo $NIX_LDFLAGS' 提取 -L 路径拼成冒号分隔
export LD_LIBRARY_PATH=$(cat /tmp/ankilinkers-qt-libs.txt)
QT_QPA_PLATFORM=offscreen uv run python -c "..."   # 实例化页面，widget.grab().save('x.png') 截图验证
```
注意：离屏 `grab()` 需等事件循环稳定（`QTest.qWait(500)`）再截，否则 cellWidget 位置是错的（已踩过）。

端到端测试（真实环境）：用 API 建测试笔记本+文档+制卡（`/api/riff/addRiffCards`，内置卡包 `20230218211946-2kw8jgx`）→ 限定 `--notebook` 同步 → 验证后清理（removeNotebook + Anki 删测试卡组）。

## 打包（NixOS）

```bash
sh scripts/build-dist.sh   # 产物 dist/ankilinkers
```
- nix-shell 提供 objdump 与 Qt 系统库（libGL/libxkbcommon 等），库文件以 `--add-binary` 打进 onefile（onefile 启动时自动加库路径）
- **绝不删除 `dist/` 目录**（`dist/data/` 是用户数据）；脚本只清理 `build/`
- 打包环境变量 NIX_LDFLAGS 自动含传递依赖闭包

## 用户偏好（来自需求迭代）

- 删除策略：同步删除+自动备份；未命中映射进默认卡组；挖空单笔记多编号
- 界面中文；交互逻辑清晰优先；日志默认不展开
- Python 生态 + uv；数据文件跟可执行文件走（便携模式）
- 不确定的需求点先问，给出推荐项（问答确认过 4 个关键决策）

## 提交规范

- git remote：git@github.com:liyuelove/ankilinkers.git（main 分支）
- 提交信息用中文，概括行为变更
- `.gitignore` 已排除：.venv/ build/ dist/ data/ ankilinkers.spec（机器相关中间产物）
