---
name: wechat-article-crawler
description: >-
  This skill should be used when the user needs to batch-extract or download articles from WeChat Official Accounts (微信公众号).
  Triggers include: "抓取公众号文章", "批量下载微信公众号", "提取公众号历史文章", "wechat article crawler",
  "公众号文章备份", "导出公众号文章为 Markdown", "采集微信文章", "下载微信公众号内容",
  "根据公众号名称抓文章", "按时间范围抓公众号", "按关键词筛选公众号文章", "抽取公众号正文并入库 wiki-kb".
  Covers both self-owned accounts (via mp.weixin.qq.com) and third-party public accounts,
  with enhanced anti-detection features (random delays, batching, resume capability) and an end-to-end workflow
  for login, extraction, markdown archiving, cleaning, raw wiki ingestion, and follow-up wiki drafting.
---

# WeChat Official Account Article Crawler — End-to-End

## Overview

Provide a robust, anti-detection solution for batch-extracting articles from WeChat Official Accounts (微信公众号).
Built on top of `wechat-article-claw` and the proven Playwright extraction path. This skill now supports an end-to-end workflow:

1. 用户只提供公众号名称、时间范围或关键词
2. 自动弹出登录页（本地环境）
3. 登录后抓取文章列表
4. 用 Playwright 提取正文
5. 导出 Markdown
6. 清洗 Markdown
7. 同步入库 `wiki-kb/raw`
8. 产出一份后续 wiki 建页/互链建议报告

## When to Trigger

Activate this skill whenever the user:

- Mentions scraping, extracting, downloading, or backing up WeChat Official Account articles
- Needs historical articles from a specific time range
- Wants to convert WeChat articles into Markdown or other formats
- Wants the full workflow from login → extraction → markdown → wiki ingestion
- Asks for filtering by account name, date range, or keywords
- Asks about `wechat-article-claw` or WeChat article crawling tools
- Needs guidance on anti-blocking strategies for WeChat content extraction

## Environment Rule

### Local machine / user PC
Preferred path. You can open the browser login flow and continue after the user scans QR.

### Cloud server / remote Linux
Do **not** default to QR login. Ask the user to extract fresh Cookie + Token locally, then pass them via `--credentials`.

## Prerequisites

1. Install dependencies first:

```bash
pip install -r requirements.txt
playwright install chromium
```

2. The target repository `wechat-article-claw` should exist at the workspace root.

## Fastest Reliable Workflow

### A. End-to-end mode (强制唯一路径)

**任何要求"爬取公众号文章"、"提取正文"、"导出 Markdown"的任务，必须调用此端到端工作流，不得单独调用 `wechat_crawler_enhanced.py` 做完整抓取。**

```bash
python scripts/wechat_article_workflow.py \
  --nickname "目标公众号名称" \
  --since 2026-01-01 \
  --until 2026-04-30 \
  --keywords "因子,风险,组合优化" \
  --max 200 \
  --visible
```

What it does:
- runs the enhanced article-list crawler (阶段一，只抓列表，不走 Url2Html)
- filters by keyword/date if needed
- runs Playwright正文提取 (阶段二，强制)
- exports Markdown
- cleans Markdown + **正文质量校验**（检测验证页）
- syncs files to `wiki-kb/raw/...`
- writes an ingest report for the next wiki-building step，报告中明确标注真实成功/失败数

### B. Article-list only mode

仅当用户**明确说"只要列表""只要标题和链接"**时才使用：

```bash
python scripts/wechat_crawler_enhanced.py \
  --nickname "目标公众号名称" \
  --since 2026-01-01 \
  --max 200
```

### C. Resume interrupted crawl

```bash
python scripts/wechat_article_workflow.py \
  --nickname "目标公众号名称" \
  --since 2026-01-01 \
  --resume
```

## Why Playwright Is the Mandatory Path For Full Text

`Url2Html` 方案已被证明不可靠——即使 Cookie 有效，微信正文页面仍可能返回"环境异常"验证页，而 `Url2Html` 会把验证页 HTML 误判为成功提取。

**因此，任何需要完整正文的场景，强制走 Playwright 路径：**

`wechat_article_workflow.py` → `playwright_extract.py`

### 正文成功判定规则（铁律）

不以"没抛异常"为准，以**内容质量**为准：

