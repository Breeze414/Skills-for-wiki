# wiki-research-scout — 验证记录

## 目录结构

```
wiki-research-scout/
├── SKILL.md                           (5.2 KB)
├── references/
│   ├── candidate-scoring.md           (2.3 KB)
│   ├── output-template.md             (2.6 KB)
│   ├── site-selection-policy.md       (3.9 KB)
│   └── topic-patterns.md              (5.3 KB)
└── assets/                            (空，为将来脚本预留)
```

## SKILL.md 验证

- ✅ YAML frontmatter：`name` + `description` 完整
- ✅ description 含触发词和 when-to-use 描述
- ✅ 三段式工作流（Phase 1 扫描 → Phase 2 侦察 → Phase 3 候选输出）
- ✅ 默认模式声明为 `suggest-only`
- ✅ 来源可信度分级规则
- ✅ 交互准则（面向用户时的首段话术）
- ✅ 安全/质量规则（幻觉/越权/来源可见）

## references 验证

- ✅ `site-selection-policy.md`：4 类主题 × 站点映射表 + A/B/C 分级定义 + 搜索行为规则
- ✅ `candidate-scoring.md`：三维评分体系 + 综合优先级计算 + 排序规则
- ✅ `output-template.md`：完整报告模板 + 单卡片最小要求 + 用户交互短格式
- ✅ `topic-patterns.md`：6 类扫描模式 × 检测方法 × 判断标准 × 建议动作 + 执行顺序建议

## 与 wiki-kb-builder 职责分离

| 层面 | wiki-research-scout | wiki-kb-builder |
|------|-------------------|----------------|
| 职能 | 发现 + 侦察 | 整理 + 建页 |
| 改库 | 否 | 是 |
| 默认模式 | suggest-only | 需用户指定模式 |
| 外部搜索 | 是 | 否 |
| 接入上游 | wiki-kb 扫描 → 外部搜索 | raw 资料入库 |

## 剩余待完成

- 脚本开发（`scan_wiki_topics.py`、`build_search_queries.py` 等）— 当前阶段不写
- smoke test 回归 — 有脚本后再补
