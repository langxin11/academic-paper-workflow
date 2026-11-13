# RFC 0001 · academic-paper-workflow 独立化与跨 Agent 适配

**状态**　第一至第三阶段已通过实施验收。第三阶段已用 Yan 2024 实测 MinerU 官方精准解析、全文与配图校验和离线缓存复用。

**日期**　2026-11-11  
**目标客户端**　Antigravity、Claude Code、Codex  
**核心组件**　Python、uv、PyMuPDF、MinerU 精准解析 API

## 1. 目标和物理边界

让三个客户端在不同设备上使用同版文献精读方法，并能从笔记里的关键结论追溯到原文图表、公式与实验条件。工作流覆盖材料定位、按需解析、精读笔记、PDF 批注、索引登记和按需生成的一图流。

引擎 Git 仓库与个人 Obsidian Vault 使用独立物理目录。引擎维护通用规约、模板、代码和锁文件；Vault 维护私人笔记、正式图片与人工索引。个人部署使用三个客户端的用户级入口；共享项目可按需采用第 7 节的项目级同步。两库采用外部 CLI 和普通文件交互，避免 submodule 和符号链接。Python 环境、解析缓存、ZIP、凭据与机器配置留在 Vault 之外，个人论文和笔记也不进入引擎 Git 历史。

## 2. 规约归属和仓库结构

| 计划路径 | 职责 |
| --- | --- |
| `core/spec.md` | v3.1 精读证据要求、阅读范围、作者陈述与【笔记推断】的区分、完成状态 |
| `core/color_system.md`、`core/checklist.md` | 六色语义和色值的权威定义，及面向精读成果的质量验收项 |
| `core/prompts/` | 精读和一图流任务步骤，引用相应规约 |
| `templates/obsidian/` | 正式十段式模板的标题、首屏看板及元数据；另保留 Zotero 快速导入草稿模板 |
| `templates/typst/` | 可编辑的 16:9 一图流模板 |
| `src/academic_paper_workflow/` | `paper-flow` 入口、材料定位、MinerU 客户端、批注、笔记写入、索引与同步 |
| `pyproject.toml`、`uv.lock`、`.python-version` | CLI 入口、依赖锁定和项目 Python 版本 |
| `config.example.yaml` | 可公开的占位示例；每台机器自行维护忽略入库的 `config.local.yaml` |

规范负责证据标准，模板负责布局，Vault 配置负责专题别名、输出目录与索引入口。发布包携带对应版本的资源，CLI 和各端提示词读取同一来源。历史笔记保持原有组织，正式模板升级作用于新笔记。

## 3. uv 运行方式与寻址

在引擎仓库内使用 `uv run --locked paper-flow …`。从 Vault 或其他目录启动 Agent 时，以 `--project` 指定引擎。以下为已实现的接口；尖括号表示由本机配置或调用者提供的值。

```text
uv run --project "<引擎目录>" --locked paper-flow --vault "<Vault目录>" sync --target all --check
uv run --project "<引擎目录>" --locked paper-flow --vault "<Vault目录>" sync --target all
uv run --project "<引擎目录>" --locked paper-flow parse --pdf "<PDF路径>" --cloud --json
uv run --project "<引擎目录>" --locked paper-flow parse --attachment-key "<附件key>"
uv run --project "<引擎目录>" --locked paper-flow annotate --pdf "<PDF路径>" --json "<批注JSON>"
uv run --project "<引擎目录>" --locked paper-flow --vault "<Vault目录>" index
```

需要 Vault 的命令按以下优先级选择目标：显式 `--vault` → `PAPER_FLOW_VAULT` → 引擎本地 `config.local.yaml` → 从当前工作目录向上查找最近的 `.obsidian`。显式选定的目录无效时报告错误，避免回退后误写另一个库。只处理 PDF 的命令可单独运行。Vault、Zotero 数据目录、既有 MinerU 结果、引擎缓存和备份位置分别配置；分发到 Vault 的入口保持机器无关。

## 4. 一篇论文的端到端流程

