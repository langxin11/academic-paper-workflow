---
name: paper-note
description: >-
  精读论文并制作中文 Obsidian 文献笔记；触发于“精读《论文标题》”“精读X”“对X形成笔记”
  “给X做笔记”“处理这篇文献”“close reading”“read and take notes”；
  也适用于“给PDF加批注”“划线”“标注原文”的证据与语义规划，
  以及按需“文献一图流”“论文单页速览”。使用本地 PDF 或 Zotero/MinerU 材料，
  逐项核查证据、区分【笔记推断】并维护人工索引。支持可预检的六色 PDF 内嵌批注。
---

# 文献工作流入口

工作流面向 Agent，笔记和批注面向读者。写图注或准备 PDF 划线时，按共享规约（`paper-flow show spec`）的“写给谁”一节核对。用户要求给 PDF 加批注时，默认多标几处有独立阅读价值的原句，让读者能沿着划线复盘方法、证据与边界。

先遵循目标 Vault 的项目指令确定输出目录与索引。本入口通过 CLI 读取已安装版本的通用资源；入口所在目录不含完整资源，不拼接不存在的相对路径。机器私有路径与凭据只放在本机环境/配置，按引擎 README 设置。

用户级入口末尾附有本机启动指令，可在任意目录使用该指令运行下文的 `paper-flow` 子命令。若 `paper-flow` 已安装在 PATH，也可直接调用；手动配置的环境可以使用 `PAPER_FLOW_ENGINE` 指定引擎项目：

```powershell
uv run --project "$env:PAPER_FLOW_ENGINE" --locked paper-flow show spec
```

```sh
uv run --project "$PAPER_FLOW_ENGINE" --locked paper-flow show spec
```

三种启动方式都不可用时，说明缺少本机引擎入口。需要目标 Vault 的操作使用显式 `--vault`、本机 `PAPER_FLOW_VAULT` 或配置选择，避免误写别的库。

## 默认按题名精读

收到“精读《论文标题》”时，取出书名号中的题名，运行 `paper-flow parse --title "<论文标题>" --cloud --json`。先复用已校验材料，缺少时调用 MinerU。用户指定离线时改用 `--local-only`。唯一命中并且 PDF 可用就继续；有多个候选时列出题名、年份、DOI 或文件名请用户选择。拿到明确的版本之前暂停解析和写入。用户已提供 PDF 或附件 key 时直接走相应入口。

读取精读提示词，核对原 PDF；按返回的 `vault` 和 `notes_dir` 落盘完整中文笔记和相对路径配图。按 DOI、条目 key 或题名查重并保留已有人工内容。登记人工索引，刷新自动台账，验证图片及索引双链后，发送可点击的 Obsidian 笔记链接。普通精读对原 PDF 只读；用户明确说“给 PDF 加批注”“划线”或同义要求时，才预检、备份和写回下划线与评论。

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

写完笔记后用 `paper-flow --vault "<Vault>" index --note "<笔记的 Vault 相对路径>" --guide "<可检索的一句话导读>"` 登记人工入口并重建台账。需要写回 PDF 时先读色表及 `paper-flow annotate --help`，准备完整引文和稳定 ID，在目标 PDF 上运行 `annotate --pdf "<PDF>" --json "<计划>" --check`，核对定位后以相同参数去掉 `--check` 提交。按实际结果报告备份、跳过或失败项。详见 `paper-flow show note-prompt`。明确指定来源时可用 `parse --attachment-key <附件key>` 或 `parse --pdf <PDF>`，沿用同一材料复用策略。