1. **字数校验**：正文内容长度必须 > 100 字（验证页通常 < 100 字）
2. **关键词校验**：内容不得包含 "环境异常"、"完成验证后即可继续访问"、"去验证"
3. **阶段一隔离**：`wechat_crawler_enhanced.py` 默认 `skip_content=True`，不再走 Url2Html 正文提取
4. **阶段二强制**：`wechat_article_workflow.py` 在 Playwright 提取后，扫描所有 Markdown 文件执行质量校验
5. **报告真实**：入库报告中明确标注 `正文提取成功 / 正文提取失败`，失败文件列出路径供复核

## Command-Line Reference

### `scripts/wechat_article_workflow.py`

| Flag | Description |
|------|-------------|
| `--nickname` | Target account name |
| `--since` | Start date `YYYY-MM-DD` |
| `--until` | End date `YYYY-MM-DD` for post-filtering |
| `--keywords` | Keywords separated by commas |
| `--max` | Maximum articles to fetch |
| `--credentials` | JSON string with cookie/token |
| `--credentials-file` | Credential file name under `wechat-article-claw/` |
| `--workspace-root` | Workspace root, default `<YOUR_WORKSPACE_PATH>` |
| `--wiki-kb-root` | wiki-kb root, default `<YOUR_WIKI_KB_PATH>` |
| `--delay` | Base delay for Playwright extraction |
| `--visible` | Show browser window |
| `--resume` | Resume crawl |

### `scripts/wechat_crawler_enhanced.py`

| Flag | Description |
|------|-------------|
| `--nickname` | Target account name |
| `--since` | Start date `YYYY-MM-DD` |
| `--max` | Maximum articles to fetch |
| `--fakeid` | Account fakeid |
| `--credentials` | JSON with cookie and token |
| `--resume` | Resume from checkpoint |
| `--batch-limit` | Override batch size per run |
| `--rest-seconds` | Override inter-batch rest duration |
| `--delay` | Override base delay |
| `--skip-content` | Fetch article list only（**默认已开启**） |
| `--use-url2html` | [不推荐] 启用 Url2Html 正文提取（易触发验证页） |
| `--no-markdown` | Skip Markdown export |
| `--config` | Path to config JSON |
| `--headless` | Run browser in headless mode |

## Output Structure

```text
output/
├── 公众号名称_YYYYMMDD_HHMMSS.json
├── 公众号名称_full_YYYYMMDD_HHMMSS.json
├── 公众号名称_filtered_YYYYMMDD_HHMMSS.json
└── markdown/
    └── 公众号名称/
        ├── 2026-04/
        │   ├── 20260420_文章标题.md
        │   └── 20260421_文章标题.md
        └── ...

wiki-kb/
└── raw/
    └── YYYYMMDD_HHMMSS_公众号名称/
        └── ...markdown files...
```

## Anti-Detection Guidance

| Situation | Recommended Setting |
|----------|---------------------|
| < 50 articles | `--delay 5.5` |
| 50~200 articles | `--delay 5.5` + visible browser |
| > 200 articles | `--delay 8` + smaller batches + resume |
| verify page appears | keep cookies, backoff, continue conservatively |

## Agent SOP

When using this skill, follow this order:

1. Confirm local vs cloud environment
2. Prefer local visible login for QR scan
3. Gather target info: nickname / date range / keywords / max count
4. **Run `wechat_article_workflow.py` (end-to-end) — 这是唯一合法路径**
   - 不允许直接调用 `wechat_crawler_enhanced.py` 做"完整抓取"，因为它默认跳过正文提取
   - 只有用户**明确说"只要列表"**时，才允许单独调用 `wechat_crawler_enhanced.py`
5. After workflow finishes, **必须检查报告中的正文质量校验结果**：
   - 如果 `正文提取失败 > 0`，明确告知用户哪些文章失败、建议重新提取
   - 不得以"工作流完成"掩盖正文提取失败的事实
6. Decide whether to:
   - only deliver raw archive
   - or continue with wiki topic pages / related links

## Resources

### scripts/
- `wechat_crawler_enhanced.py` — **列表抓取器（默认跳过正文）**。仅用于阶段一获取文章列表，默认 `skip_content=True`；正文提取由工作流统一调度 Playwright 完成。
- `wechat_article_workflow.py` — **端到端编排器（强制路径）**。负责阶段一（列表）→ 阶段二（Playwright 正文提取）→ Markdown 清洗 → 质量校验 → raw 入库 → 报告输出。

### references/
- `config_enhanced.json` — default configuration template

## Disclaimer

This tool is for personal learning and research only. Respect content copyright and data ownership. The user assumes all risks related to account suspension or IP blocking.
