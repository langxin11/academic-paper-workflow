# academic-paper-workflow

为 Antigravity、Claude Code 和 Codex 分发同一版中文文献精读工作流。当前提供 `show`、`sync`、`index`、`annotate`。使用本地 PDF 或已解析材料开展精读，按笔记更新索引，并将核查过的引文安全写回 PDF。MinerU 云解析属于后续阶段。[定稿 RFC](docs/rfc/0001-academic-paper-workflow.md)

## 从工程目录运行

安装 uv 后，在引擎仓库根目录执行：

```text
uv sync --locked
uv run --locked paper-flow --version
uv run --locked paper-flow show spec
uv run --locked paper-flow --vault "<目标 Vault>" sync --target all --check
uv run --locked paper-flow --vault "<目标 Vault>" sync --target all
```

目标目录应是已有 Obsidian Vault，测试时可新建空目录并创建 `.obsidian/`。预览返回 0 表示已同步，1 表示有待应用的变更，2 表示冲突或错误。应用成功返回 0。首次接入遇到已有不同内容时，先比较本地文件和 `show agent-entry` 或 `show note-template`，将希望保留的修改合并或另存后再同步。第二次同步同一版本可检查是否零写入。

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

| 目标 | Vault 内的项目入口 |
| --- | --- |
| Antigravity、Codex | `.agents/skills/paper-note/SKILL.md` |
| Claude Code | `.claude/skills/paper-note/SKILL.md` |

`--target all` 安装上述两个入口，单独选择 `codex`、`antigravity` 或 `claude` 只安装所需入口。每次分发均包含正式笔记模板 `Templates/文献精读模板.md`。同步清单记录分发内容的摘要，用于识别手工修改。重载对应客户端的项目技能后，可用“精读这篇论文”调用。

通过 `paper-flow show --help` 查看资源名。常用项包括 `spec`、`note-prompt`、`checklist`、`note-template`、`import-template`、`colors`、`one-pager-prompt` 和 `one-pager-template`。


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

规范和精读模板随引擎分发，`show note-template` 可以完全从 Vault 外读取。当前项目级 `sync` 会安装 `Templates/文献精读模板.md`、项目技能及 `.paper-flow/` 状态。希望 Vault 只保存知识资产时，可在外部工作区使用引擎，通过 `--vault` 定向写笔记、图片和索引；若要在 Vault 中直接发现技能，应先在每台机器部署用户级技能。项目级同步保留原有行为。Obsidian 自己的配置目录、原生模板插件对库内模板的需求，以及课程与 Zotero 等其他模板，需另行迁移。

## 验证

在引擎目录执行 `uv run --locked python -m unittest discover -s tests -v`。集成测试在临时目录创建测试 Vault，覆盖入口与模板、路径选择、台账与人工登记、双栏引文定位、写入失败恢复和重复运行。