1. 默认接受自然语言“精读《论文标题》”，由三个客户端用 `paper-flow parse --title "<题名>" --cloud --json` 从 Zotero 选择唯一的书目和 PDF。近似命中、重名或多个 PDF 时先展示候选。确认后执行精读、写笔记与配图、登记索引并返回 Obsidian 链接；只有显式请求 PDF 批注才写回原件。也可接收明确 PDF 路径或 Zotero 附件 key。已存储附件优先使用指定数据目录，缺省探测 `~/Zotero/storage/<附件key>/`。父文献 key 和附件 key 有不同含义。遇到父条目、多个候选或链接附件时，首版提示传入明确的附件 key 或实际 PDF 路径。
2. 从已配置的 MinerU 结果目录和引擎缓存复用相应论文的 `full.md` 与图像。来源有歧义时返回候选。缺少解析结果时按第 5 节策略处理，离线阅读仍可使用原 PDF。
3. Agent 读取对应版本的规约与正式模板。Citation 后给出核心启发、复用价值和阅读范围，正文依次说明问题、背景、核心方法、关键实验、Claim ↔ Evidence、图像速查、公式速查、局限与创新切入点、研究用途及最终评价。重要数据与公式回查原 PDF，标出图表号、公式号或页码。§4 记录实测数据，§5 检视论证。阅读者的延伸判断标注【笔记推断】。
4. 关键图片复制到笔记同级 `assets/<paper-slug>/`，使用相对链接。核心证据有缺口时保留 `in-progress`；限定阅读范围内核实闭环后才标记 `done`。导入草稿、空白模板和解析成功均不触发精读完成状态。
5. 自动索引从 frontmatter 重建专用总台账，兼容正式和历史笔记。人工专题索引及检索入口按条目登记双链与导读，待确认项进入“待补收录”。正文写入与索引登记分别报告。需要一图流时，复用已核查的证据、图像和 Typst 模板。

## 5. MinerU 自动解析与断点续查

本机可配置“仅使用本地材料”或“缺失时自动云解析”。优先复用已有材料。确需上传时，从非空的 `MINERU_API_KEY` 或本机 `config.local.yaml` 的 `parse.api_key` 读取 Token，环境变量优先。本机配置属于 Git 忽略项。任务状态、分发包、笔记及普通日志均不包含 Token 或临时签名 URL。

| 阶段 | 协议和恢复约定 |
| --- | --- |
| 申请 | 本地文件使用 `POST /api/v4/file-urls/batch`，单文件也走该接口。取得结果后立即记录输入 PDF 指纹、解析参数、阶段、`batch_id` 及文件的 `data_id`。 |
| 上传与查询 | 将原始字节 PUT 到对应签名 URL，上传完成后服务端自动解析。用 Bearer 凭据查询 `GET /api/v4/extract-results/batch/{batch_id}`，按文件读取状态。 |
| 等待与恢复 | 客户端默认从 5 秒开始轮询，逐步延长至 30 秒；每次网络请求单独设超时，前台等待默认 10 分钟。超时、断网或 Ctrl+C 时保留本地状态，再运行同一输入时优先续查原批次。`waiting-file` 返回上传阶段；`pending`、`running`、`converting` 继续等待；`failed` 返回服务端原因。 |
| 下载与发布 | `done` 后读取该文件的 `full_zip_url`，下载并解压到独立临时目录。校验归档成员的解压目标均在该目录内，检查 `full.md` 与引用的本地图像可读，随后发布为有效缓存。下载或解压中断支持重取结果，旧缓存继续可用。 |

新缓存默认独立于 Zotero 管理的附件目录，按输入指纹和解析参数区分版本。兼容已有插件在 Zotero 周边生成的解析材料。笔记只引用复制进 Vault 的正式资产。

## 6. PDF 内嵌批注与可恢复写回

输出为 PDF 内嵌下划线及附带评论。Zotero 原生数据库批注的导入和同步属于单独功能。批注输入含页码、完整引文、语义色、评论和稳定 ID，可补充前后文或区域。明确页码基准，兼容旧脚本的零基页码，并在预检中展示对应的 PDF 物理页序。

| 色名 | 学术语义 |
| --- | --- |
| amber | 背景与研究动机 |
| red | 核心矛盾、痛点与物理硬伤 |
| green | 核心机制、创新贡献与主实验结果 |
| blue | 硬件拓扑、通信与时延 |
| purple | 核心公式、控制律与损失函数 |
| orange | 核心假设、客观局限与工程避坑 |

先预检整批引文。双栏论文按栏或文本块限定候选，结合完整引文、规范化断词及前后文定位。允许一个引文跨行形成多个区域。仅命中句首或同页存在多处候选时，报告实际匹配范围，保留原件供复核。

在同目录唯一临时副本中，按稳定 ID 更新引擎拥有的批注，保留用户手工批注。覆盖前备份**本轮开始时的文件**，重新打开并验证输出，确认源文件自预检以来未变化，再原子替换。首次创建的 `.pdf.bak` 可保留为原始档，后续覆盖仍须能恢复到当轮之前。备份、验证或替换失败时原件保持可用。

## 7. 跨 Agent 分发与多设备同步

个人 Vault 采用用户级入口。每台机器运行 `paper-flow sync --scope user`，引擎记录本机资源读取命令及入口文件摘要。精读规范、模板和脚本保存在 Vault 外。个人 Vault 的补充规约保存在私有目录，由三个客户端的用户规则按目标路径引用。

