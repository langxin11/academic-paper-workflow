<!-- Zotero 插件快速导入草稿，保留插件字段和批注循环语法；并非通用模板渲染器。正式精读读取 paper-flow show spec 与 paper-flow show note-template。导入不代表证据核查完成，parse_route 在实际阅读后更新。 -->

---
citekey: {{citekey}}
title: {{title}}
authors: {{authorsString}}
year: {{year}}
publication: {{publicationTitle}}
doi: {{DOI}}
tags: {{allTags}}
zotero_link: {{desktopURI}}
pdf_link: {{pdfZoteroLink}}
source: zotero
parse_route: original-pdf # 导入仅声明原始材料；实际阅读时改为真实路线
status: unread
project:
created: {{date}}
---

# {{title}}

## Citation
> {{authorsString}} ({{year}}). {{title}}.

## Summary
-

## Key Figures
- Figure:
  Why it matters:

## Key Equations
- Equation:
  Meaning:
  Why it matters:

## One-Sentence Take
-

## Research Question
-

## Core Idea
-

## Methods
-

## Key Results
-

## Limitations
-

## Usefulness
-

## My Take
-
-
-

## Evidence From Annotations
{% for annotation in annotations %}
- {{annotation.annotatedText}}{% if annotation.comment %}\
  Comment: {{annotation.comment}}{% endif %}
{% endfor %}

## Action Items
-

## Related Notes
-
