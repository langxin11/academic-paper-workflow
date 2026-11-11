---
name: paper-note
description: >-
  精读论文并制作中文 Obsidian 文献笔记；触发于“精读X”“对X形成笔记”
  “给X做笔记”“处理这篇文献”“close reading”“read and take notes”；
  也适用于“给PDF加批注”“划线”“标注原文”的证据与语义规划，
  以及按需“文献一图流”“论文单页速览”。使用本地 PDF 或 Zotero/MinerU 材料，
  逐项核查证据、区分【笔记推断】并维护人工索引。首版不提供自动 PDF 写回。
---

# 文献工作流入口

先遵循目标 Vault 的项目指令确定输出目录与索引。本入口通过 CLI 读取已安装版本的通用资源；入口所在目录不含完整资源，不拼接不存在的相对路径。机器私有路径与凭据只放在本机环境/配置，按引擎 README 设置。

若 `paper-flow` 已安装在 PATH，直接运行下列命令。否则以本机环境变量 `PAPER_FLOW_ENGINE` 指定引擎项目，在任意工作目录使用以下前缀（把最后的资源名替换为所需项）：

```powershell
uv run --project "$env:PAPER_FLOW_ENGINE" --locked paper-flow show spec
```

```sh
uv run --project "$PAPER_FLOW_ENGINE" --locked paper-flow show spec
```

变量为空或项目不存在时先说明本机设置缺失；不猜测引擎或 Vault 的绝对路径。需要目标 Vault 的操作使用显式 `--vault`、本机 `PAPER_FLOW_VAULT` 或配置选择，避免误写别的库。

## 按需加载资源

正式精读开始前读取：

```text
paper-flow show spec
paper-flow show note-prompt
paper-flow show checklist
paper-flow show note-template
```

涉及批注语义时读取：

```text
paper-flow show colors
```

仅在用户请求一图流时读取：

```text
paper-flow show one-pager-prompt
paper-flow show one-pager-template
```

按 note-prompt 完成实际材料核对、证据清单、十段笔记、原文复核和人工索引；图片存笔记同级相对 `assets/<paper-slug>/`。旧笔记保留组织。关键证据未闭环保留 `in-progress`，限定范围核实后才能 `done`；AI分析标 **【笔记推断】**。交付分别报告正文与索引结果。

当前阶段的 `show`/`sync` 管理资源与入口。材料自动定位、MinerU 云解析、自动索引、PDF 批注写回及 Zotero 原生批注同步尚属后续实现，不调用不存在的命令或 Vault 私有脚本。用户请求批注时可生成语义与引文计划，明确是否真正修改 PDF；安全约定见 spec、checklist 和 note-prompt。
