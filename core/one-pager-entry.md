---
name: paper-one-pager
description: >-
  将已有文献精读笔记或论文材料制作成中文学术一图流（one-pager / graphical abstract），
  用于首屏复盘、3分钟组会汇报或跨论文对照。触发于“给这篇精读做一图流”“论文单页速览”
  “制作一图流”“one-pager”“graphical abstract”，以及高价值闭环精读的自动触发场景。
  默认生成可编辑 Typst 源码、SVG 与 PNG，并按规则嵌入笔记首屏。
---

# 文献一图流入口

把一篇论文压缩成一张可讲清“信号如何变成行动，证据支持到哪里”的 16:9 页面。展示页不是证据来源，内容严格忠于已核查的原论文与精读笔记，不继承示例模板或视觉参考中的物理假设与数值。

先遵循目标 Vault 的项目指令（如有本地视觉参考或组会发布约定，一并按需加载）。本入口通过 CLI 读取已安装版本的通用资源；入口所在目录不含完整资源，不拼接不存在的相对路径。

用户级入口末尾附有本机启动指令，可在任意目录使用该指令运行下文的 `paper-flow` 子命令。若 `paper-flow` 已安装在 PATH，也可直接调用；手动配置的环境可以使用 `PAPER_FLOW_ENGINE` 指定引擎项目：

```powershell
uv run --project "$env:PAPER_FLOW_ENGINE" --locked paper-flow show one-pager-prompt
```

```sh
uv run --project "$env:PAPER_FLOW_ENGINE" --locked paper-flow show one-pager-prompt
```

## 加载资源

制作一图流前读取：

```text
paper-flow show spec
paper-flow show one-pager-prompt
paper-flow show one-pager-template
paper-flow show checklist
```

## 执行与交付要点

1. **复用已核查笔记或先精读**：优先复用已有精读笔记、已核查证据与库内图片；只有影响本页结论的缺口才回查原 PDF 或解析全文。若用户仅提供论文标题、PDF 或附件 key 且要求同时精读与制图，先按 `paper-note`（`paper-flow parse` 与 `paper-flow show note-prompt`）完成精读核查，再生成一图流。
2. **先内容后版式**：按 `one-pager-prompt` 在输出目录写 `one-pager-content.md` 保留出处、条件、LaTeX 原式、符号与缺口；再保存并修改 `one-pager.typ`，使用本机 Typst 编译 `one-pager.svg` 与 `one-pager.png`（1920×1080）。缺编译器或字体时保留源码并明确说明未成图。
3. **归档与首屏嵌入**：默认归档于笔记同级 `assets/<paper-slug>/`（含 `.typ`、`.svg`、`.png`、`one-pager-content.md` 及引用原图）。随精读条件自动触发或用户要求融入笔记时，在 Citation、核心启发看板与阅读范围之后插入或更新 `> [!NOTE] 文献一图流速览` Callout（仅明确“只导出图片”时不改动笔记）。
4. **状态与索引边界**：一图流不改变原笔记 `status`，关键缺口未关闭时在图上标明“阅读草稿／关键证据待核查”；不因制图重复更新文献索引。
