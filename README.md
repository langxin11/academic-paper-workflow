# academic-paper-workflow

为 Antigravity、Claude Code 和 Codex 分发同一版中文文献精读工作流。当前提供 `show`、`sync`、`parse`、`index`、`annotate`。可从 Zotero 附件或本地 PDF 准备材料，按需解析、编写精读笔记、更新索引和写回批注。[定稿 RFC](docs/rfc/0001-academic-paper-workflow.md)

## 从工程目录运行

安装 uv 后，在引擎仓库根目录执行：

```text
uv sync --locked
uv run --locked paper-flow --version
uv run --locked paper-flow show spec
uv run --locked paper-flow sync --scope user --target all --check
uv run --locked paper-flow sync --scope user --target all
```

用户级同步从任意目录运行。只有显式使用 `--scope project` 时才需要指定 Vault，测试用空库可通过创建 `.obsidian/` 识别。预览返回 0 表示已同步，1 表示有待应用的变更，2 表示冲突或错误。应用成功返回 0。首次接入遇到已有不同内容时，先比较本地文件和 `show agent-entry` 或 `show note-template`，将希望保留的修改合并或另存后再同步。第二次同步同一版本可检查是否零写入。

## 从 Vault 启动客户端

每台机器将 `PAPER_FLOW_ENGINE` 设为引擎仓库根目录，再启动客户端。Windows PowerShell 和 Linux shell 从任意目录读取资源的方法如下：

```powershell
uv run --project "$env:PAPER_FLOW_ENGINE" --locked paper-flow show note-prompt
```

```sh
uv run --project "$PAPER_FLOW_ENGINE" --locked paper-flow show note-prompt
```

也可以执行 `uv tool install .` 将当前版本安装为独立命令，再直接使用 `paper-flow`。打包后的资源随 wheel 分发。指定引擎目录的调用使用该目录的锁文件和虚拟环境。

### Vault 选择顺序

1. 全局参数 `--vault`（位于子命令前）。
2. 环境变量 `PAPER_FLOW_VAULT`。
3. 引擎根目录下的 `config.local.yaml`。可复制 `config.example.yaml` 修改。独立安装时用 `PAPER_FLOW_ENGINE` 定位该文件。
4. 从当前目录向上查找最近含 `.obsidian/` 的目录。

### 入口与资源

运行 `paper-flow sync --scope user` 将精读入口安装到当前用户。Codex 读取 `~/.agents/skills/paper-note/`，Claude Code 读取 `~/.claude/skills/paper-note/`，Antigravity 读取 `~/.gemini/config/skills/paper-note/`。同步清单在 `~/.paper-flow/user-manifest.json`。重启客户端后，从任意 Vault 调用精读，入口会给出本机引擎命令。

共享仓库确有项目级需求时，使用 `paper-flow --vault "<Vault>" sync --scope project`。该模式会写入项目入口、正式模板和项目同步清单。

通过 `paper-flow show --help` 查看资源名。常用项包括 `spec`、`note-prompt`、`checklist`、`note-template`、`import-template`、`colors`、`one-pager-prompt` 和 `one-pager-template`。


## 准备 PDF 与 MinerU 全文

从引擎目录运行以下命令。第一条只读本地，第二条在没有可用解析时调用 MinerU 精准 API。要使用云服务，在引擎根目录的 `config.local.yaml` 中填写下列字段。已存在 `parse:` 时只添加其下的 `api_key`。这个本机文件以明文保存 Token，Git 会忽略它。每次调用都会重新读取；如果同时设置了非空的 `MINERU_API_KEY`，优先使用环境变量。

```yaml
parse:
  api_key: ""
```

```text
uv run --locked paper-flow parse --attachment-key <附件key> --json
uv run --locked paper-flow parse --pdf "<PDF路径>" --cloud --json
```

