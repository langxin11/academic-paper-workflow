# academic-paper-workflow

为 Antigravity、Claude Code 和 Codex 分发同一版中文文献精读工作流。第一阶段提供 `paper-flow show` 和 `paper-flow sync`。可直接用本地 PDF、已解析的 Markdown 和正式模板开展精读。自动云解析、PDF 写回和索引重建属于后续阶段。[定稿 RFC](docs/rfc/0001-academic-paper-workflow.md)

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

## 验证

在引擎目录执行 `uv run --locked python -m unittest discover -s tests -v`。集成测试在临时目录创建测试 Vault，覆盖入口、模板、路径选择、预览、重复同步和本地编辑冲突。
