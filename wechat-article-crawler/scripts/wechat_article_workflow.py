#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信公众号端到端工作流：
1. 自动登录 / 读取凭证
2. 抓取公众号文章列表
3. 用 Playwright 提取正文并导出 Markdown
4. 清洗 Markdown 并同步到 wiki-kb/raw

说明：
- 这是用户级 skill 的 orchestrator，尽量少依赖项目外上下文。
- 知识页提炼与双向链接当前输出为待办建议文件，不在脚本内直接生成最终专题页，避免过度猜测主题。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = Path.cwd()
DEFAULT_CLAW_PROJECT = Path.home() / "workspace"
DEFAULT_WIKI_KB = Path.home() / "wiki-kb"


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""


def run_cmd(args: list[str], cwd: Path | None = None) -> None:
    print("[CMD]", " ".join(str(x) for x in args))
    proc = subprocess.run(args, cwd=str(cwd or WORKSPACE_ROOT))
    if proc.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(str(x) for x in args)} (exit={proc.returncode})")


def detect_python() -> str:
    for candidate in ("python", "python3"):
        try:
            proc = subprocess.run([candidate, "--version"], capture_output=True, text=True)
            if proc.returncode == 0:
                version = (proc.stdout or proc.stderr).strip()
                print(f"[i] 检测到 Python: {candidate} ({version})")
                return candidate
        except FileNotFoundError:
            continue
    raise RuntimeError("未找到可用 Python 命令（python / python3）")


def latest_file(directory: Path, pattern: str) -> Path | None:
    files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def sanitize_account_name(name: str) -> str:
    return name.replace("/", "_").replace("\\", "_").strip()


