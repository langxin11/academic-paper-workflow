// Self-contained layout example; requires Typst and an installed CJK font.
// No packages, private references, external images or network dependencies.
// Layout demonstration. Replace all example content with sourced paper content.
#set page(width: 960pt, height: 540pt, margin: 0pt)
#set text(
  font: ("Noto Sans SC", "Microsoft YaHei"),
  size: 14pt,
  lang: "zh",
  fill: rgb("222222"),
)
#set par(leading: 0.35em, spacing: 0pt)
#let green = rgb("126344")
#let pale = rgb("f0f6f2")
#let at(x, y, w, body) = place(top + left, dx: x, dy: y, block(width: w, body))
#let card(x, title, body) = at(x, 120pt, 200pt, block(
  height: 216pt,
  inset: 14pt,
  fill: pale,
)[
  #text(size: 18pt, weight: "bold", fill: green)[#title]
  #v(13pt)
  #body
])
#let bottom(x, title, body, color: green) = at(x, 400pt, 274pt)[
  #text(size: 17pt, weight: "bold", fill: color)[#title]
  #v(15pt)
  #text(size: 14pt)[#body]
]

#at(44pt, 25pt, 872pt)[#text(
  size: 26pt,
  weight: "bold",
)[文献一图流：用方法链讲清机制、证据与边界]]
#at(44pt, 78pt, 872pt)[#text(
  size: 14pt,
  fill: rgb("6b7274"),
)[版式示范 · 正式使用时替换为作者年份、英文原题与期刊会议]]

#at(44pt, 126pt, 190pt)[
  #text(size: 18pt, weight: "bold", fill: green)[感知 / 输入]
  #v(16pt)
  #block(
    width: 176pt,
    height: 120pt,
    inset: 12pt,
    stroke: 0.7pt + rgb("b8c7be"),
  )[
    #text(fill: green)[原论文图的位置]
    #v(12pt)
    实际使用时放硬件、传感器或系统原图。
  ]
  #v(10pt)
  #text(size: 13pt)[图号 · 测量量 · 关键部件]
]
#card(258pt, [建模 / 辨识])[
  *先说清输入和输出*
  #v(12pt)
  用参数把接触现象变成可计算关系。
  #v(12pt)
  #text(fill: green)[参数、假设与适用范围]
  #v(12pt)
  记录辨识数据来源；与在线状态估计区分。
]
#card(482pt, [算法 / 决策])[
  *直觉在前，公式在后*
  #v(10pt)
  偏差如何改变决策？
  #v(8pt)
  #text(size: 18pt, fill: green)[$ e = r - y $]
  #text(size: 12pt)[此式仅示范排版：参考值减输出。正式页替换为论文核心式。]
  #v(8pt)
  标明式号、信号与更新率。
]
#card(706pt, [执行 / 反馈])[
  *命令如何改变对象？*
  #v(12pt)
  说明执行响应、约束和实际反馈量。
  #v(12pt)
  把控制更新率与实际响应速度分开。
  #v(12pt)
  开环论文改为验证链。
]
#for x in (225pt, 461pt, 685pt) {
  at(x, 213pt, 22pt, text(size: 22pt, fill: green)[→])
}
// Add a feedback path only when supported by the paper; see the one-pager prompt.

#bottom(44pt, [验证证据])[
  *关键数值 + 单位 + 测试条件* \
  分清拟合结果与独立测试结果。
]
#bottom(342pt, [成立边界], color: rgb("884827"))[
  核心假设、失效场景与代价。 \
  读者推导的边界标明笔记推断。
]
#bottom(640pt, [对本课题的启发])[
  【笔记推断】可复用机制。 \
  提出一个能实际执行的验证动作。
]
#at(44pt, 509pt, 872pt)[#text(
  size: 10pt,
  fill: rgb("6b7274"),
)[版式示范，非论文结论 · 正式页脚：作者 年份 · DOI · 关键图号 / 公式号]]