- 默认在 `~/Zotero/storage/<附件key>/` 定位唯一 PDF，可用 `--zotero-data-dir` 指定含 `storage/` 的目录。多个 PDF、父条目或链接附件请用明确的 `--pdf`。
- 先查引擎缓存，再查附件附近的 `mineru/`、`--results-dir` 下按 PDF SHA-256 或附件 key 命名的目录，以及 llm-for-zotero 插件的来源记录。插件复用会比对附件 key、文件名及源摘要或解析时间。多份结果用 `--materials <该论文的结果目录>` 明确选择。外部旧结果通常缺少参数记录，非默认解析参数只自动匹配引擎自己的缓存。
- 本地没有全文时返回 `original-pdf`。成功复用外部全文、命中引擎缓存、完成云端下载时分别返回 `existing`、`cache`、`mineru-cloud`。JSON 包含 PDF 路径、指纹、页数、`full_md` 和可用的 `images_dir`。图像路径同时从 Markdown 和 MinerU 的 content_list.json 中核验。默认缓存位于 `~/.cache/paper-flow/mineru/`，可用 `--cache-dir` 覆盖。
- 云解析以 PDF 指纹和解析参数区分任务。退出码 3 表示等待超时或连接中断，使用相同输入和参数重跑可续查原批次。默认最长等待 600 秒，`--wait-seconds` 可调整。任务状态、缓存和命令输出均不会保存 Token 或签名 URL。若服务仍在等待上传，但上次进程已丢失签名 URL，CLI 会提示用 `--replace-batch` 重新申请。成功退出码为 0，输入、服务拒绝或校验错误为 2。
- `--skip-existing` 跳过外部解析，仍可命中引擎缓存。下载限制为 512 MiB；ZIP 先检查条目路径、大小及 CRC，再校验全文和本地图像。校验通过后才发布新的缓存版本。下次命中前核对内容摘要。

## 笔记索引

```text
paper-flow --vault "<Vault>" index --check
paper-flow --vault "<Vault>" index
paper-flow --vault "<Vault>" index --note "文献研究/文献笔记/论文.md" --guide "可检索的核心结论与适用条件"
paper-flow --vault "<Vault>" index --note "文献研究/文献笔记/论文.md" --backlog "等待核查附录实验条件"
```

自动台账默认扫描 `文献研究/文献笔记/`，生成 `文献研究/主题综述/文献笔记总索引（自动生成）.md`。受管区域之外保留人工内容。首次遇到没有受管标记的既有页，先保留快照或显式划定由工具维护的正文范围，再运行。人工检索入口须已存在；`--index` 可指定已有专题页。已有人工导读保持原文，受管条目随导读或待办状态更新。自动台账与单篇登记各自报告结果。`--check` 的退出码沿用同步约定。

## PDF 批注

```json
{
  "page_base": 1,
  "annotations": [
    {
      "id": "mechanism-1",
      "page": 3,
      "quote": "exact consecutive words from the source",
      "color": "green",
      "comment": "解释该段支持的机制或结论"
    }
  ]
}
```

`page_base: 1` 表示物理页序从封面开始；旧脚本的数组输入继续使用零基页码和 `search/content`。先用完整引文运行两条命令中的第一条，再核对结果并执行第二条：

```text
paper-flow annotate --pdf "<论文.pdf>" --json "<计划.json>" --check
paper-flow annotate --pdf "<论文.pdf>" --json "<计划.json>" --backup-dir "<本轮备份目录>"
```

唯一命中才能通过整批预检。同页重复引文可用紧邻的 `before`、`after` 或未旋转页面坐标 `region: [x0, y0, x1, y1]` 消歧（左上角为原点，单位为 PDF 点）。支持栏内同一文本块的多行引文及换行断词；扫描页和跨文本块引文需要另选可核对的文本。预检通过返回 0，定位或写回错误返回 2。每次实际修改都验证临时副本、核对手工批注和源文件摘要，保留写入前的独立备份，再原子替换。原样重跑不会新增批注或备份。默认在 PDF 旁保存带唯一编号的 `.bak`；可配置 `backup_dir` 或用命令行参数存到 Vault 外。恢复时可先将该轮备份复制为单独的 PDF，打开确认后再决定是否替换。写入的是 PDF 内嵌批注。

## Vault 中的工作流文件

用户级同步把模板、规范和同步清单保存在 Vault 外。课程模板和 Zotero 导入属于本地工具，本机部署于引擎的 `local/vault-profile/`，测试记录位于 `local/qa/`。Git 忽略整个 `local/`。Obsidian 自身使用 `.obsidian/` 保存设置。启用原生模板并使用其“插入模板”命令时，模板选择器只列出库内文件。


## 验证

在引擎目录执行 `uv run --locked python -m unittest discover -s tests -v`。集成测试在临时目录创建测试 Vault，覆盖入口与模板、路径选择、台账与人工登记、双栏引文定位、写入失败恢复和重复运行。