class CheckpointManager:
    """
    Checkpoint 管理器 —— 100% 可靠设计：
    1. 文件位置固定，不存在则自动创建
    2. 读取/写入封装为原子操作，失败时抛出异常而非静默跳过
    3. 所有调用点都必须在 main() 的执行路径上，不可绕过
    """

    def __init__(self, workspace_root: Path) -> None:
        self.path = workspace_root / ".workbuddy" / "artifacts" / "wechat-crawl-checkpoint.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict] = {}
        self._loaded = False
        self._load()

    def _load(self) -> None:
        """加载 checkpoint 文件；不存在则初始化为空"""
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
                self._loaded = True
                print(f"[✓] Checkpoint 已加载: {self.path}")
                return
            except Exception as e:
                print(f"[⚠] Checkpoint 文件损坏，将重新初始化: {e}")
        self._data = {}
        self._loaded = True
        print(f"[i] Checkpoint 初始化（首次运行或文件缺失）: {self.path}")

    def _save(self) -> None:
        """原子写入 checkpoint 文件"""
        tmp_path = self.path.with_suffix(".tmp")
        try:
            tmp_path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(self.path)
            print(f"[✓] Checkpoint 已保存: {self.path}")
        except Exception as e:
            print(f"[✗] Checkpoint 保存失败: {e}")
            raise

    def get_last_success(self, account: str) -> datetime | None:
        """返回上次成功运行的 datetime（带时区）"""
        entry = self._data.get(account)
        if not entry:
            return None
        ts_str = entry.get("last_success_timestamp")
        if not ts_str:
            return None
        try:
            return datetime.fromisoformat(ts_str)
        except Exception:
            return None

    def get_since_date(self, account: str, fallback_days: int = 14) -> str:
        """
        根据 checkpoint 计算 --since 日期。
        有 checkpoint → 从上次成功往前推1天（留重叠窗口）
        无 checkpoint → fallback 到 N 天前
        """
        last_success = self.get_last_success(account)
        if last_success:
            # 留1天重叠窗口（防止边界文章丢失）
            since_dt = last_success - __import__("datetime").timedelta(days=1)
            since_str = since_dt.strftime("%Y-%m-%d")
            print(f"[i] Checkpoint 驱动: 上次成功={last_success.isoformat()}, since={since_str} (重叠1天)")
            return since_str
        # 无 checkpoint → fallback
        since_dt = datetime.now() - __import__("datetime").timedelta(days=fallback_days)
        since_str = since_dt.strftime("%Y-%m-%d")
        print(f"[i] Checkpoint 缺失: fallback since={since_str} ({fallback_days}天前)")
        return since_str

    def record_success(
        self,
        account: str,
        batch_dir: Path,
        total_articles: int,
        new_articles: int,
    ) -> None:
        """记录一次成功运行"""
        now = datetime.now()
        entry = self._data.setdefault(account, {})
        entry["last_success_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
        entry["last_success_timestamp"] = now.isoformat()
        entry["last_batch_dir"] = str(batch_dir)
        entry["total_articles_ever"] = entry.get("total_articles_ever", 0) + new_articles
        entry["success_count"] = entry.get("success_count", 0) + 1
        entry["failure_count"] = entry.get("failure_count", 0)
        self._save()

    def record_failure(self, account: str, reason: str) -> None:
        """记录一次失败运行"""
        entry = self._data.setdefault(account, {})
        entry["failure_count"] = entry.get("failure_count", 0) + 1
        entry["last_failure_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry["last_failure_reason"] = reason
        self._save()


def filter_articles(articles: list[dict], keywords: list[str]) -> list[dict]:
    if not keywords:
        return articles
    lower_keywords = [k.lower() for k in keywords if k.strip()]
    filtered = []
    for article in articles:
        hay = " ".join([
            str(article.get("title", "")),
            str(article.get("digest", "")),
            str(article.get("content", "")),
            str(article.get("content_text", "")),
            str(article.get("content_markdown", "")),
        ]).lower()
        if any(k in hay for k in lower_keywords):
            filtered.append(article)
    return filtered


def extract_source_from_frontmatter(path: Path) -> str | None:
    """从 Markdown frontmatter 中提取 source 链接"""
    try:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return None
        parts = text.split("---", 2)
        if len(parts) < 3:
            return None
        fm = parts[1]
        for line in fm.splitlines():
            if line.strip().startswith("source:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return None


def build_historical_link_set(raw_base_dir: Path, safe_account: str) -> set[str]:
    """扫描历史 raw 文章，提取所有文章的 source 链接集合"""
    links: set[str] = set()
    account_dir = raw_base_dir / "wechat" / safe_account
    if not account_dir.exists():
        return links
    for md_file in account_dir.rglob("*.md"):
        src = extract_source_from_frontmatter(md_file)
        if src:
            links.add(src)
    return links


def filter_new_articles(articles: list[dict], historical_links: set[str]) -> tuple[list[dict], list[dict]]:
    """返回 (新文章列表, 跳过的历史文章列表)"""
    new_articles = []
    skipped_articles = []
    for article in articles:
        link = article.get("link") or article.get("url") or article.get("source") or ""
        if link in historical_links:
            skipped_articles.append(article)
        else:
            new_articles.append(article)
    return new_articles, skipped_articles


def save_filtered_json(data: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_article_name(name: str) -> str:
    """归一化文章文件名用于去重比较（去掉日期格式差异）"""
    return name.lower().replace("-", "").replace("_", "").replace(" ", "").strip()


def extract_month_from_filename(name: str) -> str | None:
    """从文件名提取 YYYY-MM，用于按月份归档"""
    import re
    m = re.match(r"(\d{4})-(\d{2})-\d{2}", name)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.match(r"(\d{4})(\d{2})\d{2}", name)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


def compute_md5(path: Path) -> str:
    import hashlib
    h = hashlib.md5()
    h.update(path.read_bytes())
    return h.hexdigest()


def find_historical_raw_articles(raw_base_dir: Path, safe_account: str) -> dict[str, Path]:
    """扫描历史 raw 文章，建立归一化文件名 -> 最新文件路径 的映射"""
    historical: dict[str, Path] = {}
    account_dir = raw_base_dir / "wechat" / safe_account
    if not account_dir.exists():
        return historical
    for md_file in account_dir.rglob("*.md"):
        norm = normalize_article_name(md_file.name)
        if norm not in historical or md_file.stat().st_mtime > historical[norm].stat().st_mtime:
            historical[norm] = md_file
    return historical


def deduplicate_and_sync(markdown_root: Path, raw_base_dir: Path, safe_account: str) -> tuple[int, int, list[str], list[str]]:
    """
    增量去重并同步到 raw/wechat/账号名/YYYY-MM/。
    返回: (同步数, 跳过数, 跳过文件列表, 新增文件列表)
    """
    historical = find_historical_raw_articles(raw_base_dir, safe_account)
    synced = 0
    skipped = 0
    skipped_files: list[str] = []
    new_files: list[str] = []

    for src in markdown_root.rglob("*.md"):
        rel = src.relative_to(markdown_root)
        norm = normalize_article_name(src.name)

        if norm in historical:
            # 文件已存在，校验 MD5 是否完全一致
            hist_path = historical[norm]
            try:
                if compute_md5(src) == compute_md5(hist_path):
                    skipped += 1
                    skipped_files.append(str(rel))
                    continue
            except Exception:
                pass  # MD5 计算失败，继续当作新文件处理

        # 新文章或内容有变化，同步到 raw/wechat/账号名/YYYY-MM/
        # 只接受 YYYY-MM-DD_ 标准命名格式，跳过 YYYYMMDD_ 紧凑格式和 _N.md 碰撞副本
        if not re.match(r"^\d{4}-\d{2}-\d{2}_.*\.md$", src.name):
            print(f"[!] 跳过非规范文件名: {src.name} (不符合 YYYY-MM-DD_ 格式)")
            skipped += 1
            skipped_files.append(f"{rel} (non-canonical filename)")
            continue

        month = extract_month_from_filename(src.name) or datetime.now().strftime("%Y-%m")
        raw_dir = raw_base_dir / "wechat" / safe_account / month
        dst = raw_dir / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        synced += 1
        new_files.append(str(rel))
        # 更新 historical，防止同批次内不同命名格式的副本漏过
        historical[norm] = dst

    return synced, skipped, skipped_files, new_files


def sync_markdown_to_raw(markdown_root: Path, raw_dir: Path) -> int:
    """（保留向后兼容的简单同步）"""
    count = 0
    for src in markdown_root.rglob("*.md"):
        rel = src.relative_to(markdown_root)
        dst = raw_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        count += 1
    return count


def clean_markdown_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text
    text = text.replace("#< CLIXML", "")
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    text = text.strip() + "\n"
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def clean_markdown_tree(markdown_root: Path) -> tuple[int, int]:
    total = 0
    changed = 0
    for fp in markdown_root.rglob("*.md"):
        total += 1
        if clean_markdown_file(fp):
            changed += 1
    return total, changed


def verify_markdown_quality(markdown_root: Path) -> tuple[int, int, list[str]]:
    """扫描 Markdown 文件，检测验证页/空内容，返回 (成功数, 失败数, 失败文件列表)"""
    verify_keywords = ["环境异常", "完成验证后即可继续访问", "去验证"]
    success = 0
    failed = 0
    failed_files = []
    for fp in markdown_root.rglob("*.md"):
        text = fp.read_text(encoding="utf-8")
        # 去掉 frontmatter 后检查正文
        body = text
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                body = parts[2]
        body = body.strip()
        if not body or any(kw in body for kw in verify_keywords) or len(body) < 100:
            failed += 1
            failed_files.append(str(fp.relative_to(markdown_root)))
        else:
            success += 1
    return success, failed, failed_files


def write_ingest_report(
    report_path: Path,
    account: str,
    markdown_root: Path,
    raw_dir: Path,
    total: int,
    changed: int,
    synced: int,
    skipped: int,
    skipped_files: list[str],
    new_files: list[str],
    keywords: list[str],
    md_success: int = 0,
    md_failed: int = 0,
    md_failed_files: list[str] | None = None,
) -> None:
    report = f"""# 微信公众号抓取入库报告

- 公众号：{account}
- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- Markdown 根目录：`{markdown_root}`
- Raw 入库目录：`{raw_dir}`
- 关键词过滤：{', '.join(keywords) if keywords else '无'}
- Markdown 文件数：{total}
- **正文提取成功：{md_success}**
- **正文提取失败：{md_failed}**
- 清洗变更数：{changed}
- 同步入库数：{synced}
- **历史重复跳过：{skipped}**

## 增量去重详情

"""
    if skipped > 0:
        report += f"以下 **{skipped}** 篇文章与历史 raw 批次完全重复（文件名 + MD5 一致），已自动跳过：\n\n"
        for f in skipped_files[:50]:  # 最多列 50 条
            report += f"- 🔄 `{f}`\n"
        if len(skipped_files) > 50:
            report += f"- ... 等共 {len(skipped_files)} 篇\n"
        report += "\n"
    else:
        report += "✅ 无历史重复文章，全部为新内容。\n\n"

    if new_files:
        report += f"**本次新增文章（{len(new_files)} 篇）：**\n\n"
        for f in new_files[:50]:
            report += f"- 🆕 `{f}`\n"
        if len(new_files) > 50:
            report += f"- ... 等共 {len(new_files)} 篇\n"
        report += "\n"

    report += "## 正文质量校验详情\n\n"
    if md_failed_files:
        report += "以下文件疑似验证页或内容异常，需人工复核或重新提取：\n\n"
        for f in md_failed_files:
            report += f"- ❌ `{f}`\n"
        report += "\n"
    else:
        report += "✅ 所有 Markdown 文件正文内容校验通过。\n\n"

    report += """## 后续动作建议

1. 基于本批文章主题生成 wiki 专题页
2. 为专题页补 `related` 与回链
3. 更新 `index.md` 与 `log.md`
"""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")


def run_workflow(parsed_args: argparse.Namespace, checkpoint: CheckpointManager) -> None:
    """核心工作流逻辑（参数已由外部解析和 checkpoint 初始化）"""
    args = parsed_args

    python_cmd = detect_python()
    workspace_root = Path(args.workspace_root).resolve()
    wiki_kb_root = Path(args.wiki_kb_root).resolve()
    project_wechat_dir = workspace_root / "wechat-article-claw"
    if not project_wechat_dir.exists():
        raise RuntimeError(f"未找到项目目录: {project_wechat_dir}")

    safe_account = sanitize_account_name(args.nickname)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_base_dir = wiki_kb_root / "raw"
    reports_dir = workspace_root / ".workbuddy" / "artifacts" / "wechat-workflow"
    report_path = reports_dir / f"{timestamp}_{safe_account}_report.md"
    filtered_json_path = project_wechat_dir / "output" / f"{safe_account}_filtered_{timestamp}.json"

    # ====== CHECKPOINT: 计算 since 日期（100% 执行） ======
    since_date = args.since
    if args.no_checkpoint:
        print("[i] --no-checkpoint 已指定，跳过 checkpoint 计算")
    elif not since_date:
        since_date = checkpoint.get_since_date(safe_account, fallback_days=14)
    else:
        print(f"[i] 用户显式指定 --since={since_date}，覆盖 checkpoint")

    credentials_arg = []
    if args.credentials:
        credentials_arg = ["--credentials", args.credentials]

    crawl_cmd = [
        python_cmd,
        str(SCRIPT_DIR / "wechat_crawler_enhanced.py"),
        "--nickname", args.nickname,
        "--since", since_date or "1970-01-01",
        "--max", str(args.max),
        "--skip-content",
        "--config", str(SKILL_ROOT / "references" / "config_enhanced.json"),
    ]
    if args.resume:
        crawl_cmd.append("--resume")
    # 注意：--visible 不传给 crawl_cmd（阶段一不需要浏览器）
    crawl_cmd.extend(credentials_arg)

    run_cmd(crawl_cmd, cwd=project_wechat_dir)

    latest_full = latest_file(project_wechat_dir / "output", f"{safe_account}_full_*.json")
    if not latest_full:
        latest_full = latest_file(project_wechat_dir / "output", f"{safe_account}_*.json")
    if not latest_full:
        raise RuntimeError("抓取完成后未找到输出 JSON")

    data = json.loads(latest_full.read_text(encoding="utf-8"))
    articles = data.get("articles", data if isinstance(data, list) else [])

    keywords = [x.strip() for x in args.keywords.split(",") if x.strip()]
    if keywords:
        articles = filter_articles(articles, keywords)

    if args.until:
        until_dt = datetime.strptime(args.until, "%Y-%m-%d")
        until_ts = until_dt.timestamp() + 86399
        tmp = []
        for article in articles:
            ts = article.get("update_time") or article.get("create_time") or 0
            if ts and ts <= until_ts:
                tmp.append(article)
        articles = tmp

    # 增量过滤：只保留新增文章
    historical_links = build_historical_link_set(raw_base_dir, safe_account)
    new_articles, skipped_articles = filter_new_articles(articles, historical_links)

    print(f"\n[i] 增量过滤结果:")
    print(f"    抓取总数: {len(articles)}")
    print(f"    历史已存在: {len(skipped_articles)}")
    print(f"    真正新增: {len(new_articles)}")

    if not new_articles:
        print("\n✅ 无新增文章，跳过正文提取阶段")
        # 仍然写入空报告
        write_ingest_report(
            report_path, args.nickname, project_wechat_dir / "output" / "markdown" / safe_account,
            raw_base_dir / "wechat" / safe_account, 0, 0, 0, len(skipped_articles), [], [], keywords, 0, 0, [],
        )
        # ====== CHECKPOINT: 记录成功（即使无新增也算成功） ======
        checkpoint.record_success(
            safe_account, raw_base_dir / "wechat" / safe_account, total_articles=0, new_articles=0
        )
        print(f"\n===== WORKFLOW DONE =====")
        print(f"无新增文章")
        print(f"Report: {report_path}")
        return

    filtered = {
        "account": args.nickname,
        "total": len(new_articles),
        "original_total": len(articles),
        "skipped_historical": len(skipped_articles),
        "filtered_at": datetime.now().isoformat(),
        "keywords": keywords,
        "articles": new_articles,
    }
    save_filtered_json(filtered, filtered_json_path)

    extract_cmd = [
        python_cmd,
        str(project_wechat_dir / "playwright_extract.py"),
        "--json", str(filtered_json_path),
        "--credentials", str(project_wechat_dir / args.credentials_file),
        "--delay", str(args.delay),
        "--output-dir", str(project_wechat_dir / "output"),
    ]
    if args.visible:
        extract_cmd.append("--visible")
    run_cmd(extract_cmd, cwd=project_wechat_dir)

    markdown_root = project_wechat_dir / "output" / "markdown" / safe_account
    if not markdown_root.exists():
        raise RuntimeError(f"未找到 Markdown 输出目录: {markdown_root}")

    total, changed = clean_markdown_tree(markdown_root)
    # 正文质量校验：检测验证页
    md_success, md_failed, md_failed_files = verify_markdown_quality(markdown_root)
    # 增量去重同步到 raw/wechat/账号名/YYYY-MM/
    synced, skipped, skipped_files, new_files = deduplicate_and_sync(
        markdown_root, raw_base_dir, safe_account
    )
    write_ingest_report(
        report_path, args.nickname, markdown_root, raw_base_dir / "wechat" / safe_account, total, changed,
        synced, skipped, skipped_files, new_files, keywords,
        md_success, md_failed, md_failed_files,
    )

    # ====== CHECKPOINT: 记录成功（100% 执行，位于成功路径末尾） ======
    checkpoint.record_success(
        safe_account, raw_base_dir / "wechat" / safe_account,
        total_articles=len(new_articles),
        new_articles=synced,
    )

    print("\n===== WORKFLOW DONE =====")
    print(f"Markdown 文件总数: {total}")
    print(f"正文提取成功: {md_success}")
    print(f"正文提取失败: {md_failed}")
    if md_failed > 0:
        print(f"⚠️  有 {md_failed} 篇文章内容异常，详见报告")
    print(f"新增入库: {synced}")
    print(f"历史重复跳过: {skipped}")
    print(f"Markdown: {markdown_root}")
    print(f"Raw: {raw_base_dir / 'wechat' / safe_account}")
    print(f"Report: {report_path}")


def _parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="微信公众号文章端到端抓取与入库工作流")
    parser.add_argument("--nickname", required=True, help="公众号名称")
    parser.add_argument("--since", help="起始日期 YYYY-MM-DD（如不指定，自动从 checkpoint 计算）")
    parser.add_argument("--until", help="结束日期 YYYY-MM-DD，仅用于后处理过滤")
    parser.add_argument("--keywords", default="", help="关键词，逗号分隔")
    parser.add_argument("--max", type=int, default=200, help="最大文章数")
    parser.add_argument("--credentials", default="", help="凭证 JSON 字符串")
    parser.add_argument("--credentials-file", default="credentials.json", help="凭证文件名")
    parser.add_argument("--workspace-root", default=str(DEFAULT_CLAW_PROJECT), help="工作区根目录")
    parser.add_argument("--wiki-kb-root", default=str(DEFAULT_WIKI_KB), help="wiki-kb 根目录")
    parser.add_argument("--delay", type=float, default=5.5, help="正文提取基础延迟")
    parser.add_argument("--visible", action="store_true", help="显示浏览器窗口")
    parser.add_argument("--resume", action="store_true", help="断点续传")
    parser.add_argument("--no-checkpoint", action="store_true", help="禁用 checkpoint（用于首次全量抓取或强制重跑）")
    return parser.parse_args()


def main() -> None:
    """唯一入口：解析参数 → 初始化 checkpoint → 执行工作流 → 异常时记录失败"""
    args = _parse_args()
    workspace_root = Path(args.workspace_root).resolve()
    safe_account = sanitize_account_name(args.nickname)

    # ====== CHECKPOINT: 初始化（100% 执行，不可绕过） ======
    checkpoint = CheckpointManager(workspace_root)

    try:
        run_workflow(args, checkpoint)
    except Exception as e:
        print(f"\n[✗] WORKFLOW FAILED: {e}")
        # ====== CHECKPOINT: 记录失败（尽最大努力，不抛异常） ======
        try:
            checkpoint.record_failure(safe_account, str(e))
        except Exception as ce:
            print(f"[✗] Checkpoint 失败记录也出错了: {ce}")
        raise


if __name__ == "__main__":
    main()
