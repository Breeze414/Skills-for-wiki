#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wiki_kb_builder.py

增强版目标：
1. 扫描 raw 目录并抽取更稳的文档特征
2. 基于 hint / token / 月份 / 标题模式做更稳的主题聚类
3. 生成 topic_clusters / link_candidates / backfill_suggestions / build_report
4. safe-apply 时生成专题页草稿并更新 index/log

说明：
- 仍然避免复杂 NLP 和全库激进改写。
- 优先保证：结构清晰、可审计、可解释、可迭代。
"""

from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import tempfile





STOPWORDS = {
    "的",
    "了",
    "和",
    "与",
    "及",
    "在",
    "对",
    "是",
    "把",
    "将",
    "用",
    "从",
    "看",
    "再",
    "又",
    "按",
    "上",
    "下",
    "中",
    "后",
    "前",
    "研究",
    "专题",
    "方法",
    "策略",
    "系列",
    "一个",
    "我们",
    "你",
    "我",
    "他",
    "她",
    "它",
    "以及",
    "关于",
    "如何",
    "为什么",
    "什么",
    "哪些",
    "这种",
    "这个",
    "那个",
    "进行",
    "问题",
    "分析",
    "实战",
    "框架",
    "系统",
    "模型",
}

NOISE_TOKENS = {
    "title",
    "type",
    "source",
    "created",
    "updated",
    "status",
    "confidence",
    "related",
    "tags",
    "raw",
    "sources",
    "guide",
    "topic",
    "wiki",
    "markdown",
    "http",
    "https",
    "com",
    "www",
    "作者",
    "链接",
    "原文",
    "公众号",
    "阅读",
    "点击",
    "本文",
    "文中",
    "内容",
    "整理",
    "草稿",
    "待补充",
}

NOISE_LINE_PATTERNS = [
    re.compile(r"^\s*(title|type|source|created|updated|status|confidence|related|tags):", re.I),
    re.compile(r"^\s*\*\*Raw Sources\*\*:?\s*$", re.I),
    re.compile(r"^\s*raw\s+sources\s*:?\s*$", re.I),
    re.compile(r"^\s*https?://\S+\s*$", re.I),
    re.compile(r"^\s*\[[^\]]+\]\([^\)]+\)\s*$"),
]

NOISE_SENTENCE_PATTERNS = [
    re.compile(r"^raw sources", re.I),
    re.compile(r"^最后更新", re.I),
    re.compile(r"^来源批次", re.I),
    re.compile(r"^当前 related", re.I),
    re.compile(r"^当前主题", re.I),
    re.compile(r"^该页由一批 raw 资料自动聚合生成", re.I),
]


TITLE_HINT_PATTERNS = [
    re.compile(r"(?P<name>.+?)(?:专题|系列|方法论|框架|实战|研究)$"),
    re.compile(r"(?P<name>.+?)(?:入门|详解|拆解|笔记|综述)$"),
]

BACKLINK_SECTION_CANDIDATES = [
    "## 与其他知识的关联",
    "## 关联",
    "## 相关知识",
    "## 延伸阅读",
]


@dataclass
class RawDoc:
    path: str
    title: str
    rel_path: str
    month: str
    tags: list[str]
    preview: str
    text: str = ""
    tokens: list[str] = field(default_factory=list)
    score_map: dict[str, int] = field(default_factory=dict)
    title_key: str = ""


@dataclass
class TopicCluster:
    name: str
    hints_hit: list[str]
    docs: list[RawDoc]
    anchor_terms: list[str] = field(default_factory=list)
    months: list[str] = field(default_factory=list)
    suggested_title: str = ""
    page_type: str = "guide"
    page_type_candidates: list[str] = field(default_factory=list)
    page_type_scores: dict[str, int] = field(default_factory=dict)
    page_type_breakdown: str = ""
    quality_score: int = 0
    quality_flags: list[str] = field(default_factory=list)
    summary: str = ""
    consensus_points: list[str] = field(default_factory=list)
    divergence_points: list[str] = field(default_factory=list)


@dataclass
class RelatedCandidate:
    title: str
    score: int
    reasons: list[str]
    strength: str = "candidate"


@dataclass
class ExistingPage:
    title: str
    page_type: str
    tags: list[str]
    related: list[str]
    path: str
    text: str
    inbound_refs: int = 0


@dataclass
class BacklinkSuggestion:
    target_title: str
    target_path: str
    score: int
    reasons: list[str]
    suggested_line: str
    section_hint: str
    section_found: bool


@dataclass
class ConflictAlert:
    target_title: str
    target_path: str
    severity: str
    reasons: list[str]
    shared_terms: list[str] = field(default_factory=list)


@dataclass
class RiskGateDecision:
    blocked: bool
    level: str
    reasons: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


@dataclass
class WikiHealthReport:
    total_pages: int = 0
    single_direction_links: list[str] = field(default_factory=list)
    related_missing_links: list[str] = field(default_factory=list)
    body_missing_links: list[str] = field(default_factory=list)
    island_pages: list[str] = field(default_factory=list)
    pseudo_existing_pages: list[str] = field(default_factory=list)
    frontmatter_issues: list[str] = field(default_factory=list)
    index_missing_pages: list[str] = field(default_factory=list)
    log_missing_pages: list[str] = field(default_factory=list)
    maintenance_actions: list[str] = field(default_factory=list)












TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+\-]{1,}|[\u4e00-\u9fff]{2,}")


def read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def extract_title(path: Path, text: str) -> str:
    m = re.search(r"^title:\s*\"?(.*?)\"?$", text, flags=re.M)
    if m and m.group(1).strip():
        return m.group(1).strip()
    first_heading = re.search(r"^#\s+(.+)$", text, flags=re.M)
    if first_heading:
        return first_heading.group(1).strip()
    return path.stem


def extract_frontmatter_list(text: str, field_name: str) -> list[str]:
    m = re.search(rf"^{field_name}:\s*\[(.*?)\]\s*$", text, flags=re.M)
    if not m:
        return []
    raw = m.group(1).strip()
    if not raw:
        return []

    normalized_raw = re.sub(r"\]\]\s*,\s*\[\[", ",", raw)

    items = []
    seen = set()
    for part in normalized_raw.split(","):
        item = part.strip().strip('"').strip("'")
        item = re.sub(r"^\[+|\]+$", "", item).strip()
        item = re.sub(r"^\[\[|\]\]$", "", item).strip()
        if item and item not in seen:
            seen.add(item)
            items.append(item)
    return items




def normalize_token(token: str) -> str:
    token = token.strip().lower()
    token = re.sub(r"^[^\w\u4e00-\u9fff]+|[^\w\u4e00-\u9fff]+$", "", token)
    return token


def clean_markdown_noise(text: str) -> str:
    clean = re.sub(r"^---\n.*?\n---\n", " ", text, flags=re.S)
    clean = re.sub(r"```.*?```", " ", clean, flags=re.S)
    clean = re.sub(r"!\[[^\]]*\]\([^\)]+\)", " ", clean)
    clean = re.sub(r"\[[^\]]+\]\([^\)]+\)", " ", clean)
    clean = re.sub(r"\[\[([^\]]+)\]\]", r"\1", clean)

    kept_lines = []
    for raw_line in clean.splitlines():
        line = raw_line.strip()
        if not line:
            kept_lines.append("")
            continue
        if any(pattern.match(line) for pattern in NOISE_LINE_PATTERNS):
            continue
        if re.match(r"^\s*[-*]\s*`[^`]+`\s*$", line):
            continue
        kept_lines.append(raw_line)

    clean = "\n".join(kept_lines)
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()


def is_noise_token(token: str) -> bool:
    if token in STOPWORDS or token in NOISE_TOKENS:
        return True
    if len(token) <= 1:
        return True
    if token.isdigit():
        return True
    if re.fullmatch(r"\d{4}[-/]?\d{1,2}([-/]?\d{1,2})?", token):
        return True
    if token.endswith(("md", "html", "json")) and len(token) <= 12:
        return True
    if token in {"section", "candidate", "reason", "score", "yes", "no"}:
        return True
    if re.fullmatch(r"[\u4e00-\u9fff]{7,}", token):
        return True
    if len(token) >= 5 and any(stop in token for stop in {"我们", "你", "我", "在", "与", "及", "的", "了", "和"}):
        return True
    return False



def is_noise_sentence(sentence: str) -> bool:
    if len(sentence) < 12:
        return True
    if any(pattern.search(sentence) for pattern in NOISE_SENTENCE_PATTERNS):
        return True
    if sentence.count("`") >= 2:
        return True
    if sentence.startswith(("title:", "type:", "source:", "tags:", "related:")):
        return True
    token_list = tokenize(sentence, drop_noise=False)
    useful = [token for token in token_list if not is_noise_token(token)]
    return len(useful) < 2


def tokenize(text: str, drop_noise: bool = True) -> list[str]:
    source = clean_markdown_noise(text) if drop_noise else text
    tokens = []
    for raw in TOKEN_RE.findall(source):
        token = normalize_token(raw)
        if not token:
            continue
        if drop_noise and is_noise_token(token):
            continue
        tokens.append(token)
    return tokens



def extract_title_key(title: str) -> str:
    clean = re.sub(r"[：:（(].*?$", "", title).strip()
    for pattern in TITLE_HINT_PATTERNS:
        m = pattern.match(clean)
        if m:
            key = m.group("name").strip()
            if key:
                return key
    return clean


def infer_hint_scores(title: str, preview: str, topic_hints: list[str]) -> dict[str, int]:
    hay = f"{title}\n{preview}".lower()
    scores: dict[str, int] = {}
    for hint in topic_hints:
        score = 0
        hint_l = hint.lower()
        if hint_l in title.lower():
            score += 4
        if hint_l in preview.lower():
            score += 2
        pieces = [p for p in re.split(r"[、，,\s/]+", hint_l) if p]
        score += sum(1 for p in pieces if len(p) >= 2 and p in hay)
        if score > 0:
            scores[hint] = score
    return scores


def extract_key_sentences(text: str, limit: int = 6) -> list[str]:
    clean = clean_markdown_noise(text)
    chunks = re.split(r"(?<=[。！？!?；;])|\n+", clean)

    seen = set()
    sentences = []
    for chunk in chunks:
        sentence = re.sub(r"\s+", " ", chunk).strip(" -#*`\t\r\n")
        if is_noise_sentence(sentence):
            continue
        if sentence in seen:
            continue
        seen.add(sentence)
        sentences.append(sentence)
        if len(sentences) >= limit:
            break
    return sentences



def synthesize_cluster_summary(cluster: TopicCluster) -> str:
    months = "、".join([m for m in cluster.months if m != "unknown"][:3]) or "多批次"
    anchors = "、".join(cluster.anchor_terms[:4]) if cluster.anchor_terms else cluster.name
    return f"该专题聚合了 {len(cluster.docs)} 篇 raw，主要围绕“{cluster.name}”，覆盖时间 {months}，高频锚点包括 {anchors}。"


def derive_consensus_and_divergence(cluster: TopicCluster) -> tuple[list[str], list[str]]:
    token_counter = Counter(token for doc in cluster.docs for token in doc.tokens)
    common = [token for token, count in token_counter.items() if count >= 2 and len(token) >= 2][:5]
    consensus = [f"多篇 raw 反复提到“{token}”，属于该主题稳定共识。" for token in common[:3]]

    title_keys = Counter(doc.title_key for doc in cluster.docs if doc.title_key)
    divergence = []
    if len(title_keys) >= 2:
        for key, count in title_keys.most_common(2):
            divergence.append(f"存在“{key}”视角的子主题分化（{count} 篇）。")
    month_set = [m for m in cluster.months if m != "unknown"]
    if len(month_set) >= 2:
        divergence.append(f"资料跨 {month_set[0]} 到 {month_set[-1]}，可能存在阶段性观点变化。")
    return consensus[:3], divergence[:3]


def score_cluster_quality(cluster: TopicCluster) -> tuple[int, list[str]]:
    score = 100
    flags = []
    if len(cluster.docs) == 1:
        score -= 25
        flags.append("仅 1 篇 raw，主题稳定性弱")
    if len(cluster.anchor_terms) >= 6 and len(set(cluster.anchor_terms[:6])) >= 6:
        score -= 10
        flags.append("锚点词较发散，可能混入异质内容")
    months = [m for m in cluster.months if m != "unknown"]
    if len(months) >= 3:
        score -= 10
        flags.append("跨月跨度较大，建议检查是否应继续拆分")
    title_keys = {doc.title_key for doc in cluster.docs if doc.title_key}
    if len(title_keys) >= 4:
        score -= 10
        flags.append("标题模式较杂，聚类边界可能偏宽")
    if not cluster.hints_hit:
        score -= 8
        flags.append("未命中明确 topic-hints，建议补 hint 或人工命名")
    return max(score, 0), flags





def scan_raw_docs(raw_dir: Path, topic_hints: list[str]) -> list[RawDoc]:
    docs: list[RawDoc] = []
    for fp in sorted(raw_dir.rglob("*.md")):
        text = read_text_safe(fp)
        title = extract_title(fp, text)
        cleaned_text = clean_markdown_noise(text)
        preview = re.sub(r"\s+", " ", cleaned_text[:600]).strip()
        month = fp.parent.name if re.match(r"\d{4}-\d{2}", fp.parent.name) else "unknown"
        score_map = infer_hint_scores(title, preview, topic_hints)
        tags = [k for k, _ in sorted(score_map.items(), key=lambda x: (-x[1], x[0]))[:5]]
        tokens = tokenize(f"{title}\n{cleaned_text}")
        title_key = extract_title_key(title)
        docs.append(
            RawDoc(
                path=str(fp),
                title=title,
                rel_path=str(fp.relative_to(raw_dir.parent)).replace("\\", "/"),
                month=month,
                tags=tags,
                preview=preview,
                text=text,
                tokens=tokens,
                score_map=score_map,
                title_key=title_key,
            )
        )
    return docs



def choose_cluster_name(items: list[RawDoc]) -> tuple[str, list[str]]:

    hint_counter = Counter(tag for doc in items for tag in doc.tags)
    if hint_counter:
        name = hint_counter.most_common(1)[0][0]
        return name, [k for k, _ in hint_counter.most_common(8)]

    key_counter = Counter(doc.title_key for doc in items if len(doc.title_key) >= 2)
    if key_counter:
        name = key_counter.most_common(1)[0][0]
        return name, []

    token_counter = Counter(token for doc in items for token in doc.tokens)
    anchor_terms = [k for k, _ in token_counter.most_common(3)]
    if anchor_terms:
        return " / ".join(anchor_terms[:2]), []
    return "未分类主题", []


def split_oversized_cluster(name: str, items: list[RawDoc]) -> list[tuple[str, list[RawDoc]]]:
    if len(items) <= 6:
        return [(name, items)]

    key_buckets: dict[str, list[RawDoc]] = defaultdict(list)
    for doc in items:
        key = doc.title_key if len(doc.title_key) >= 2 else name
        key_buckets[key].append(doc)

    significant = {k: v for k, v in key_buckets.items() if len(v) >= 2 and k != name}
    if len(significant) >= 2:
        result = []
        for key, docs in sorted(significant.items(), key=lambda x: len(x[1]), reverse=True):
            result.append((key, docs))
        leftovers = [doc for key, docs in key_buckets.items() if key not in significant for doc in docs]
        if leftovers:
            result.append((name, leftovers))
        return result

    month_buckets: dict[str, list[RawDoc]] = defaultdict(list)
    for doc in items:
        month_buckets[doc.month].append(doc)
    if len(month_buckets) >= 3:
        return [
            (f"{name}-{month}", docs)
            for month, docs in sorted(month_buckets.items(), key=lambda x: x[0])
        ]

    return [(name, items)]


def cluster_docs(docs: list[RawDoc], topic_hints: list[str], source_type: str = "mixed",
                 existing_pages: list[ExistingPage] | None = None) -> list[TopicCluster]:
    primary_buckets: dict[str, list[RawDoc]] = defaultdict(list)
    fallback = "未分类主题"
    for doc in docs:
        if doc.tags:
            primary_buckets[doc.tags[0]].append(doc)
        elif len(doc.title_key) >= 2:
            primary_buckets[doc.title_key].append(doc)
        else:
            primary_buckets[fallback].append(doc)

    clusters: list[TopicCluster] = []
    for base_name, items in primary_buckets.items():
        for split_name, split_items in split_oversized_cluster(base_name, items):
            cluster_name, hints_hit = choose_cluster_name(split_items)
            token_counter = Counter(token for doc in split_items for token in doc.tokens)
            anchor_terms = [k for k, _ in token_counter.most_common(8)]
            months = sorted({doc.month for doc in split_items})
            cluster = TopicCluster(
                name=cluster_name or split_name,
                hints_hit=hints_hit,
                docs=sorted(split_items, key=lambda x: x.title),
                anchor_terms=anchor_terms,
                months=months,
            )
            cluster.page_type, cluster.page_type_candidates, cluster.page_type_scores, cluster.page_type_breakdown = choose_page_type(cluster, source_type, existing_pages)
            cluster.quality_score, cluster.quality_flags = score_cluster_quality(cluster)
            cluster.summary = synthesize_cluster_summary(cluster)
            cluster.consensus_points, cluster.divergence_points = derive_consensus_and_divergence(cluster)
            clusters.append(cluster)

    clusters.sort(key=lambda x: (len(x.docs), len(x.hints_hit)), reverse=True)
    return clusters



def similarity_score(cluster: TopicCluster, page: ExistingPage) -> RelatedCandidate | None:
    reasons = []
    score = 0
    title_l = page.title.lower()
    cluster_name_l = cluster.name.lower()

    if cluster_name_l and cluster_name_l in title_l:
        score += 8
        reasons.append("标题包含主题名")

    shared_hints = [tag for tag in cluster.hints_hit if tag in page.tags or tag.lower() in title_l]
    if shared_hints:
        score += min(6, len(shared_hints) * 3)
        reasons.append(f"共享主题标签: {', '.join(shared_hints[:3])}")

    shared_anchor = [token for token in cluster.anchor_terms[:8] if token and token in title_l]
    if shared_anchor:
        score += min(5, len(shared_anchor) * 2)
        reasons.append(f"标题命中关键词: {', '.join(shared_anchor[:3])}")

    type_priority = {
        "guide": {"guide": 2, "topic": 2, "concept": 1},
        "topic": {"topic": 3, "concept": 2, "guide": 1, "paper": 1},
        "strategy": {"strategy": 3, "topic": 2, "concept": 2},
        "concept": {"concept": 3, "topic": 2, "strategy": 1, "paper": 1, "guide": 1},
        "paper": {"paper": 3, "topic": 2, "concept": 2},
    }
    type_bonus = type_priority.get(cluster.page_type, {}).get(page.page_type, 0)
    if type_bonus:
        score += type_bonus
        reasons.append(f"页面类型匹配: {cluster.page_type}->{page.page_type}")

    if page.inbound_refs >= 3 and cluster.page_type in {"concept", "topic"}:
        score += 1
        reasons.append("高连接度页面，适合做桥接")

    if score <= 0:
        return None
    return RelatedCandidate(title=page.title, score=score, reasons=reasons, strength=related_strength(score))




def propose_related(cluster: TopicCluster, existing_pages: list[ExistingPage], self_title: str | None = None) -> list[RelatedCandidate]:
    candidates: list[RelatedCandidate] = []
    for page in existing_pages:
        if self_title and page.title == self_title:
            continue
        candidate = similarity_score(cluster, page)
        if candidate:
            candidates.append(candidate)

    dedup: dict[str, RelatedCandidate] = {}
    for candidate in candidates:
        prev = dedup.get(candidate.title)
        if not prev or candidate.score > prev.score:
            dedup[candidate.title] = candidate

    result = sorted(dedup.values(), key=lambda x: (-x.score, x.title))
    return result[:8]


def detect_backlink_section(text: str) -> tuple[str, bool]:
    for section in BACKLINK_SECTION_CANDIDATES:
        if section in text:
            return section, True
    return "无明确关联章节", False


def find_section_bounds(text: str, marker: str) -> tuple[int, int] | None:
    marker_start = text.find(marker)
    if marker_start == -1:
        return None

    section_start = marker_start + len(marker)
    remainder = text[section_start:]
    next_heading = re.search(r"\n##\s+", remainder)
    section_end = section_start + next_heading.start() if next_heading else len(text)
    return section_start, section_end


LIST_LINE_RE = re.compile(r"^(?P<indent>[ \t]*)([-*+] |\d+\. )")


def insert_backlink_into_section(text: str, marker: str, suggested_line: str) -> str | None:
    bounds = find_section_bounds(text, marker)
    if not bounds:
        return None

    section_start, section_end = bounds
    body = text[section_start:section_end]
    lines = body.splitlines(keepends=True)

    list_indices = [i for i, line in enumerate(lines) if LIST_LINE_RE.match(line)]
    if list_indices:
        insert_at = list_indices[-1] + 1
        while insert_at < len(lines) and lines[insert_at].strip() == "":
            insert_at += 1
        new_line = suggested_line + "\n"
        lines.insert(insert_at, new_line)
        new_body = "".join(lines)
        return text[:section_start] + new_body + text[section_end:]

    body_stripped_left = body.lstrip("\r\n")
    leading_newlines = body[: len(body) - len(body_stripped_left)]
    trimmed = body_stripped_left.rstrip()

    if not trimmed:
        new_body = f"\n\n{suggested_line}\n"
        return text[:section_start] + new_body + text[section_end:]

    suffix = "\n\n" if not trimmed.endswith("\n") else "\n"
    new_body = f"{leading_newlines}{trimmed}{suffix}{suggested_line}\n"
    return text[:section_start] + new_body + text[section_end:]






def related_strength(score: int) -> str:
    if score >= 12:
        return "strong"
    if score >= 8:
        return "medium"
    return "weak"


def sentence_polarity(sentence: str) -> tuple[bool, bool]:
    neg_words = ("不", "无", "未", "不能", "避免", "风险", "失效", "下降", "回撤")
    pos_words = ("可", "能够", "适合", "有效", "优势", "增强", "提升", "改善", "稳健")
    has_neg = any(word in sentence for word in neg_words)
    has_pos = any(word in sentence for word in pos_words)
    return has_neg, has_pos


def shared_topic_terms(cluster: TopicCluster, page: ExistingPage, top_k: int = 6) -> list[str]:
    cluster_terms = {token for token in cluster.anchor_terms[:10] if not is_noise_token(token)}
    page_title_tokens = set(tokenize(page.title))
    page_tag_tokens = {normalize_token(tag) for tag in page.tags if normalize_token(tag)}
    shared = [token for token in cluster_terms if token in page_title_tokens or token in page_tag_tokens]
    shared.sort(key=lambda x: (len(x), x), reverse=True)
    return shared[:top_k]


def sanitize_related_titles(items: list[str], self_title: str | None = None) -> list[str]:
    clean = []
    seen = set()
    self_key = (self_title or "").strip()
    for item in items:
        key = item.strip().replace("[[", "").replace("]]", "")
        key = re.sub(r"^\[+|\]+$", "", key).strip().strip('"').strip("'")
        if not key:
            continue
        if self_key and key == self_key:
            continue
        if key in seen:
            continue
        seen.add(key)
        clean.append(key)
    return clean






def update_related_frontmatter(path: Path, new_title: str) -> bool:
    text = read_text_safe(path)
    existing_related = extract_frontmatter_list(text, "related")
    self_title = extract_title(path, text)

    related_match = re.search(r"^related:\s*\[(.*?)\]\s*$", text, flags=re.M)
    if related_match:
        existing = list(existing_related)
        if new_title not in existing:
            existing.append(new_title)
        dedup = sanitize_related_titles(existing, self_title=self_title)
        replacement = f"related: [{', '.join(dedup)}]"
        new_text = text[: related_match.start()] + replacement + text[related_match.end() :]
    else:
        if text.startswith("---\n"):
            end = text.find("\n---", 4)
            if end == -1:
                return False
            insert_pos = end
            dedup = sanitize_related_titles([new_title], self_title=self_title)
            new_text = text[:insert_pos] + f"\nrelated: [{', '.join(dedup)}]" + text[insert_pos:]
        else:
            return False

    if new_text == text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True





def detect_conflicts(cluster: TopicCluster, page: ExistingPage) -> ConflictAlert | None:
    if cluster.suggested_title and page.title == cluster.suggested_title:
        return None

    shared_terms = shared_topic_terms(cluster, page)
    if not shared_terms:
        return None

    reasons = []
    page_sentences = extract_key_sentences(page.text, limit=10)
    raw_sentences = []
    for doc in cluster.docs[:6]:
        raw_sentences.extend(extract_key_sentences(doc.text or doc.preview, limit=2))

    for raw_sentence in raw_sentences[:10]:
        raw_tokens = set(tokenize(raw_sentence))
        raw_has_neg, raw_has_pos = sentence_polarity(raw_sentence)
        if not (raw_has_neg or raw_has_pos):
            continue

        for page_sentence in page_sentences:
            page_tokens = set(tokenize(page_sentence))
            overlap = raw_tokens & page_tokens
            topic_overlap = overlap & set(shared_terms)
            if len(overlap) < 2 or not topic_overlap:
                continue

            page_has_neg, page_has_pos = sentence_polarity(page_sentence)
            if raw_has_neg and page_has_pos:
                reasons.append(
                    f"共享词[{', '.join(sorted(topic_overlap)[:3])}]下，raw 偏否定而旧页偏正向：{page_sentence[:48]}..."
                )
            elif raw_has_pos and page_has_neg:
                reasons.append(
                    f"共享词[{', '.join(sorted(topic_overlap)[:3])}]下，raw 偏正向而旧页偏谨慎：{page_sentence[:48]}..."
                )

            if len(reasons) >= 3:
                break
        if len(reasons) >= 3:
            break

    if not reasons:
        return None
    severity = "high" if len(reasons) >= 2 and len(shared_terms) >= 2 else "medium"
    return ConflictAlert(
        target_title=page.title,
        target_path=page.path,
        severity=severity,
        reasons=reasons[:3],
        shared_terms=shared_terms,
    )




def preview_actions(args: argparse.Namespace, clusters: list[TopicCluster], existing_pages: list[ExistingPage]) -> list[str]:
    lines = ["## 批处理预览", "", f"- 模式: {args.mode}"]
    lines.append(f"- 计划处理主题数: {min(len(clusters), args.max_new_pages)}")
    lines.append(f"- 高风险阈值: 新页>{args.max_new_pages} 或 自动回链>10 或 旧页修改>6")
    lines.append("")
    for cluster in clusters[: args.max_new_pages]:
        related_candidates = propose_related(cluster, existing_pages)
        backlinks = propose_backlinks(cluster, cluster.suggested_title or cluster.name, existing_pages)
        lines.append(f"- {cluster.name}: 计划新页=1, related候选={len(related_candidates)}, 回链候选={len(backlinks)}")
    return lines


def assess_risk_gate(
    args: argparse.Namespace,
    clusters: list[TopicCluster],
    existing_pages: list[ExistingPage],
) -> RiskGateDecision:
    planned_clusters = clusters[: args.max_new_pages]
    planned_new_pages = len(planned_clusters)
    planned_backlinks = 0
    planned_existing_updates = 0
    conflict_targets = 0
    low_quality_clusters = 0
    reasons = []
    suggestions = []

    for cluster in planned_clusters:
        if cluster.quality_score < 70:
            low_quality_clusters += 1
        backlinks = propose_backlinks(cluster, cluster.suggested_title or cluster.name, existing_pages)
        planned_backlinks += len([x for x in backlinks if x.section_found and x.score >= args.backlink_min_score])
        planned_existing_updates += len([x for x in backlinks if x.section_found])
        for page in existing_pages:
            if detect_conflicts(cluster, page):
                conflict_targets += 1

    if args.mode == "full-apply" and args.apply_backlinks and planned_backlinks > 10:
        reasons.append(f"预计自动正文回链 {planned_backlinks} 条，超过安全阈值 10")
    if args.mode == "full-apply" and planned_existing_updates > 6:
        reasons.append(f"预计会触达 {planned_existing_updates} 个旧页，超过安全阈值 6")
    if planned_new_pages > args.max_new_pages:
        reasons.append(f"计划新页 {planned_new_pages} 超过 max-new-pages={args.max_new_pages}")
    if conflict_targets >= 4:
        reasons.append(f"潜在冲突目标页 {conflict_targets} 个，建议先审计 conflict_alerts")
    if low_quality_clusters >= 2:
        reasons.append(f"低质量主题簇 {low_quality_clusters} 个，建议先补 topic-hints 或拆分主题")

    if reasons:
        suggestions.extend(
            [
                "先用 suggest-only 复核 topic_clusters / conflict_alerts / backfill_suggestions。",
                "必要时收紧 --max-new-pages 或提高 --backlink-min-score。",
                "若必须 full-apply，优先分批执行，不要一次处理整批旧页。",
            ]
        )
        return RiskGateDecision(blocked=True, level="high", reasons=reasons, suggestions=suggestions)

    if args.mode == "full-apply" and args.apply_backlinks:
        suggestions.append("当前批次未触发硬门禁，但仍建议保留 reports 作为审计留痕。")
        return RiskGateDecision(blocked=False, level="medium", reasons=[], suggestions=suggestions)

    return RiskGateDecision(blocked=False, level="low", reasons=[], suggestions=suggestions)








def build_backlink_line(cluster: TopicCluster, new_title: str, page: ExistingPage) -> str:
    relation = cluster.name
    if cluster.hints_hit:
        relation = cluster.hints_hit[0]
    return f"- 与 [[{new_title}]] 的关系：该页与“{relation}”主题存在直接关联，建议互相引用补全知识网络。"


def propose_backlinks(cluster: TopicCluster, new_title: str, existing_pages: list[ExistingPage]) -> list[BacklinkSuggestion]:

    suggestions: list[BacklinkSuggestion] = []
    for page in existing_pages:
        related_candidate = similarity_score(cluster, page)
        if not related_candidate or related_candidate.score < 5:
            continue
        if new_title in page.related or f"[[{new_title}]]" in page.text:
            continue

        section_hint, section_found = detect_backlink_section(page.text)
        reasons = list(related_candidate.reasons)
        if section_found:
            reasons.append(f"可落点章节: {section_hint}")
        else:
            reasons.append("未找到明确关联章节，建议人工决定插入位置")

        suggestions.append(
            BacklinkSuggestion(
                target_title=page.title,
                target_path=page.path,
                score=related_candidate.score + (2 if section_found else 0),
                reasons=reasons,
                suggested_line=build_backlink_line(cluster, new_title, page),
                section_hint=section_hint,
                section_found=section_found,
            )
        )

    suggestions.sort(key=lambda x: (-x.score, x.target_title))
    return suggestions[:6]



def build_tag_type_map(pages: list[ExistingPage]) -> dict[str, dict[str, int]]:
    """从已有页面统计 tag→type 分布，返回 {tag: {type: count}}"""
    VALID_TYPES = {"guide", "topic", "strategy", "concept", "paper"}
    tag_map: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for page in pages:
        ptype = page.page_type if page.page_type and page.page_type != "unknown" else "guide"
        if ptype not in VALID_TYPES:
            ptype = "guide"  # 非标准类型降级为 guide
        for tag in page.tags:
            tag_map[tag][ptype] += 1
    return dict(tag_map)


def infer_type_from_tags(cluster: TopicCluster, tag_type_map: dict[str, dict[str, int]]) -> dict[str, int]:
    """根据簇的 tags/hints，从已有页面的 tag→type 分布推断类型加分"""
    boosts: dict[str, int] = defaultdict(int)
    seen_types = set()
    for tag in cluster.hints_hit:
        if tag in tag_type_map:
            for ptype, count in tag_type_map[tag].items():
                if ptype not in seen_types:
                    boosts[ptype] += min(count, 3)
                    seen_types.add(ptype)
    # 也检查 cluster name 中的 token
    name_tokens = set(tokenize(cluster.name, drop_noise=False))
    for tag, type_dist in tag_type_map.items():
        if tag in name_tokens:
            total = sum(type_dist.values())
            for ptype, count in type_dist.items():
                if count / total >= 0.5:  # 多数表决
                    boosts[ptype] += 2
    return dict(boosts)


def infer_page_types(cluster: TopicCluster, source_type: str,
                     existing_pages: list[ExistingPage] | None = None) -> tuple[list[str], dict[str, int], str]:
    """返回 (候选类型列表, 各类型总分, 可解释文本)"""
    scores = {key: 0 for key in ["guide", "topic", "strategy", "concept", "paper"]}
    hit_details: dict[str, list[str]] = {key: [] for key in scores}
    texts = []
    for doc in cluster.docs[:6]:
        texts.append(doc.title)
        texts.append(doc.preview)
        texts.extend(doc.tags[:5])
        texts.extend(doc.tokens[:20])
    hay = "\n".join(texts).lower()

    keyword_map = {
        "strategy": ["策略", "交易", "信号", "参数", "回测", "品种", "择时", "仓位"],
        "concept": ["概念", "定义", "机制", "原理", "边界", "误区", "本质", "解释"],
        "paper": ["paper", "论文", "文献", "研究", "作者", "样本", "结论", "局限", "启发"],
        "topic": ["专题", "主题", "综述", "系列", "汇总", "框架", "脉络", "共识"],
        "guide": ["指南", "入门", "guide", "手册", "清单", "流程", "步骤"],
    }
    for page_type, keywords in keyword_map.items():
        for kw in keywords:
            if kw.lower() in hay:
                scores[page_type] += 2
                hit_details[page_type].append(f"关键词[{kw}]+2")

    for doc in cluster.docs[:3]:
        title_l = doc.title.lower()
        checks = [
            ("paper", ["paper", "论文", "研报"]),
            ("strategy", ["策略", "信号", "系统"]),
            ("concept", ["概念", "定义", "机制", "原理"]),
            ("topic", ["专题", "综述", "系列", "框架"]),
            ("guide", ["入门", "指南", "guide"]),
        ]
        for ptype, tokens in checks:
            if any(t in title_l for t in tokens):
                scores[ptype] += 4
                hit_details[ptype].append(f"标题特征[{tokens[0]}]+4")

    if source_type in {"report", "pdf"}:
        scores["paper"] += 1
        hit_details["paper"].append(f"来源类型[{source_type}]+1")
    if source_type == "note":
        scores["concept"] += 1
        hit_details["concept"].append(f"来源类型[{source_type}]+1")
    if source_type == "wechat":
        scores["topic"] += 1
        hit_details["topic"].append(f"来源类型[{source_type}]+1")

    # 标签→类型推理（基于已有页面的 tag-type 统计）
    if existing_pages:
        tag_type_map = build_tag_type_map(existing_pages)
        tag_boosts = infer_type_from_tags(cluster, tag_type_map)
        for ptype, boost in tag_boosts.items():
            if ptype not in scores:
                continue  # 跳过非标准类型（如 tool）
            scores[ptype] += boost
            hit_details[ptype].append(f"标签推断[已有页统计]+{boost}")

    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    best_score = ordered[0][1]
    if best_score <= 0:
        breakdown = "所有类型得分均为0，选择默认 [guide, topic]"
        return ["guide", "topic"], scores, breakdown

    candidates = [page_type for page_type, score in ordered if score >= max(best_score - 2, 1)]
    if "guide" not in candidates and len(candidates) == 1 and best_score < 4:
        candidates.append("guide")

    # 生成可解释文本
    lines = ["### 各类型得分详情"]
    for ptype, score in ordered:
        hits = hit_details.get(ptype, [])
        reason_str = f"  →  {'、'.join(hits)}" if hits else "  →  无命中"
        lines.append(f"- **{ptype}**: {score} 分{reason_str}")
    lines.append(f"- 最佳类型: {candidates[0]} (与第2名分差: {best_score - ordered[1][1] if len(ordered)>1 else 'N/A'})")
    breakdown = "\n".join(lines)
    return candidates[:3], scores, breakdown



def choose_page_type(cluster: TopicCluster, source_type: str,
                     existing_pages: list[ExistingPage] | None = None) -> tuple[str, list[str], dict[str, int], str]:
    candidates, scores, breakdown = infer_page_types(cluster, source_type, existing_pages)
    if not candidates:
        return "guide", ["guide"], scores, breakdown
    return candidates[0], candidates, scores, breakdown



def load_existing_pages(wiki_dir: Path) -> list[ExistingPage]:
    """从 wiki 目录加载所有已存在页面，含 inbound_refs 回填"""
    pages: list[ExistingPage] = []
    for fp in sorted(wiki_dir.glob("*.md")):
        text = read_text_safe(fp)
        title = extract_title(fp, text)
        page_type = "unknown"
        m = re.search(r'^type:\s*"?(.*?)"?$', text, flags=re.M)
        if m:
            page_type = m.group(1).strip()
        tags = extract_frontmatter_list(text, "tags")
        related = extract_frontmatter_list(text, "related")
        pages.append(
            ExistingPage(
                title=title,
                page_type=page_type,
                tags=tags,
                related=related,
                path=str(fp),
                text=text,
                inbound_refs=0,
            )
        )
    inbound_map = compute_inbound_refs(pages)
    for page in pages:
        page.inbound_refs = inbound_map.get(page.title, 0)
    return pages


def slugify_title(title: str) -> str:
    """把标题转为安全文件名，保留中英文和数字"""
    slug = re.sub(r"[\\/:*?\"<>|]", "_", title)
    slug = re.sub(r"\s+", "_", slug)
    slug = slug.strip("._")
    if not slug:
        slug = "untitled"
    return slug


def build_page_title(raw_dir: Path, cluster: TopicCluster) -> str:
    batch_name = raw_dir.name
    source_prefix = batch_name.split("_", 1)[1] if "_" in batch_name else batch_name
    months = [m for m in cluster.months if m != "unknown"]
    month_suffix = ""
    if months:
        month_suffix = f"（{months[0]}~{months[-1]}）" if len(months) > 1 else f"（{months[0]}）"

    base = cluster.name.strip("-—_ ") or "未分类主题"
    suffix_map = {
        "guide": "指南",
        "topic": "专题",
        "strategy": "策略",
        "concept": "概念",
        "paper": "论文笔记",
    }
    suffix = suffix_map.get(cluster.page_type, "专题")
    if not base.endswith(("指南", "专题", "策略", "概念", "论文笔记", "研究", "框架", "方法")):
        base = f"{base}{suffix}"
    return f"{source_prefix}：{base}{month_suffix}"



def extract_type_content(cluster: TopicCluster, page_type: str) -> dict[str, list[str]]:
    """从 raw 文档中按 type 提取内容，填充模板各段"""
    result: dict[str, list[str]] = defaultdict(list)
    keyword_map = {
        "strategy": {
            "核心逻辑": ["核心", "逻辑", "基于", "策略", "框架"],
            "信号结构": ["信号", "条件", "触发", "入场", "出场"],
            "适用品种": ["品种", "适用", "合约", "标的"],
            "参数": ["参数", "周期", "窗口", "阈值", "倍数"],
            "风险": ["风险", "回撤", "止损", "杠杆", "风控"],
        },
        "concept": {
            "定义": ["定义", "指", "即", "就是", "指的是", "概念"],
            "机制": ["机制", "过程", "如何", "通过", "影响"],
            "边界": ["边界", "局限", "条件", "注意", "限制", "仅当"],
            "常见误区": ["误区", "误解", "不是", "不同于", "切忌"],
        },
        "paper": {
            "研究问题": ["提出", "研究", "问题", "目标", "旨在", "探索"],
            "方法": ["方法", "模型", "采用", "使用", "利用", "算法"],
            "结论": ["结论", "发现", "结果表明", "因此", "验证"],
            "局限": ["局限", "不足", "未能", "挑战", "改进"],
            "启发": ["启发", "启示", "参考", "借鉴", "价值"],
        },
        "guide": {
            "使用路径": ["步骤", "流程", "路径", "顺序", "阶段"],
            "推荐阅读顺序": ["推荐", "建议", "先", "后", "阅读"],
            "落地步骤": ["实施", "落地", "部署", "配置", "操作"],
        },
        "topic": {},
    }
    kw_config = keyword_map.get(page_type, {})
    if not kw_config:
        return dict(result)
    for doc in cluster.docs[:10]:
        sentences = extract_key_sentences(doc.text or doc.preview, limit=15)
        for section, keywords in kw_config.items():
            for sent in sentences:
                if any(kw in sent for kw in keywords):
                    if sent not in result[section]:
                        result[section].append(sent)
        if all(len(result[s]) >= 3 for s in kw_config):
            break
    # 每段最多留3条
    for section in list(result.keys()):
        result[section] = result[section][:3]
    return dict(result)


def render_page(title: str, source: str, tags: list[str], related: list[str], raw_sources: list[str], cluster: TopicCluster) -> str:
    page_type = cluster.page_type or "guide"
    tag_text = ", ".join(tags[:8]) if tags else "待整理"
    clean_related = sanitize_related_titles(related, self_title=title)
    related_text = ", ".join(clean_related) if clean_related else ""
    raw_lines = "\n".join(f"- `{x}`" for x in raw_sources[:20])
    today = datetime.now().strftime("%Y-%m-%d")
    anchor = "、".join(cluster.anchor_terms[:6]) if cluster.anchor_terms else "待补充"
    months = "、".join(cluster.months) if cluster.months else "unknown"
    summary = cluster.summary or f"该页由一批 raw 资料自动聚合生成，当前为{page_type}草稿，主题核心围绕：{cluster.name}。"
    consensus_lines = "\n".join(f"- {point}" for point in (cluster.consensus_points or ["待补充人工共识提炼"]))
    divergence_lines = "\n".join(f"- {point}" for point in (cluster.divergence_points or ["当前未识别明显分歧，建议后续人工复核"]))
    evidence_lines = "\n".join(
        f"- `{doc.rel_path}`：{(extract_key_sentences(doc.text or doc.preview, limit=1) or [doc.preview[:80]])[0]}"
        for doc in cluster.docs[:5]
    )
    candidate_line = "、".join(cluster.page_type_candidates) if cluster.page_type_candidates else page_type

    # 自动填充模板内容
    content_by_section = extract_type_content(cluster, page_type)
    section_template_map = {
        "guide": ["使用路径", "推荐阅读顺序", "落地步骤"],
        "strategy": ["核心逻辑", "信号结构", "适用品种", "风险", "参数"],
        "concept": ["定义", "机制", "边界", "常见误区"],
        "paper": ["研究问题", "方法", "结论", "局限", "启发"],
        "topic": [],
    }
    sections = section_template_map.get(page_type, [])
    type_block_parts = []
    for section in sections:
        entries = content_by_section.get(section, [])
        if entries:
            lines = f"## {section}\n" + "\n".join(f"- {s}" for s in entries) + "\n"
        else:
            lines = f"## {section}\n\n- 待补充\n"
        type_block_parts.append(lines)
    # topic 类型用摘要/共识/分歧
    if page_type == "topic":
        type_block_parts = [
            f"## 主题摘要\n\n{summary}\n",
            f"## 共识\n\n{consensus_lines}\n",
            f"## 分歧\n\n{divergence_lines}\n",
        ]
    type_block = "\n".join(type_block_parts)

    return f'''---
title: "{title}"
type: "{page_type}"
source: "{source}"
created: {today}
updated: {today}
tags: [{tag_text}]
related: [{related_text}]
status: growing
confidence: {cluster.quality_score}
---

# {title}

## 核心结论

{summary}

## 页面判定

- 主类型：{page_type}
- 候选类型：{candidate_line}
- 当前主题：{cluster.name}
- 来源批次：{source}
- 覆盖月份：{months}
- 主题锚点：{anchor}
- 聚类质量分：{cluster.quality_score}

{type_block}

## 证据摘录

{evidence_lines}

## 与其他知识的关联

- 当前 related 基于页面类型、标题、标签、主题锚点综合生成。
- 若需补旧页正文回链，建议先审阅 `backfill_suggestions` 报告再执行。

## 风险提示

- 当前内容为草稿页，不应直接视为成熟知识结论。
- 自动聚类、页面类型判定与链接仍可能存在边界偏差，需要二次审阅。

---

**Raw Sources**:
{raw_lines}
'''




def extract_wikilinks(text: str) -> list[str]:
    return [item.strip() for item in re.findall(r"\[\[([^\]]+)\]\]", text) if item.strip()]



def compute_inbound_refs(pages: list[ExistingPage]) -> dict[str, int]:
    inbound = Counter()
    for page in pages:
        linked_titles = set(extract_wikilinks(page.text))
        linked_titles.update(item.replace("[[", "").replace("]]", "") for item in page.related)
        for title in linked_titles:
            inbound[title] += 1
    return dict(inbound)



def build_existing_page_map(pages: list[ExistingPage]) -> dict[str, ExistingPage]:
    return {page.title: page for page in pages}



def assess_wiki_health(pages: list[ExistingPage], index_path: Path, log_path: Path) -> WikiHealthReport:
    report = WikiHealthReport(total_pages=len(pages))
    page_map = build_existing_page_map(pages)
    inbound = compute_inbound_refs(pages)
    index_text = read_text_safe(index_path) if index_path.exists() else ""
    log_text = read_text_safe(log_path) if log_path.exists() else ""

    for page in pages:
        body_links = set(extract_wikilinks(page.text))
        related_links = {item.replace("[[", "").replace("]]", "") for item in page.related}
        combined = (body_links | related_links) & set(page_map.keys())

        if page.text.startswith("---\n") and not re.search(r"^related:\s*\[.*\]\s*$", page.text, flags=re.M):
            report.frontmatter_issues.append(f"{page.title}: frontmatter 缺少 related 字段")
        if page.text.startswith("---\n") and not re.search(r'^type:\s*"?.+?"?$', page.text, flags=re.M):
            report.frontmatter_issues.append(f"{page.title}: frontmatter 缺少 type 字段")

        if not related_links and inbound.get(page.title, 0) == 0 and not combined:
            report.island_pages.append(page.title)
        if len(body_links) == 0 and len(related_links) == 0 and len(extract_key_sentences(page.text, limit=2)) < 2:
            report.pseudo_existing_pages.append(page.title)

        for target in sorted(combined):
            target_page = page_map.get(target)
            if not target_page or target == page.title:
                continue
            target_body_links = set(extract_wikilinks(target_page.text))
            target_related_links = {item.replace("[[", "").replace("]]", "") for item in target_page.related}
            if page.title not in target_body_links and page.title not in target_related_links:
                report.single_direction_links.append(f"{page.title} -> {target}")

        body_only = sorted((body_links & set(page_map.keys())) - related_links)
        for target in body_only:
            report.related_missing_links.append(f"{page.title}: 正文已链接 [[{target}]]，但 related 未同步")

        related_only = sorted(related_links - body_links)
        for target in related_only:
            if target in page_map:
                report.body_missing_links.append(f"{page.title}: related 已含 [[{target}]]，但正文未承接")

        index_marker = f"`wiki/{Path(page.path).name}`"
        if index_text and index_marker not in index_text:
            report.index_missing_pages.append(page.title)
        if log_text and not log_contains_page(log_text, page):
            report.log_missing_pages.append(page.title)


    return report


def log_contains_page(log_text: str, page: ExistingPage) -> bool:
    file_name = Path(page.path).name
    slug_name = f"{slugify_title(page.title)}.md"
    candidates = {
        f"`wiki/{file_name}`",
        f"`wiki/{slug_name}`",
        f"[[{page.title}]]",
    }
    return any(marker in log_text for marker in candidates)



def normalize_related_value(raw: str, self_title: str | None = None) -> str:
    items = extract_frontmatter_list(raw, "related")
    dedup = sanitize_related_titles(items, self_title=self_title)
    return f"related: [{', '.join(dedup)}]"





def normalize_related_format(wiki_dir: Path) -> list[str]:
    actions = []
    for fp in sorted(wiki_dir.glob("*.md")):
        text = read_text_safe(fp)
        match = re.search(r"^related:\s*\[(.*?)\]\s*$", text, flags=re.M)
        if not match:
            continue
        old_line = match.group(0)
        self_title = extract_title(fp, text)
        new_line = normalize_related_value(old_line, self_title=self_title)
        if new_line == old_line:
            continue
        new_text = text[:match.start()] + new_line + text[match.end():]
        fp.write_text(new_text, encoding="utf-8")
        actions.append(f"规范化 related: {fp.name}")
    return actions



def resolve_task(args: argparse.Namespace) -> str:
    if getattr(args, "task", ""):
        return args.task
    if args.health_only and args.repair:
        return "repair"
    if args.health_only:
        return "health-check"
    return "ingest"


def run_health_task(wiki_root: Path, wiki_dir: Path, reports_dir: Path, index_path: Path, log_path: Path,
                    raw_dir: Path | None, mode: str, do_repair: bool,
                    fix_limit_related: int, fix_limit_body: int,
                    normalize_related: bool) -> tuple[WikiHealthReport, list[str], Path]:
    existing_pages = load_existing_pages(wiki_dir)
    wiki_health = assess_wiki_health(existing_pages, index_path, log_path)
    page_map = build_existing_page_map(existing_pages)
    maintenance_actions = []
    should_normalize_related = normalize_related or (do_repair and mode in {"safe-apply", "full-apply"})
    if do_repair:
        maintenance_actions.extend(
            apply_health_fixes(
                wiki_health, page_map, mode,
                fix_limit_related, fix_limit_body,
                index_path=index_path, log_path=log_path,
                wiki_dir=wiki_dir, raw_dir=raw_dir,
            )
        )
        if should_normalize_related:
            maintenance_actions.extend(normalize_related_format(wiki_dir))
    wiki_health.maintenance_actions.extend(maintenance_actions)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = reports_dir / f"{timestamp}_wiki_health.md"
    health_lines = build_health_lines(wiki_health)
    write_report(report_path, "Wiki Health", "\n".join(health_lines))
    return wiki_health, maintenance_actions, report_path


def run_smoke_tests(script_path: Path, wiki_root: Path, raw_dir: Path, topic_hints: str) -> list[str]:
    commands = [
        (
            "ingest-suggest",
            [sys.executable, str(script_path), "--task", "ingest", "--raw-dir", str(raw_dir), "--wiki-root", str(wiki_root), "--mode", "suggest-only", "--topic-hints", topic_hints, "--source-type", "wechat", "--max-new-pages", "1"],
        ),
        (
            "ingest-safe",
            [sys.executable, str(script_path), "--task", "ingest", "--raw-dir", str(raw_dir), "--wiki-root", str(wiki_root), "--mode", "safe-apply", "--topic-hints", topic_hints, "--source-type", "wechat", "--max-new-pages", "1"],
        ),
        (
            "ingest-full",
            [sys.executable, str(script_path), "--task", "ingest", "--raw-dir", str(raw_dir), "--wiki-root", str(wiki_root), "--mode", "full-apply", "--topic-hints", topic_hints, "--source-type", "wechat", "--max-new-pages", "1"],
        ),
        (
            "health-task",
            [sys.executable, str(script_path), "--task", "health-check", "--wiki-root", str(wiki_root), "--mode", "suggest-only"],
        ),
        (
            "health-legacy",
            [sys.executable, str(script_path), "--health-only", "--wiki-root", str(wiki_root), "--mode", "suggest-only"],
        ),
        (
            "repair-task",
            [sys.executable, str(script_path), "--task", "repair", "--wiki-root", str(wiki_root), "--mode", "safe-apply", "--fix-limit-related", "3", "--fix-limit-body", "2"],
        ),
        (
            "repair-legacy",
            [sys.executable, str(script_path), "--health-only", "--repair", "--wiki-root", str(wiki_root), "--mode", "safe-apply", "--fix-limit-related", "3", "--fix-limit-body", "2"],
        ),
    ]
    results = []
    for idx, (label, command) in enumerate(commands, start=1):
        proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="ignore")

        status = "PASS" if proc.returncode == 0 else "FAIL"
        tail = (proc.stdout or proc.stderr or "").strip().splitlines()
        tail_line = tail[-1] if tail else ""
        results.append(f"[{status}] smoke-{idx}:{label} :: {tail_line}")
        if proc.returncode != 0:
            break
    return results




def create_assertion_test_fixture(base_dir: Path) -> tuple[Path, Path, Path]:
    wiki_root = base_dir / "wiki-root"
    wiki_dir = wiki_root / "wiki"
    reports_dir = base_dir / ".workbuddy" / "artifacts" / "wiki-kb-builder-reports"
    raw_parent = base_dir / "raw-batches"
    raw_dir = raw_parent / "2026-04"

    wiki_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    (wiki_root / "index.md").write_text("# Index\n", encoding="utf-8")
    (wiki_root / "log.md").write_text("# Log\n", encoding="utf-8")

    (wiki_dir / "alpha.md").write_text(
        "---\n"
        "title: \"Alpha\"\n"
        "type: \"guide\"\n"
        "related: [Beta, [[Beta]], 'Gamma', \"Gamma\"]\n"
        "---\n\n"
        "# Alpha\n\n"
        "Alpha 正文。\n",
        encoding="utf-8",
    )
    (wiki_dir / "beta.md").write_text(
        "---\n"
        "title: \"Beta\"\n"
        "type: \"guide\"\n"
        "related: [Alpha]\n"
        "---\n\n"
        "# Beta\n\n"
        "Beta 正文。\n\n"

        "## 与其他知识的关联\n",
        encoding="utf-8",
    )
    (wiki_dir / "gamma.md").write_text(
        "---\n"
        "title: \"Gamma\"\n"
        "type: \"guide\"\n"
        "related: []\n"
        "---\n\n"
        "# Gamma\n\n"
        "Gamma 正文。\n\n"
        "## 与其他知识的关联\n",
        encoding="utf-8",
    )

    (raw_dir / "sample.md").write_text(
        "---\n"
        "title: \"因子研究样本\"\n"
        "---\n\n"
        "# 因子研究样本\n\n"
        "这是一条用于断言型测试的 raw 样本。\n",
        encoding="utf-8",
    )
    return wiki_root, raw_dir, reports_dir



def assert_equal(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise AssertionError(f"{label} mismatch: expected={expected!r}, actual={actual!r}")



def run_assertion_tests() -> list[str]:
    results = []
    with tempfile.TemporaryDirectory(prefix="wiki-kb-builder-assert-") as tmp:
        base_dir = Path(tmp)
        wiki_root, raw_dir, reports_dir = create_assertion_test_fixture(base_dir)
        wiki_dir = wiki_root / "wiki"
        index_path = wiki_root / "index.md"
        log_path = wiki_root / "log.md"

        normalized = normalize_related_value("related: [Beta, [[Beta]], 'Gamma', \"Gamma\"]")
        assert_equal("normalize_related_value", normalized, "related: [Beta, Gamma]")

        self_filtered = normalize_related_value(
            "related: [Alpha, Beta, [[Alpha]], 'Gamma']",
            self_title="Alpha",
        )
        assert_equal("normalize_related_value self filter", self_filtered, "related: [Beta, Gamma]")


        results.append("[PASS] assert-normalize-related-value")
        results.append("[PASS] assert-normalize-related-self-filter")


        _, repair_actions, _ = run_health_task(
            wiki_root, wiki_dir, reports_dir, index_path, log_path,
            raw_dir=None, mode="safe-apply", do_repair=True,
            fix_limit_related=3, fix_limit_body=2,
            normalize_related=False,
        )
        alpha_text = read_text_safe(wiki_dir / "alpha.md")
        assert_equal(
            "repair related normalization",
            "related: [Beta, Gamma]" in alpha_text,
            True,
        )

        beta_text = read_text_safe(wiki_dir / "beta.md")
        assert_equal(
            "safe-apply body backlink inserted",
            "- 关联页：[[Alpha]]" in beta_text,
            True,
        )

        log_text = read_text_safe(log_path)
        existing_after_repair = load_existing_pages(wiki_dir)
        alpha_page = next(page for page in existing_after_repair if page.title == "Alpha")
        beta_page = next(page for page in existing_after_repair if page.title == "Beta")
        assert_equal(
            "safe-apply log backfill appended",
            "WIKI-HEALTH-FIX" in log_text and log_contains_page(log_text, alpha_page) and log_contains_page(log_text, beta_page),
            True,
        )


        reassessed_health = assess_wiki_health(load_existing_pages(wiki_dir), index_path, log_path)
        assert_equal(
            "health-check recognizes slug log entries",
            reassessed_health.log_missing_pages,
            [],
        )



        assert_equal(

            "repair normalization action logged",
            any("规范化 related: alpha.md" == item for item in repair_actions),
            True,
        )
        assert_equal(
            "safe-apply body action logged",
            any("补正文承接: Beta -> Alpha" == item for item in repair_actions),
            True,
        )
        assert_equal(
            "safe-apply log action logged",
            any(item.startswith("补 log 承接: ") for item in repair_actions),
            True,
        )
        results.append("[PASS] assert-repair-normalizes-related")
        results.append("[PASS] assert-safe-apply-backfills-body")
        results.append("[PASS] assert-safe-apply-backfills-log")
        results.append("[PASS] assert-health-recognizes-slug-log")



        legacy_health = run_health_task(
            wiki_root, wiki_dir, reports_dir, index_path, log_path,
            raw_dir=None, mode="suggest-only", do_repair=False,
            fix_limit_related=3, fix_limit_body=2,
            normalize_related=False,
        )[0]
        modern_health = run_health_task(
            wiki_root, wiki_dir, reports_dir, index_path, log_path,
            raw_dir=None, mode="suggest-only", do_repair=False,
            fix_limit_related=3, fix_limit_body=2,
            normalize_related=False,
        )[0]
        assert_equal("health total_pages", legacy_health.total_pages, modern_health.total_pages)
        assert_equal("health single_direction_links", legacy_health.single_direction_links, modern_health.single_direction_links)
        assert_equal("health related_missing_links", legacy_health.related_missing_links, modern_health.related_missing_links)
        results.append("[PASS] assert-health-check-equivalence")

        update_index(index_path, wiki_dir, raw_dir)
        index_text = read_text_safe(index_path)
        assert_equal(
            "cross-root raw path display",
            "raw-batches/2026-04/" in index_text or raw_dir.as_posix() in index_text,
            True,
        )
        results.append("[PASS] assert-cross-root-index-path")

    return results



def build_health_lines(wiki_health: WikiHealthReport) -> list[str]:
    lines = [
        "## 全库维护体检",
        "",
        f"- 总页面数: {wiki_health.total_pages}",
        f"- 单向链接数: {len(wiki_health.single_direction_links)}",
        f"- 正文已链接但 related 未同步: {len(wiki_health.related_missing_links)}",
        f"- related 已含但正文未承接: {len(wiki_health.body_missing_links)}",
        f"- 孤岛页数: {len(wiki_health.island_pages)}",
        f"- 伪存在页数: {len(wiki_health.pseudo_existing_pages)}",
        f"- frontmatter 问题数: {len(wiki_health.frontmatter_issues)}",
        f"- index 缺失页数: {len(wiki_health.index_missing_pages)}",
        f"- log 缺失页数: {len(wiki_health.log_missing_pages)}",
        "",
    ]
    if wiki_health.single_direction_links:
        lines.append("### 单向链接")
        lines.extend([f"- {item}" for item in wiki_health.single_direction_links[:30]])
        lines.append("")
    if wiki_health.related_missing_links:
        lines.append("### related 未同步")
        lines.extend([f"- {item}" for item in wiki_health.related_missing_links[:30]])
        lines.append("")
    if wiki_health.body_missing_links:
        lines.append("### 正文未承接")
        lines.extend([f"- {item}" for item in wiki_health.body_missing_links[:30]])
        lines.append("")
    if wiki_health.island_pages:
        lines.append("### 孤岛页")
        lines.extend([f"- {item}" for item in wiki_health.island_pages[:30]])
        lines.append("")
    if wiki_health.pseudo_existing_pages:
        lines.append("### 伪存在页")
        lines.extend([f"- {item}" for item in wiki_health.pseudo_existing_pages[:30]])
        lines.append("")
    if wiki_health.frontmatter_issues:
        lines.append("### frontmatter 问题")
        lines.extend([f"- {item}" for item in wiki_health.frontmatter_issues[:30]])
        lines.append("")
    if wiki_health.index_missing_pages:
        lines.append("### index 未收录")
        lines.extend([f"- {item}" for item in wiki_health.index_missing_pages[:30]])
        lines.append("")
    if wiki_health.log_missing_pages:
        lines.append("### log 未承接")
        lines.extend([f"- {item}" for item in wiki_health.log_missing_pages[:30]])
        lines.append("")
    if wiki_health.maintenance_actions:
        lines.append("### 批量修复动作")
        lines.extend([f"- {item}" for item in wiki_health.maintenance_actions])
        lines.append("")
    return lines








def apply_health_fixes(health: WikiHealthReport, page_map: dict[str, ExistingPage], mode: str,
                        fix_limit_related: int = 20, fix_limit_body: int = 10,
                        index_path: Path | None = None, log_path: Path | None = None,
                        wiki_dir: Path | None = None, raw_dir: Path | None = None) -> list[str]:
    actions = []
    if mode not in {"safe-apply", "full-apply"}:
        return actions

    # 1. 补 related frontmatter（正文已链但 related 未同步）
    for item in health.related_missing_links[:fix_limit_related]:
        page_title, rest = item.split(": ", 1)
        target_match = re.search(r"\[\[(.+?)\]\]", rest)
        if not target_match:
            continue
        target_title = target_match.group(1)
        page = page_map.get(page_title)
        if not page:
            continue
        if update_related_frontmatter(Path(page.path), target_title):
            actions.append(f"补全 related: {page_title} -> {target_title}")

    # 2. safe/full-apply：补正文承接
    for item in health.body_missing_links[:fix_limit_body]:
        page_title, rest = item.split(": ", 1)
        target_match = re.search(r"\[\[(.+?)\]\]", rest)
        if not target_match:
            continue
        target_title = target_match.group(1)
        page = page_map.get(page_title)
        if not page:
            continue
        marker, found = detect_backlink_section(page.text)
        if not found:
            continue
        suggested_line = f"- 关联页：[[{target_title}]]"
        new_text = insert_backlink_into_section(page.text, marker, suggested_line)
        if not new_text or new_text == page.text:
            continue
        Path(page.path).write_text(new_text, encoding="utf-8")
        page.text = new_text
        actions.append(f"补正文承接: {page_title} -> {target_title}")

    # 3. 补 frontmatter type 字段（full-apply 模式）
    if mode == "full-apply" and health.frontmatter_issues:
        for issue in health.frontmatter_issues:
            m = re.match(r"^(.+?): frontmatter 缺少 type 字段", issue)
            if not m:
                continue
            page = page_map.get(m.group(1))
            if not page:
                continue
            text = read_text_safe(Path(page.path))
            # 在 frontmatter 结束前插入 type: guide
            end_m = re.search(r"\n---\n", text[4:])
            if not end_m:
                continue
            insert_pos = 4 + end_m.start()
            if "type:" not in text[:insert_pos]:
                new_text = text[:insert_pos] + '\ntype: "guide"' + text[insert_pos:]
                Path(page.path).write_text(new_text, encoding="utf-8")
                page.text = new_text
                actions.append(f"补 frontmatter type: {page.title} -> guide")

    # 4. 孤岛页处理：找最相关页面加 related（full-apply）
    if mode == "full-apply" and health.island_pages:
        non_island = [p for p in page_map.values() if p.title not in health.island_pages]
        for island_title in health.island_pages:
            page = page_map.get(island_title)
            if not page:
                continue
            # 找最相似的非孤岛页
            best = find_best_island_bridge(page, non_island)
            if best and update_related_frontmatter(Path(page.path), best.title):
                actions.append(f"孤岛页建链: {island_title} -> {best.title}")

    # 5. 修复 index/log（full-apply 模式）
    if mode == "full-apply" and index_path and wiki_dir:
        update_index(index_path, wiki_dir, raw_dir or index_path.parent)
        wiki_page_count = len(list(wiki_dir.glob("*.md")))
        actions.append(f"重建 index.md ({wiki_page_count} 页)")

    if log_path and health.log_missing_pages:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        added = [f"- `wiki/{slugify_title(p)}.md`" for p in health.log_missing_pages[:30]]
        log_entry = [
            "",
            f"### {now} — WIKI-HEALTH-FIX",
            f"- 操作: 批量补 log 缺失",
            f"- 修复页数: {len(health.log_missing_pages)}",
            *added,
        ]
        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(log_entry) + "\n")
        actions.append(f"补 log 承接: {len(health.log_missing_pages)} 页")

    return actions



def find_best_island_bridge(page: ExistingPage, candidates: list[ExistingPage]) -> ExistingPage | None:
    """为孤岛页找到最相似的非孤岛页做桥接"""
    page_tokens = set(tokenize(page.title, drop_noise=False))
    best_score = 0
    best = None
    for candidate in candidates:
        cand_tokens = set(tokenize(candidate.title, drop_noise=False))
        overlap = len(page_tokens & cand_tokens)
        # 也检查标签重叠
        tag_overlap = len(set(page.tags) & set(candidate.tags))
        score = overlap * 3 + tag_overlap * 2
        if score > best_score:
            best_score = score
            best = candidate
    return best if best_score > 0 else None



def to_display_path(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()



def update_index(index_path: Path, wiki_dir: Path, raw_dir: Path) -> None:

    wiki_files = sorted(wiki_dir.glob("*.md"))
    rows = []
    type_counter = Counter()
    for fp in wiki_files:
        text = read_text_safe(fp)
        title = extract_title(fp, text)
        m = re.search(r'^type:\s*\"?(.*?)\"?$', text, flags=re.M)
        page_type = m.group(1).strip() if m else "unknown"
        type_counter[page_type] += 1
        rows.append(f"- **{title}** ({page_type}) - `wiki/{fp.name}`")

    stats = [
        "# Wiki-KB 全局索引",
        "",
        "> 所有知识页面的目录。每次 INGEST 后自动更新。",
        "",
        "---",
        "",
        "## 统计",
        "",
        "| 类型 | 数量 |",
        "|------|------|",
        f"| 总页面数 | {len(wiki_files)} |",
    ]
    for k, v in sorted(type_counter.items()):
        stats.append(f"| {k} | {v} |")

    body = "\n".join(stats) + "\n\n---\n\n## 页面列表\n\n" + "\n".join(rows)
    body += f"\n\n---\n\n## 原始资料批次\n\n- `{to_display_path(raw_dir, index_path.parent)}/`\n"

    body += f"\n---\n\n*最后更新: {datetime.now().strftime('%Y-%m-%d')}*\n"
    index_path.write_text(body, encoding="utf-8")


def append_log(log_path: Path, raw_dir: Path, created_pages: list[str], mode: str, clusters: list[TopicCluster]) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    block = [
        "",
        f"### {now} — WIKI-BUILD",
        f"- Raw 批次: `{raw_dir.as_posix()}`",
        f"- 模式: `{mode}`",
        f"- 主题簇数: {len(clusters)}",
        f"- 新建页面数: {len(created_pages)}",
    ]
    for cluster in clusters[:8]:
        block.append(f"- 主题: `{cluster.name}` ({len(cluster.docs)} docs)")
    for page in created_pages:
        block.append(f"- 新建: `{page}`")
    with log_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(block) + "\n")


def write_report(path: Path, title: str, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}\n\n{content}", encoding="utf-8")


def apply_backlink_suggestion(suggestion: BacklinkSuggestion) -> bool:
    if not suggestion.section_found or suggestion.score < 8:
        return False

    path = Path(suggestion.target_path)
    text = read_text_safe(path)
    if suggestion.suggested_line in text:
        return False

    marker = suggestion.section_hint
    if marker not in text:
        return False

    new_text = insert_backlink_into_section(text, marker, suggestion.suggested_line)
    if not new_text or new_text == text:
        return False

    path.write_text(new_text, encoding="utf-8")
    return True



def main() -> None:

    parser = argparse.ArgumentParser(description="wiki-kb-builder enhanced")
    parser.add_argument("--task", choices=["ingest", "health-check", "repair", "test"], default="", help="任务入口：入库整理 / 全库体检 / 全库修复 / 回归测试")
    parser.add_argument("--raw-dir", required=False, default="")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument("--reports-dir", required=False, default="", help="报告输出目录（默认：工作目录/.workbuddy/artifacts/wiki-kb-builder-reports）")
    parser.add_argument("--mode", default="full-apply", choices=["suggest-only", "safe-apply", "full-apply"])
    parser.add_argument("--source-type", default="mixed")
    parser.add_argument("--topic-hints", default="")
    parser.add_argument("--max-new-pages", type=int, default=5)
    parser.add_argument("--link-depth", type=int, default=1)
    parser.add_argument("--update-existing", action="store_true")
    parser.add_argument("--bridge-strategy-pages", action="store_true")
    parser.add_argument("--apply-backlinks", action="store_true")
    parser.add_argument("--backlink-min-score", type=int, default=8)
    parser.add_argument("--health-only", action="store_true", help="旧入口兼容：仅运行全库维护体检，不扫描 raw")
    parser.add_argument("--fix-limit-related", type=int, default=20, help="批量补 related 上限（默认20）")
    parser.add_argument("--fix-limit-body", type=int, default=10, help="批量补正文承接上限（默认10）")
    parser.add_argument("--repair", action="store_true", help="旧入口兼容：health-only 模式下启用批量修复")
    parser.add_argument("--normalize-related", action="store_true", help="规范化全库 related frontmatter 格式")
    args = parser.parse_args()

    task = resolve_task(args)
    raw_dir = Path(args.raw_dir).resolve() if args.raw_dir else None
    wiki_root = Path(args.wiki_root).resolve()
    wiki_dir = wiki_root / "wiki"
    # 报告目录：默认输出到工作目录 .workbuddy/artifacts/，禁止污染 wiki-kb
    if args.reports_dir:
        reports_dir = Path(args.reports_dir).resolve()
    else:
        reports_dir = Path.cwd() / ".workbuddy" / "artifacts" / "wiki-kb-builder-reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    index_path = wiki_root / "index.md"
    log_path = wiki_root / "log.md"
    script_path = Path(__file__).resolve()

    topic_hints = [x.strip() for x in args.topic_hints.split(",") if x.strip()]

    if task in {"ingest", "test"} and not raw_dir:
        parser.error("--raw-dir is required for task=ingest/test")

    if task == "health-check":
        wiki_health, _, _ = run_health_task(
            wiki_root, wiki_dir, reports_dir, index_path, log_path,
            raw_dir, args.mode, False,
            args.fix_limit_related, args.fix_limit_body,
            normalize_related=False,
        )
        print("[DONE] wiki-kb-builder health-check")
        print(f"total_pages={wiki_health.total_pages} single_dir={len(wiki_health.single_direction_links)} "
              f"related_missing={len(wiki_health.related_missing_links)} islands={len(wiki_health.island_pages)} "
              f"fix_actions={len(wiki_health.maintenance_actions)}")
        return

    if task == "repair":
        wiki_health, maintenance_actions, _ = run_health_task(
            wiki_root, wiki_dir, reports_dir, index_path, log_path,
            raw_dir, args.mode, True,
            args.fix_limit_related, args.fix_limit_body,
            normalize_related=args.normalize_related,
        )
        print("[DONE] wiki-kb-builder repair")
        print(f"total_pages={wiki_health.total_pages} single_dir={len(wiki_health.single_direction_links)} "
              f"related_missing={len(wiki_health.related_missing_links)} islands={len(wiki_health.island_pages)} "
              f"fix_actions={len(maintenance_actions)}")
        return

    if task == "test":
        smoke_hints = args.topic_hints or "因子研究,动量策略,风险定价"
        assertion_results = run_assertion_tests()
        results = assertion_results + run_smoke_tests(script_path, wiki_root, raw_dir, smoke_hints)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = reports_dir / f"{timestamp}_smoke_test.md"
        write_report(report_path, "Smoke Test", "\n".join(["## 测试结果", "", *[f"- {line}" for line in results]]))
        print("[DONE] wiki-kb-builder test")
        print(" | ".join(results))
        return


    existing_pages = load_existing_pages(wiki_dir)
    docs = scan_raw_docs(raw_dir, topic_hints)

    clusters = cluster_docs(docs, topic_hints, args.source_type, existing_pages)
    risk_gate = assess_risk_gate(args, clusters, existing_pages)
    wiki_health = assess_wiki_health(existing_pages, index_path, log_path)
    page_map = build_existing_page_map(existing_pages)


    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    cluster_report = []

    link_report = []
    backlink_report = []
    created_pages = []
    orphan_clusters = []
    total_backlink_suggestions = 0
    applied_backlinks = 0
    related_updates = 0
    conflict_alerts = []
    preview_lines = preview_actions(args, clusters, existing_pages)
    planned_existing_updates = 0

    for cluster in clusters[: args.max_new_pages]:

        cluster.suggested_title = build_page_title(raw_dir, cluster)

        cluster_report.append(
            "\n".join(
                [
                    f"## {cluster.name}",
                    "",
                    f"- 文档数: {len(cluster.docs)}",
                    f"- 命中 hints: {', '.join(cluster.hints_hit) if cluster.hints_hit else '无'}",
                    f"- 主题锚点: {', '.join(cluster.anchor_terms[:8]) if cluster.anchor_terms else '无'}",
                    f"- 覆盖月份: {', '.join(cluster.months) if cluster.months else 'unknown'}",
                    f"- 建议标题: {cluster.suggested_title}",
                    f"- 页面类型: {cluster.page_type}",
                    f"- 候选类型: {', '.join(cluster.page_type_candidates) if cluster.page_type_candidates else cluster.page_type}",
                    f"- 聚类质量分: {cluster.quality_score}",
                    f"- 类型判定分解:",
                    *[f"  - {ptype}: {cluster.page_type_scores.get(ptype, 0)} 分" for ptype in sorted(cluster.page_type_scores.keys(), key=lambda k: -cluster.page_type_scores.get(k, 0))],
                    f"- 质量提示: {'；'.join(cluster.quality_flags) if cluster.quality_flags else '无'}",
                    f"- 自动摘要: {cluster.summary}",
                    "- 共识结论:",
                    *[f"  - {point}" for point in (cluster.consensus_points or ['无'])],
                    "- 分歧与待验证点:",
                    *[f"  - {point}" for point in (cluster.divergence_points or ['无'])],
                    "- 样本文档:",
                    *[f"  - `{doc.rel_path}` :: {doc.title}" for doc in cluster.docs[:5]],
                    "",
                ]
            )
        )

        related_candidates = propose_related(cluster, existing_pages)
        if not related_candidates:
            orphan_clusters.append(cluster.name)

        link_lines = [f"## {cluster.name}", "", f"- 建议标题: {cluster.suggested_title}"]
        if related_candidates:
            link_lines.append("- 候选 related:")
            for candidate in related_candidates:
                link_lines.append(
                    f"  - [[{candidate.title}]] | score={candidate.score} | strength={candidate.strength} | reason={'；'.join(candidate.reasons)}"
                )
        else:
            link_lines.append("- 候选 related: 无")
            link_lines.append("- 风险: 该主题可能形成孤岛页，建议补 topic-hints 或人工指定桥接页")
        link_lines.append("")
        link_report.append("\n".join(link_lines))

        backlink_suggestions = propose_backlinks(cluster, cluster.suggested_title, existing_pages)
        total_backlink_suggestions += len(backlink_suggestions)
        backfill_lines = [f"## {cluster.name}", "", f"- 新页标题: {cluster.suggested_title}"]
        if backlink_suggestions:
            backfill_lines.append("- 旧页回链建议:")
            for suggestion in backlink_suggestions:
                backfill_lines.append(
                    f"  - 目标页: [[{suggestion.target_title}]] | score={suggestion.score} | section={suggestion.section_hint}"
                )
                backfill_lines.append(f"    - reason: {'；'.join(suggestion.reasons)}")
                backfill_lines.append(f"    - suggested_line: {suggestion.suggested_line}")
                if args.mode == "full-apply" and suggestion.section_found and not risk_gate.blocked:
                    if update_related_frontmatter(Path(suggestion.target_path), cluster.suggested_title):
                        related_updates += 1
                        planned_existing_updates += 1
                        backfill_lines.append("    - related_updated: yes")
                elif args.mode == "full-apply" and suggestion.section_found and risk_gate.blocked:
                    backfill_lines.append("    - related_updated: blocked_by_risk_gate")
                if (
                    args.mode == "full-apply"
                    and args.apply_backlinks
                    and suggestion.score >= args.backlink_min_score
                    and suggestion.section_found
                    and not risk_gate.blocked
                ):
                    if apply_backlink_suggestion(suggestion):
                        applied_backlinks += 1
                        planned_existing_updates += 1
                        backfill_lines.append("    - applied: yes")
                    else:
                        backfill_lines.append("    - applied: no")
                elif (
                    args.mode == "full-apply"
                    and args.apply_backlinks
                    and suggestion.score >= args.backlink_min_score
                    and suggestion.section_found
                    and risk_gate.blocked
                ):
                    backfill_lines.append("    - applied: blocked_by_risk_gate")

        else:
            backfill_lines.append("- 旧页回链建议: 无")
            backfill_lines.append("- 说明: 当前未找到足够强的旧页承接目标，建议人工判断是否需要桥接页")
        backfill_lines.append("")
        backlink_report.append("\n".join(backfill_lines))

        for page in existing_pages:
            conflict = detect_conflicts(cluster, page)
            if conflict:
                conflict_alerts.append((cluster.suggested_title, conflict))

        if args.mode in {"safe-apply", "full-apply"}:
            title = cluster.suggested_title
            page_path = wiki_dir / f"{slugify_title(title)}_{cluster.page_type}.md"
            if not page_path.exists():
                page = render_page(

                    title=title,
                    source=f"{raw_dir.name} 聚合整理",
                    tags=cluster.hints_hit or cluster.anchor_terms[:5] or [cluster.name],
                    related=[x.title for x in related_candidates],
                    raw_sources=[doc.rel_path for doc in cluster.docs],
                    cluster=cluster,
                )
                page_path.write_text(page, encoding="utf-8")
                created_pages.append(f"wiki/{page_path.name}")

    risk_flags = []
    effective_mode = args.mode
    if risk_gate.blocked:
        effective_mode = "suggest-only"
        risk_flags.append(f"高风险执行门禁已阻断自动旧页改写：{'；'.join(risk_gate.reasons)}")
    elif len(created_pages) > args.max_new_pages or applied_backlinks > 10 or planned_existing_updates > 6:
        effective_mode = "suggest-only"
        risk_flags.append("检测到高风险批处理规模，结果已按 suggest-only 思路输出审计提示")


    conflict_lines = ["## 冲突检测", ""]
    if conflict_alerts:
        for source_title, alert in conflict_alerts[:20]:
            conflict_lines.append(
                f"- 新页: [[{source_title}]] -> 旧页: [[{alert.target_title}]] | severity={alert.severity} | shared_terms={', '.join(alert.shared_terms) if alert.shared_terms else '无'}"
            )
            for reason in alert.reasons:
                conflict_lines.append(f"  - {reason}")
    else:
        conflict_lines.append("- 未检测到明显观点冲突")

    conflict_lines.append("")
    conflict_lines.append("## 风险门禁")
    conflict_lines.append(f"- blocked: {'yes' if risk_gate.blocked else 'no'}")
    conflict_lines.append(f"- level: {risk_gate.level}")
    if risk_gate.reasons:
        for reason in risk_gate.reasons:
            conflict_lines.append(f"- reason: {reason}")
    if risk_gate.suggestions:
        for suggestion in risk_gate.suggestions:
            conflict_lines.append(f"- suggestion: {suggestion}")

    maintenance_actions = apply_health_fixes(wiki_health, page_map, args.mode,
                                              args.fix_limit_related, args.fix_limit_body,
                                              index_path=index_path, log_path=log_path,
                                              wiki_dir=wiki_dir, raw_dir=raw_dir)
    if args.mode in {"safe-apply", "full-apply"}:
        maintenance_actions.extend(normalize_related_format(wiki_dir))
    wiki_health.maintenance_actions.extend(maintenance_actions)


    health_lines = build_health_lines(wiki_health)


    write_report(reports_dir / f"{timestamp}_topic_clusters.md", "Topic Clusters", "\n".join(cluster_report) or "无")
    write_report(reports_dir / f"{timestamp}_link_candidates.md", "Link Candidates", "\n".join(link_report) or "无")
    write_report(reports_dir / f"{timestamp}_backfill_suggestions.md", "Backfill Suggestions", "\n".join(backlink_report) or "无")
    write_report(reports_dir / f"{timestamp}_conflict_alerts.md", "Conflict Alerts", "\n".join(conflict_lines))
    write_report(reports_dir / f"{timestamp}_wiki_health.md", "Wiki Health", "\n".join(health_lines))

    summary = [
        f"- raw 批次: `{raw_dir.as_posix()}`",
        f"- 请求模式: `{args.mode}`",
        f"- 实际审计模式: `{effective_mode}`",
        f"- 文档数: {len(docs)}",
        f"- 主题簇数: {len(clusters)}",
        f"- 新建页面数: {len(created_pages)}",
        f"- 孤岛风险主题数: {len(orphan_clusters)}",
        f"- 旧页回链建议数: {total_backlink_suggestions}",
        f"- 自动写入回链数: {applied_backlinks}",
        f"- 自动补全 related 数: {related_updates}",
        f"- 冲突提醒数: {len(conflict_alerts)}",
        f"- 单向链接数: {len(wiki_health.single_direction_links)}",
        f"- 孤岛页数: {len(wiki_health.island_pages)}",
        f"- 批量修复动作数: {len(wiki_health.maintenance_actions)}",
    ]


    if orphan_clusters:
        summary.append(f"- 孤岛风险主题: {', '.join(orphan_clusters[:10])}")
    summary.append(f"- 风险门禁: blocked={'yes' if risk_gate.blocked else 'no'} level={risk_gate.level}")
    if risk_gate.reasons:
        summary.extend([f"- 门禁原因: {reason}" for reason in risk_gate.reasons])
    if risk_gate.suggestions:
        summary.extend([f"- 门禁建议: {suggestion}" for suggestion in risk_gate.suggestions])
    if risk_flags:
        summary.extend([f"- 风险降级: {flag}" for flag in risk_flags])

    summary.append("")
    summary.extend(preview_lines)
    summary.append("")
    summary.append("## 主题簇摘要")
    for cluster in clusters[: args.max_new_pages]:
        score_items = sorted(cluster.page_type_scores.items(), key=lambda x: -x[1])
        score_str = "、".join(f"{k}={v}" for k, v in score_items if v > 0)
        anchor_str = "、".join(cluster.anchor_terms[:5]) if cluster.anchor_terms else "无"
        summary.append(
            f"- {cluster.name}: docs={len(cluster.docs)}, title=`{cluster.suggested_title}`, type={cluster.page_type}, scores={{{score_str}}}, quality={cluster.quality_score}, anchors={anchor_str}"
        )
    summary.append("")
    summary.append("## 建链质量提示")
    summary.append("- related 候选按标题包含、共享标签、锚点词命中、页面类型优先级综合评分，并标记 strong/medium/weak。")
    summary.append("- full-apply 下会优先补旧页 frontmatter 的 related，再根据显式开关决定是否写正文回链。")
    summary.append("- 若出现孤岛风险主题，优先补 topic-hints 或手工指定桥接页，而不是盲目扩链。")
    summary.append("")
    summary.append("## 内容蒸馏提示")
    summary.append("- 每个专题页新增自动摘要、共识结论、分歧与待验证点、证据摘录。")
    summary.append("- 冲突检测报告只提醒，不自动覆盖旧页结论。")

    write_report(reports_dir / f"{timestamp}_build_report.md", "Build Report", "\n".join(summary))

    if args.mode in {"safe-apply", "full-apply"}:
        update_index(index_path, wiki_dir, raw_dir)
        append_log(log_path, raw_dir, created_pages, args.mode, clusters[: args.max_new_pages])

    print("[DONE] wiki-kb-builder enhanced")
    print(
        f"docs={len(docs)} clusters={len(clusters)} created={len(created_pages)} backlinks={total_backlink_suggestions} "
        f"applied_backlinks={applied_backlinks} related_updates={related_updates} conflicts={len(conflict_alerts)}"
    )





if __name__ == "__main__":
    main()