| 客户端 | 用户级精读入口 |
| --- | --- |
| Antigravity | `~/.gemini/config/skills/paper-note/SKILL.md` |
| Claude Code | `~/.claude/skills/paper-note/SKILL.md` |
| Codex | `~/.agents/skills/paper-note/SKILL.md` |

同步清单位于 `~/.paper-flow/user-manifest.json`。默认 `sync` 使用用户级模式。共享项目仍可显式运行 `paper-flow --vault "<Vault>" sync --scope project` 生成库内入口、模板与项目清单。独立安装的一图流技能保留配套样图和模板，精读入口同步只管理自己的文件。

`sync --check` 预览新增、更新及本地冲突。以已安装版本和受管文件清单为准，用上次分发的内容摘要识别本地修改；首次接入若已存在不同内容，展示差异供合并。文本统一 UTF-8 和 LF。内容相同跳过写入，升级采用临时文件替换。换行一致性和多设备编辑冲突分别处理。

## 8. 首版实施与验收

已建立 uv 包、可分发的规范与模板，以及三个用户级客户端入口。第一、第二阶段已在测试 Vault 验证同篇精读、索引和 PDF 写回。第三阶段已接入附件 key 寻址、本地材料与指纹缓存复用、v4 批次上传、断点续查、ZIP 验证及原子发布。一图流按需触发，MCP 和其他客户端留待出现具体需求时评估。

| 验收场景 | 通过条件 |
| --- | --- |
| 跨 Agent 精读 | 三个客户端分别从测试 Vault 启动，以同版规约精读同一篇双栏论文；关键结论可追溯，正式图片相对链接可打开。 |
| 两库隔离 | 从 Vault 调用后，Python 环境、缓存、ZIP 和机器配置均在预期外部位置。 |
| 解析恢复 | 已有解析直接复用；上传后中断可续查原批次；损坏下载不被标为有效缓存。 |
| 批注安全 | 手工批注保留，重复执行零叠加；歧义引文和模拟写入失败均保留原件，可恢复本轮之前的版本。 |
| 同步升级 | 连续同步两次，第二次写入零个文件；本地修改可看到差异并得到保留。 |
| 笔记与索引 | 新笔记符合正式模板，证据缺口可见，旧笔记仍可查找，自动重建台账保留人工阅读路线。 |

### 第三阶段实施记录（2026-11-12）

- Yan 2024 附件 `SJ552ALW` 在测试 Vault 中解析为一份 21 页 PDF。Zotero 插件来源记录与附件匹配，命令复用既有 MinerU 全文及引用图片；原 PDF 保持原摘要。离线缺少材料时验证返回原 PDF。
- 在本地 HTTP 协议测试中，使用同一 PDF 字节完成一次申请和一次上传；前台中断后从原批次取得结果 ZIP，重复命中缓存时没有新增请求。另验证了损坏 ZIP、越界条目和无效图片均不发布为有效缓存。中断恢复和错误注入使用本地测试服务验证。
- 官方云端验收使用同一附件 `SJ552ALW`，在测试 Vault 跳过插件结果，向 MinerU v4 精准解析服务提交新批次（pipeline）。服务返回 done，CLI 返回 mineru-cloud。全文可按 UTF-8 打开，包含论文题名、引言与参考文献。内容清单覆盖全部 21 页。Markdown 引用了 40 张不同图片，结合内容清单共 96 张，均已打开并解码。使用同一附件和参数执行离线复查，返回 cache；又校验了缓存清单的 102 个文件。原 PDF 的 SHA-256 与前两轮验收一致。
- 已确认上传的任务继续查询原批次。服务返回 `waiting-file` 时，进程内仍持有的上传地址可重试。跨进程无法从公开 API 取回原签名上传地址；CLI 提示显式 `--replace-batch`，避免无提示重复申请。

实现以这些可复现的结果决定完成度。主要通过共享规约、复用安装入口和统一解析流程控制代码规模。

## 9. 接口依据

- [MinerU 精准解析 API](https://mineru.net/doc/docs/)
- [Antigravity Skills](https://antigravity.google/docs/skills) · [Codex Skills](https://learn.chatgpt.com/docs/build-skills) · [Claude Code Skills](https://code.claude.com/docs/en/skills)
- [uv run 与项目选择](https://docs.astral.sh/uv/reference/cli/#uv-run)
- [Zotero 附件类型](https://www.zotero.org/support/attaching_files) · [Zotero 数据目录](https://www.zotero.org/support/zotero_data) · [Zotero 原生批注](https://www.zotero.org/support/kb/annotations_in_database)
