# 微信公众号文章爬虫 —— 增强版使用说明

现在这套流程分成两层：

1. `wechat_crawler_enhanced.py`：抓文章列表，保留轻量正文抓取能力
2. `wechat_article_workflow.py`：端到端自动流程，负责登录、抓取、正文提取、Markdown 清洗、raw 入库

---

## 推荐用法：端到端工作流

### 适用场景

- 只给公众号名称
- 只给时间范围
- 只给关键词
- 要求自动弹登录页、登录后继续抓
- 要求完整正文 Markdown 存档
- 要求自动清洗并同步进 `wiki-kb/raw`

### 命令

```bash
python scripts/wechat_article_workflow.py \
  --nickname "目标公众号名称" \
  --since 2026-01-01 \
  --until 2026-04-30 \
  --keywords "因子,风险,组合优化" \
  --max 200 \
  --visible
```

### 流程说明

1. 调用增强版抓取器拉文章列表
2. 读取最新 `_full_*.json` 或列表 JSON
3. 按关键词 / 截止日期过滤
4. 调用 `playwright_extract.py` 用真实浏览器抽正文
5. 导出到 `output/markdown/公众号名/月份/`
6. 清洗 Markdown
7. 同步到 `<YOUR_WIKI_KB_PATH>/raw/时间戳_公众号名/`
8. 输出一份入库报告，供后续建 wiki 专题页与互链

---

## 为什么正文提取改走 Playwright

之前验证过：

- `requests` 不稳定
- 带 Cookie 的 `requests` 依然可能撞到验证页
- `Url2Html` 可保留作轻量 fallback，但不该再当“完整正文稳定方案”

所以要抓完整正文，默认走：

```text
wechat_article_workflow.py -> playwright_extract.py
```

---

## 旧版增强抓取器

如果用户只要文章列表，可以继续：

```bash
python scripts/wechat_crawler_enhanced.py --nickname "目标公众号名称" --since 2026-01-01 --max 200
```

如果中断：

```bash
python scripts/wechat_crawler_enhanced.py --nickname "目标公众号名称" --since 2026-01-01 --resume
```

---

## 风控建议

- 小批量：`--delay 5.5`
- 中批量：保持可见浏览器，优先稳
- 大批量：`--delay 8` + 分批 + 续跑
- 出现 `环境异常`：不要硬顶，退避后继续

---

## 输出结构

```text
output/
├── 公众号名称_YYYYMMDD_HHMMSS.json
├── 公众号名称_full_YYYYMMDD_HHMMSS.json
├── 公众号名称_filtered_YYYYMMDD_HHMMSS.json
└── markdown/
    └── 公众号名称/
        └── YYYY-MM/
            └── YYYYMMDD_文章标题.md

wiki-kb/raw/
└── YYYYMMDD_HHMMSS_公众号名称/
    └── ...
```

---

## 后续补充

当前脚本已经把“抓取 → 正文 → 清洗 → raw 入库”串起来。

后续若要继续扩展到：
- 自动按主题生成 wiki 专题页
- 自动补 `related`
- 自动更新 `index.md` / `log.md`

建议把“wiki 建页与互链”再单独拆成下一层脚本，避免在抓取阶段过度猜主题。
