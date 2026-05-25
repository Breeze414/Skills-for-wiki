# WorkBuddy Skills — Open Source Collection

一套用于 AI 工作助手（WorkBuddy / OpenClaw 等）的 Skill 插件，专注于知识库管理、内容抓取与研究侦察。

---

## Skills 概览

### 1. wechat-article-crawler
微信公众号文章批量抓取与入库工具。

- 基于 Playwright 的端到端正文提取
- 反检测机制（随机延迟、分批、断点续传）
- Markdown 导出与质量校验
- 增量去重同步到 wiki-kb/raw

### 2. wiki-kb-builder
通用 wiki 知识库建页与建链工具。

- 将 raw 资料自动整理为专题页
- 生成 related 候选与回链建议
- 全库健康检查与修复
- 支持 suggest-only / safe-apply / full-apply 三种模式

### 3. wiki-research-scout
wiki 知识库研究选题发现器与外部资料侦察器。

- 6 维度扫描识别知识扩展点
- 外部站点侦察与来源分级（A/B/C）
- 结构化候选清单输出
- 默认 suggest-only，不改库

---

## 快速开始

每个 skill 目录结构遵循 OpenClaw 规范：

```
skill-name/
├── SKILL.md              # Skill 定义与使用说明
├── scripts/              # 可执行脚本
└── references/           # 参考文档与配置模板
```

将目录复制到你的 WorkBuddy skills 目录即可使用：

```bash
# Linux/macOS
cp -r wechat-article-crawler ~/.workbuddy/skills/
cp -r wiki-kb-builder ~/.workbuddy/skills/
cp -r wiki-research-scout ~/.workbuddy/skills/

# Windows
xcopy /E /I wechat-article-crawler %USERPROFILE%\.workbuddy\skills\
xcopy /E /I wiki-kb-builder %USERPROFILE%\.workbuddy\skills\
xcopy /E /I wiki-research-scout %USERPROFILE%\.workbuddy\skills\
```

---

## 配置说明

部分脚本包含默认路径占位符，使用前请替换为你的实际路径：

- `<YOUR_WIKI_KB_PATH>` — wiki 知识库根目录
- `<YOUR_WORKSPACE_PATH>` — 工作区根目录

---

## 依赖

- Python 3.10+
- Playwright（wechat-article-crawler 需要）
- beautifulsoup4（可选，增强 HTML 解析）

---

## License

MIT
