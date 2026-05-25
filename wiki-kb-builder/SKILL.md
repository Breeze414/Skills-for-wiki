---
name: wiki-kb-builder
description: >-
  通用 wiki-kb 建页与建链 skill。用于把一批 raw 资料自动整理为 wiki 专题页、候选 related、旧页回链建议、index/log 更新与审计报告。
  触发词包括："wiki入库整理", "批量建wiki页", "raw生成专题页", "给知识库补链接", "更新wiki索引", "更新wiki日志", "知识整理", "知识蒸馏", "raw转wiki", "专题页生成", "wiki互链", "wiki回链"。
---

# Wiki-KB Builder

## 一句话定位

这是给代理自己用的知识整理 skill，不是给用户手敲命令用的。
目标是把 raw 资料整理成 wiki 页面、互链、index/log 和审计报告。

---

## 最短操作手册

### 1. 先判定用户要的是哪类任务

只允许落到四类任务之一：

- `ingest`：把一批 raw 整理成专题页建议或落库
- `health-check`：只做全库体检，不改内容
- `repair`：做全库修复
- `test`：跑回归验证

如果用户表达模糊，先别自己拍脑袋全做。
要先把意图归到这四类之一。

---

### 2. 你该问什么，不该问什么

#### A. 用户说“整理这批 raw / 生成专题页 / raw 转 wiki”
归类为 `ingest`。

你必须确认的只有：
- raw 批次是哪一批
- 是先看建议，还是直接落库

如果用户没说模式，必须主动用选择题确认：
- 只看建议
- 安全落库
- 完整落库

不要要求用户记 `suggest-only / safe-apply / full-apply` 这些英文名。
你自己在内部映射：
- 只看建议 → `suggest-only`
- 安全落库 → `safe-apply`
- 完整落库 → `full-apply`

如果用户没给 topic hints，可以：
- 先根据 raw 标题和上下文自己提一版
- 或给用户一个很短的确认问题
不要一上来让用户自己写整套命令。

#### B. 用户说“检查 wiki 有没有断链 / 看下知识库健康度 / 先体检”
归类为 `health-check`。

通常不需要再追问。
直接执行即可。
除非用户同时要求修复，否则默认只体检不改库。

#### C. 用户说“修一下 related / 补回链 / 修 index/log / 全库修复”
归类为 `repair`。

你必须确认的只有一件事：
- 只是修最明显的问题，还是允许做较完整修复

若用户没说明，默认先按较保守方式执行。
如果修复范围可能触达较多旧页，先明确提醒风险，再执行。

#### D. 用户说“测一下这个 skill 还通不通 / 跑回归”
归类为 `test`。

如果 `test` 需要 raw 批次，而当前上下文里没有，就只问 raw 批次路径。
不要展开问一串实现细节。

---

### 3. 执行规则

#### ingest
- 输入：`wiki-root` + `raw-dir`
- 默认思路：先建议，后落库
- 主要产物：专题页草稿、related 候选、回链建议、index/log、build report

#### health-check
- 输入：`wiki-root`
- 不扫描 raw
- 不默认修复
- 主要产物：wiki health report

#### repair
- 输入：`wiki-root`
- 允许执行：
  - related 补全
  - 正文承接补写
  - frontmatter type 修复
  - 孤岛页桥接
  - index/log 修复
  - 全库 related 格式规范化
- 默认先保守，避免大规模旧页激进改写

#### test
- 输入：`wiki-root` + `raw-dir`
- 跑 smoke test，验证 ingest / health-check / repair 主路径
- 必须覆盖 `full-apply` 与旧入口 `--health-only` / `--health-only --repair` 兼容路径


---

### 4. 模式选择规则

- `suggest-only`：先出报告，不落库
- `safe-apply`：安全落库，尽量避免激进旧页改写
- `full-apply`：允许更完整执行，但风险最高

默认优先级：
1. 用户明确指定
2. 若未指定：`full-apply`
3. 若用户明确要求保守模式，才降级为 `safe-apply` 或 `suggest-only`

---

### 5. 风险规则

- 不要因为 skill 功能多，就自动把四类任务全跑一遍
- 一次只执行当前任务意图对应的那一类
- 涉及旧页批量改写时，优先保守
- 建链优先生成候选，不要盲改全库
- `index.md` 与 `log.md` 更新必须保持可重复运行
- 发现高风险批处理时，宁可降级成建议模式，也不要硬冲

---

### 6. 对用户的交互准则

你面向用户时：
- 说任务名称和人话选择，不说一堆 CLI 参数
- 问题尽量一次问清，只问必要项
- 先帮用户做任务分流，再执行
- 不要把 skill 用法写成“请你去 PowerShell 输入下面命令”

正确做法是：
- 你识别任务
- 你在必要节点询问
- 你自己执行
- 你把结果和下一步建议告诉用户

---

## 内部参数映射速查

仅供代理内部使用，不对用户展开：

- `ingest` → raw 入库整理
- `health-check` → 全库体检
- `repair` → 全库修复
- `test` → 回归测试

旧入口兼容：
- `--health-only` → 等价 `health-check`
- `--health-only --repair` → 等价 `repair`

---

## 约束

- 这是知识整理层，不负责抓取、OCR、网页正文抽取
- 默认不要大规模改旧页，除非用户明确允许更激进执行
- 调用前先判断任务类型，再决定是否需要追问
- 用户要的是完成结果，不是命令教学
