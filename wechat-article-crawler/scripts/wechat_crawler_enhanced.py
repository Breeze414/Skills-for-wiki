# coding: utf-8
"""
微信公众号文章获取工具 —— 增强版 v2

在原版 wechat-article-claw 基础上增加：
  - 随机延迟（防检测）
  - 自动分批 + 批次间休息
  - 断点续传
  - 正文提取（使用 wechatarticles.Url2Html，无需 appmsg_token）
  - 按日期分文件夹导出 Markdown
  - 支持 --cookie / --token 独立传参（适合对话式交互）
  - 完善的统计与日志

用法:
  1. 先安装原版依赖: pip install -r requirements.txt && playwright install chromium
  2. python wechat_crawler_enhanced.py --nickname "目标公众号" --since 2026-01-01 --cookie "..." --token "..."
  3. 或先运行 python wechat_login.py 扫码获取凭证，再运行本脚本

作者: Open Source Contributor
日期: 2026-04-25
更新: v2 修复正文提取（Url2Html替代read_wechat_article），增加cookie/token独立参数
"""

import json
import time
import os
import sys
import argparse
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

try:
    from wechatarticles import PublicAccountsWeb
except ImportError:
    print("[✗] 未找到 wechatarticles 模块，请先安装原版依赖:")
    print("      pip install -r requirements.txt")
    sys.exit(1)

# 正文提取：使用 wechatarticles 内置的 Url2Html（无需 appmsg_token）
try:
    from wechatarticles import Url2Html
    URL2HTML_AVAILABLE = True
except ImportError:
    URL2HTML_AVAILABLE = False
    print("[!] 警告: 未找到 wechatarticles.Url2Html，正文提取功能将不可用")

# HTML 解析
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    print("[!] 警告: 未找到 beautifulsoup4，正文提取功能将不可用")

# 扫码登录（可选）
try:
    from wechat_login import playwright_login
    PLAYWRIGHT_LOGIN_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_LOGIN_AVAILABLE = False
    print("[!] 警告: 未找到 wechat_login 模块，自动扫码登录功能将不可用")


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def load_config(config_path="config.json"):
    """加载配置文件（兼容原版 + 增强字段）"""
    default_config = {
        "targets": [],
        "crawl_settings": {
            "batch_size": 5,
            "delay_seconds": 5,
            "delay_jitter": 0.4,
            "batch_limit": 100,
            "rest_seconds": 60,
            "output_dir": "output",
            "content_delay_seconds": 3,
            "content_delay_jitter": 0.5,
            "max_retries": 3,
            "retry_backoff": 2.0,
            "skip_content": True,
            "export_markdown": True,
            "markdown_dir": "output/markdown",
        }
    }
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
        default_config.update(user_config)
        if "crawl_settings" in user_config:
            default_config["crawl_settings"].update(user_config["crawl_settings"])
    return default_config


def save_json(data, path, indent=2):
    """安全保存 JSON"""
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)


def load_json(path, default=None):
    """安全加载 JSON"""
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def is_verify_page(text: str) -> bool:
    """检测内容是否为微信验证页/拦截页而非真实正文

    返回 True 表示是验证页（应判定为提取失败）
    """
    if not text:
        return True
    verify_keywords = ["环境异常", "完成验证后即可继续访问", "去验证"]
    if any(kw in text for kw in verify_keywords):
        return True
    # 真实微信正文通常远长于验证页（验证页一般 < 100 字）
    if len(text.strip()) < 100:
        return True
    return False


def random_delay(base_seconds, jitter_ratio=0.4):
    """计算随机延迟时间"""
    min_delay = max(1.0, base_seconds * (1 - jitter_ratio))
    max_delay = base_seconds * (1 + jitter_ratio)
    return random.uniform(min_delay, max_delay)


def format_duration(seconds):
    """将秒数格式化为易读字符串"""
    if seconds < 60:
        return f"{seconds:.1f}秒"
    elif seconds < 3600:
        return f"{seconds/60:.1f}分钟"
    else:
        return f"{seconds/3600:.1f}小时"


def save_credentials(cookie, token, path="credentials.json"):
    """保存凭证到本地文件"""
    data = {
        "cookie": cookie,
        "token": token,
        "updated_at": datetime.now().isoformat(),
    }
    save_json(data, path)
    print(f"[✓] 凭证已保存到 {path}")


def load_credentials(path="credentials.json"):
    """从本地文件加载凭证"""
    data = load_json(path)
    if data:
        print(f"[i] 使用已保存的凭证 (更新于 {data.get('updated_at', '未知')})")
        return data["cookie"], data["token"]
    return None, None


# ═══════════════════════════════════════════════════════════════
# 正文提取（Url2Html 方案，无需 appmsg_token）
# ═══════════════════════════════════════════════════════════════

def html_to_text(html_content):
    """
    将微信文章 HTML 转为纯文本
    优先使用 BeautifulSoup，兜底用正则
    """
    if BS4_AVAILABLE and html_content:
        try:
            soup = BeautifulSoup(html_content, "html.parser")

            # 移除 script/style 标签
            for tag in soup.find_all(["script", "style", "noscript"]):
                tag.decompose()

            # 尝试定位微信文章正文区域
            content_area = (
                soup.find("div", id="js_content") or
                soup.find("div", class_="rich_media_content") or
                soup.find("div", class_="rich_media_area_primary") or
                soup.find("article") or
                soup.body or
                soup
            )

            if content_area:
                # 处理换行：p/div/br 标签转为换行符
                for tag in content_area.find_all(["p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li"]):
                    tag.append("\n")

                text = content_area.get_text(separator="", strip=False)
                # 清理多余空行
                lines = [line.strip() for line in text.splitlines()]
                text = "\n".join(line for line in lines if line)
                return text
        except Exception as e:
            print(f"    [!] BeautifulSoup 解析失败: {e}，使用正则兜底")

    # 正则兜底
    if html_content:
        text = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.S)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.S)
        text = re.sub(r'<br\s*/?>', '\n', text)
        text = re.sub(r'</?p[^>]*>', '\n', text)
        text = re.sub(r'</?div[^>]*>', '\n', text)
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'&nbsp;', ' ', text)
        text = re.sub(r'&amp;', '&', text)
        text = re.sub(r'&lt;', '<', text)
        text = re.sub(r'&gt;', '>', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    return ""


def html_to_markdown(html_content):
    """
    将微信文章 HTML 转为简易 Markdown
    """
    if not html_content:
        return ""

    if BS4_AVAILABLE:
        try:
            soup = BeautifulSoup(html_content, "html.parser")

            for tag in soup.find_all(["script", "style", "noscript"]):
                tag.decompose()

            content_area = (
                soup.find("div", id="js_content") or
                soup.find("div", class_="rich_media_content") or
                soup.find("div", class_="rich_media_area_primary") or
                soup.find("article") or
                soup.body or
                soup
            )

            if not content_area:
                return html_to_text(html_content)

            # 处理各标签转为 Markdown
            for h in content_area.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
                level = int(h.name[1])
                h.replace_with(f"\n{'#' * level} {h.get_text(strip=True)}\n")

            for strong in content_area.find_all("strong"):
                strong.replace_with(f"**{strong.get_text(strip=True)}**")

            for em in content_area.find_all("em"):
                em.replace_with(f"*{em.get_text(strip=True)}*")

            for a in content_area.find_all("a"):
                href = a.get("href", "")
                text = a.get_text(strip=True)
                if href and text:
                    a.replace_with(f"[{text}]({href})")
                else:
                    a.replace_with(text)

            for img in content_area.find_all("img"):
                src = img.get("data-src") or img.get("src", "")
                alt = img.get("alt", "")
                if src:
                    img.replace_with(f"\n![{alt}]({src})\n")

            for br in content_area.find_all("br"):
                br.replace_with("\n")

            for p in content_area.find_all("p"):
                p.append("\n")

            for li in content_area.find_all("li"):
                li.replace_with(f"- {li.get_text(strip=True)}\n")

            text = content_area.get_text(separator="", strip=False)
            lines = [line.strip() for line in text.splitlines()]
            text = "\n".join(line for line in lines if line)
            # 清理多余空行
            text = re.sub(r'\n{3,}', '\n\n', text)
            return text.strip()
        except Exception as e:
            print(f"    [!] Markdown 转换失败: {e}，降级为纯文本")

    return html_to_text(html_content)


def fetch_article_content(url, timeout=20, max_retries=3):
    """
    使用 Url2Html 提取单篇文章正文

    Parameters
    ----------
    url : str
        微信文章链接
    timeout : int
        超时秒数
    max_retries : int
        最大重试次数

    Returns
    -------
    dict: {"html": ..., "text": ..., "markdown": ...} 或 {"error": ...}
    """
    if not URL2HTML_AVAILABLE:
        return {"error": "Url2Html 模块不可用"}

    # 确保使用 https
    if url.startswith("http://"):
        url = url.replace("http://", "https://", 1)

    for attempt in range(max_retries):
        try:
            fetcher = Url2Html()
            # mode=1: 返回 html 源码，不下载图片
            html = fetcher.run(url, mode=1)

            if not html or len(html) < 100:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return {"error": "返回内容为空或过短"}

            text = html_to_text(html)
            md = html_to_markdown(html)

            return {
                "html": html,
                "text": text,
                "markdown": md,
                "text_length": len(text),
            }
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"    [!] 重试 {attempt + 1}/{max_retries}: {e}")
                time.sleep(wait)
            else:
                return {"error": str(e)}

    return {"error": "重试次数耗尽"}


def fetch_all_content_enhanced(articles, settings):
    """
    批量提取文章正文（增强版，使用 Url2Html）

    Parameters
    ----------
    articles : list
        文章列表（每项需包含 link 字段）
    settings : dict
        配置参数

    Returns
    -------
    list: 含正文的文章列表
    """
    content_delay = settings.get("content_delay_seconds", 3)
    content_jitter = settings.get("content_delay_jitter", 0.5)
    max_retries = settings.get("max_retries", 3)

    total = len(articles)
    results = []
    success_count = 0
    fail_count = 0

    print(f"\n📖 开始抓取 {total} 篇文章正文 (Url2Html 方案)...\n")

    for i, article in enumerate(articles):
        url = article.get("link") or article.get("url") or article.get("content_url", "")
        title = article.get("title", "无标题")

        if not url:
            print(f"  [{i+1}/{total}] ⚠️ 跳过（无 URL）: {title[:40]}")
            article["content"] = ""
            article["content_text"] = ""
            article["content_markdown"] = ""
            results.append(article)
            fail_count += 1
            continue

        print(f"  [{i+1}/{total}] 抓取: {title[:40]}...")

        fetched = fetch_article_content(url, max_retries=max_retries)

        if "error" in fetched:
            print(f"           ❌ 失败: {fetched['error']}")
            article["content"] = ""
            article["content_text"] = ""
            article["content_markdown"] = ""
            article["fetch_error"] = fetched["error"]
            fail_count += 1
        else:
            text = fetched.get("text", "")
            text_len = fetched.get("text_length", 0)
            # 使用验证页检测确保状态准确
            if is_verify_page(text):
                print(f"           ❌ 被拦截/验证页 ({text_len} 字)")
                article["fetch_error"] = "wechat_verify_page"
                fail_count += 1
            elif text_len > 0:
                print(f"           ✅ 成功 ({text_len} 字)")
                success_count += 1
            else:
                print(f"           ⚠️ 页面已获取但正文为空")
                fail_count += 1

            article["content"] = text
            article["content_text"] = text
            article["content_markdown"] = fetched.get("markdown", "")
            article["content_html"] = fetched.get("html", "")
            article["content_length"] = text_len

        results.append(article)

        # 随机延迟
        if i < total - 1:
            delay = random_delay(content_delay, content_jitter)
            time.sleep(delay)

    print(f"\n{'='*50}")
    print(f"✅ 正文提取完成: 成功 {success_count} / 失败 {fail_count} / 总计 {total}")

    return results


# ═══════════════════════════════════════════════════════════════
# 断点续传管理
# ═══════════════════════════════════════════════════════════════

class ProgressTracker:
    """断点续传进度管理器"""

    def __init__(self, nickname, progress_file=None):
        self.nickname = nickname
        safe_name = nickname.replace("/", "_").replace(" ", "_")
        self.progress_file = progress_file or f"progress_{safe_name}.json"
        self.data = load_json(self.progress_file, default={})

    def is_resumable(self, since_date, max_articles):
        if not self.data:
            return False
        saved_since = self.data.get("since_date")
        saved_max = self.data.get("max_articles")
        current_since = since_date.strftime("%Y-%m-%d") if since_date else None
        if saved_since == current_since and saved_max == max_articles:
            return True
        return False

    def get_progress(self):
        return {
            "fetched_count": self.data.get("fetched_count", 0),
            "article_urls": self.data.get("article_urls", []),
            "failed_urls": self.data.get("failed_urls", []),
        }

    def save(self, fetched_count, article_urls, failed_urls, since_date, max_articles):
        self.data = {
            "nickname": self.nickname,
            "since_date": since_date.strftime("%Y-%m-%d") if since_date else None,
            "max_articles": max_articles,
            "fetched_count": fetched_count,
            "article_urls": article_urls,
            "failed_urls": failed_urls,
            "updated_at": datetime.now().isoformat(),
        }
        save_json(self.data, self.progress_file)

    def clear(self):
        if os.path.exists(self.progress_file):
            os.remove(self.progress_file)


# ═══════════════════════════════════════════════════════════════
# 核心抓取逻辑（增强版）
# ═══════════════════════════════════════════════════════════════

def crawl_enhanced(
    cookie, token, nickname, settings,
    fakeid=None, max_articles=None, since_date=None,
    resume=False, progress_tracker=None
):
    """增强版公众号文章抓取"""
    batch_size = settings.get("batch_size", 5)
    base_delay = settings.get("delay_seconds", 5)
    delay_jitter = settings.get("delay_jitter", 0.4)
    batch_limit = settings.get("batch_limit", 100)
    rest_seconds = settings.get("rest_seconds", 60)
    output_dir = settings.get("output_dir", "output")
    max_retries = settings.get("max_retries", 3)
    retry_backoff = settings.get("retry_backoff", 2.0)

    os.makedirs(output_dir, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"🚀 增强版抓取启动")
    print(f"   目标公众号: {nickname}")
    print(f"   配置: 延迟={base_delay}s±{delay_jitter*100:.0f}%, 分批={batch_limit}篇/批, 休息={rest_seconds}s")
    print(f"   正文提取: {'Url2Html ✅' if URL2HTML_AVAILABLE else '❌ 不可用'}")
    print(f"{'='*60}")

    paw = PublicAccountsWeb(cookie=cookie, token=token)

    # ── 1. 确定 fakeid ──
    if fakeid:
        print(f" 使用提供的 FakeID: {fakeid}")
    else:
        try:
            info = paw.official_info(nickname)
            if info:
                found = info[0]
                fakeid = found['fakeid']
                print(f" 公众号: {found['nickname']}")
                print(f" FakeID: {fakeid}")
            else:
                print(f"[✗] 未找到公众号: {nickname}")
                return []
        except Exception as e:
            print(f"[✗] 查询公众号失败: {e}")
            return []

    # ── 2. 获取文章总数 ──
    try:
        data = paw._PublicAccountsWeb__get_articles_data("", begin="0", biz=fakeid)
        articles_sum = data.get("app_msg_cnt", 0)
    except Exception as e:
        print(f"[✗] 获取文章总数失败: {e}")
        return []

    if max_articles and max_articles < articles_sum:
        crawl_total = max_articles
    else:
        crawl_total = articles_sum

    if since_date:
        print(f" 📅 时间过滤: 仅抓取 {since_date.strftime('%Y-%m-%d')} 之后的文章")
    print(f" 📄 文章总数: {articles_sum}，本次抓取上限: {crawl_total}")

    if articles_sum == 0:
        print("[!] 未找到文章")
        return []

    # ── 3. 断点续传检查 ──
    all_articles = []
    consecutive_failures = 0
    since_ts = since_date.timestamp() if since_date else None
    start_offset = 0

    if resume and progress_tracker and progress_tracker.is_resumable(since_date, max_articles):
        prog = progress_tracker.get_progress()
        print(f" 📦 检测到断点续传进度: 已抓取 {prog['fetched_count']} 篇")
        start_offset = prog["fetched_count"]
        print(f"    从第 {start_offset} 篇继续...")

    # ── 4. 计算分批计划 ──
    remaining = crawl_total - start_offset
    num_batches = (remaining + batch_limit - 1) // batch_limit
    print(f" 📊 执行计划: 共 {remaining} 篇, 分 {num_batches} 批, 每批最多 {batch_limit} 篇")

    start_time = time.time()

    for batch_idx in range(num_batches):
        batch_start = start_offset + batch_idx * batch_limit
        batch_end = min(batch_start + batch_limit, crawl_total)
        batch_size_actual = batch_end - batch_start

        print(f"\n{'─'*60}")
        print(f" 📦 第 {batch_idx + 1}/{num_batches} 批 ({batch_start} ~ {batch_end - 1})")
        print(f"{'─'*60}")

        batch_articles = []
        batch_failed = 0
        should_stop = False

        for begin in range(batch_start, batch_end, batch_size):
            retries = 0
            success = False

            while retries < max_retries and not success:
                try:
                    data = paw._PublicAccountsWeb__get_articles_data(
                        "",
                        begin=str(begin),
                        biz=fakeid,
                        count=batch_size
                    )
                    article_data = data.get("app_msg_list", [])

                    # 按日期过滤
                    if since_ts:
                        for article in article_data:
                            article_time = article.get("update_time") or article.get("create_time", 0)
                            if article_time >= since_ts:
                                batch_articles.append(article)
                            else:
                                article_date = datetime.fromtimestamp(article_time).strftime('%Y-%m-%d')
                                print(f"  📅 遇到 {article_date} 的文章，到达时间边界，提前终止")
                                should_stop = True
                                break
                    else:
                        batch_articles.extend(article_data)

                    success = True
                    consecutive_failures = 0

                except Exception as e:
                    retries += 1
                    consecutive_failures += 1
                    wait = retry_backoff ** retries
                    print(f"  [!] 第 {begin} 批失败 (重试 {retries}/{max_retries}): {e}")
                    print(f"      {wait:.1f}s 后重试...")
                    time.sleep(wait)

                    if consecutive_failures >= 3:
                        print("[✗] 连续失败 3 次，停止抓取")
                        print("[!] 可能是 cookie/token 已过期，请重新提取")
                        should_stop = True
                        break

            if not success:
                batch_failed += batch_size
                continue

            # 进度报告
            elapsed = time.time() - start_time
            fetched_total = len(all_articles) + len(batch_articles)
            if fetched_total > 0:
                avg_time = elapsed / fetched_total
                eta = avg_time * (crawl_total - fetched_total)
                print(f"  进度: {fetched_total}/{crawl_total} | 已用 {format_duration(elapsed)} | 预计剩余 {format_duration(eta)}")

            # 随机延迟
            delay = random_delay(base_delay, delay_jitter)
            time.sleep(delay)

            if should_stop:
                break

        all_articles.extend(batch_articles)
        print(f"  ✅ 本批完成: {len(batch_articles)} 篇, 累计: {len(all_articles)} 篇")

        # 保存进度
        if progress_tracker:
            urls = [a.get("link", "") for a in all_articles]
            progress_tracker.save(len(all_articles), urls, [], since_date, max_articles)

        if should_stop:
            break

        # ── 批次间休息（非最后一批）──
        if batch_idx < num_batches - 1:
            rest = random_delay(rest_seconds, 0.3)
            print(f"  💤 批次间休息 {rest:.1f}s，模拟人工操作...")
            time.sleep(rest)

    # ── 5. 保存文章列表 ──
    safe_name = nickname.replace("/", "_").replace(" ", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = os.path.join(output_dir, f"{safe_name}_{timestamp}.json")

    result = {
        "account": nickname,
        "total": len(all_articles),
        "crawled_at": datetime.now().isoformat(),
        "settings": {k: v for k, v in settings.items() if k != "credentials"},
        "articles": all_articles,
    }
    save_json(result, output_file)

    elapsed_total = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"✅ 第一阶段（文章列表）抓取完成!")
    print(f"   公众号: {nickname}")
    print(f"   文章数: {len(all_articles)}")
    print(f"   耗时: {format_duration(elapsed_total)}")
    print(f"   平均速度: {len(all_articles)/elapsed_total*60:.1f} 篇/分钟")
    print(f"   基础列表保存: {output_file}")
    print(f"{'='*60}")

    # ── 6. 第二阶段：正文内容 ──
    skip_content = settings.get("skip_content", False)
    if not skip_content and all_articles and URL2HTML_AVAILABLE:
        print(f"\n{'='*60}")
        print(f"📖 第二阶段：提取文章正文 (Url2Html)")
        content_delay = settings.get("content_delay_seconds", 3)
        content_jitter = settings.get("content_delay_jitter", 0.5)
        print(f"   正文延迟: {content_delay}s±{content_jitter*100:.0f}%")
        print(f"{'='*60}")

        results = fetch_all_content_enhanced(all_articles, settings)

        full_output_file = os.path.join(output_dir, f"{safe_name}_full_{timestamp}.json")
        output_data = {
            "account": nickname,
            "total": len(results),
            "success": sum(1 for r in results if r.get("content")),
            "crawled_at": datetime.now().isoformat(),
            "articles": results,
        }
        save_json(output_data, full_output_file)

        success_rate = output_data["success"] / len(results) * 100 if results else 0
        print(f"\n✅ 第二阶段完成!")
        print(f"   成功提取正文: {output_data['success']}/{output_data['total']} ({success_rate:.1f}%)")
        print(f"   完整数据保存: {full_output_file}")

        # ── 7. 导出 Markdown ──
        if settings.get("export_markdown", True):
            export_markdown(results, nickname, settings)

        # 清除进度文件
        if progress_tracker:
            progress_tracker.clear()

        return results

    elif not skip_content and not URL2HTML_AVAILABLE:
        print("\n[!] 正文提取模块不可用，仅保存文章列表")
        print("    原因: wechatarticles.Url2Html 未安装")
        if progress_tracker:
            progress_tracker.clear()
        return all_articles

    if progress_tracker:
        progress_tracker.clear()
    return all_articles


# ═══════════════════════════════════════════════════════════════
# Markdown 导出
# ═══════════════════════════════════════════════════════════════

def sanitize_filename(name, max_len=80):
    """清理文件名中的非法字符"""
    invalid_chars = '\\/:*?"<>|'
    for ch in invalid_chars:
        name = name.replace(ch, '_')
    name = name.strip().replace('\n', ' ').replace('\r', '')
    return name[:max_len]


def export_markdown(articles, nickname, settings):
    """按日期分文件夹导出 Markdown"""
    md_dir = settings.get("markdown_dir", "output/markdown")
    safe_account = sanitize_filename(nickname)
    md_root = os.path.join(md_dir, safe_account)
    os.makedirs(md_root, exist_ok=True)

    print(f"\n📝 导出 Markdown 到: {md_root}")

    count = 0
    for article in articles:
        title = article.get("title", "无标题")
        # 优先使用 markdown 格式，降级使用纯文本
        content = article.get("content_markdown") or article.get("content_text") or article.get("content", "")
        url = article.get("link", "")
        create_time = article.get("create_time", 0)

        if not content:
            continue

        dt = datetime.fromtimestamp(create_time) if create_time else datetime.now()
        date_folder = dt.strftime("%Y-%m")
        folder = os.path.join(md_root, date_folder)
        os.makedirs(folder, exist_ok=True)

        safe_title = sanitize_filename(title)
        filename = f"{dt.strftime('%Y-%m-%d')}_{safe_title}.md"
        filepath = os.path.join(folder, filename)

        # 处理重名
        counter = 1
        orig_filepath = filepath
        while os.path.exists(filepath):
            name, ext = os.path.splitext(orig_filepath)
            filepath = f"{name}_{counter}{ext}"
            counter += 1

        md_content = f"""---
title: "{title}"
date: {dt.strftime('%Y-%m-%d %H:%M:%S')}
source: {url}
account: {nickname}
---

{content}
"""
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(md_content)
        count += 1

    print(f"   成功导出 {count} 篇 Markdown 文章")


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="微信公众号文章爬虫 —— 增强版 v2（随机延迟 + 自动分批 + 断点续传 + Url2Html正文）"
    )
    parser.add_argument("--credentials", type=str,
                        help='凭证 JSON: \'{"cookie":"...","token":"..."}\'')
    parser.add_argument("--cookie", type=str, default=None,
                        help="微信公众平台 Cookie（独立传参，优先级低于 --credentials）")
    parser.add_argument("--token", type=str, default=None,
                        help="微信公众平台 Token（独立传参，优先级低于 --credentials）")
    parser.add_argument("--login", action="store_true",
                        help="自动启动浏览器扫码登录获取凭证")
    parser.add_argument("--config", type=str, default="config.json",
                        help="配置文件路径 (默认: config.json)")
    parser.add_argument("--nickname", type=str, default=None,
                        help="目标公众号名称")
    parser.add_argument("--fakeid", type=str, default=None,
                        help="公众号 fakeid（提供后跳过搜索）")
    parser.add_argument("--max", type=int, default=None, dest="max_articles",
                        help="最多抓取的文章数量")
    parser.add_argument("--since", type=str, default=None,
                        help="只抓取此日期之后的文章，格式: YYYY-MM-DD")
    parser.add_argument("--headless", action="store_true",
                        help="无头模式启动浏览器")
    parser.add_argument("--resume", action="store_true",
                        help="断点续传模式（从上次进度继续）")
    parser.add_argument("--batch-limit", type=int, default=None,
                        help="每批最大数量（覆盖配置文件）")
    parser.add_argument("--rest-seconds", type=int, default=None,
                        help="批次间休息秒数（覆盖配置文件）")
    parser.add_argument("--delay", type=float, default=None,
                        help="基础延迟秒数（覆盖配置文件）")
    parser.add_argument("--skip-content", action="store_true",
                        help="跳过正文提取，只抓列表（默认行为）")
    parser.add_argument("--use-url2html", action="store_true",
                        help="[不推荐] 启用 Url2Html 正文提取（易触发验证页，仅供轻量测试）")
    parser.add_argument("--no-markdown", action="store_true",
                        help="不导出 Markdown")

    args = parser.parse_args()

    # 加载配置
    config = load_config(args.config)
    settings = config.get("crawl_settings", {})

    # 命令行参数覆盖配置
    if args.batch_limit is not None:
        settings["batch_limit"] = args.batch_limit
    if args.rest_seconds is not None:
        settings["rest_seconds"] = args.rest_seconds
    if args.delay is not None:
        settings["delay_seconds"] = args.delay
    if args.use_url2html:
        settings["skip_content"] = False
        print("[!] 已启用 Url2Html 正文提取（不推荐，可能触发验证页）")
    elif not args.skip_content:
        # 默认跳过正文（由工作流负责 Playwright 提取）
        settings["skip_content"] = True
    if args.no_markdown:
        settings["export_markdown"] = False

    # 确定目标
    targets = config.get("targets", [])
    if args.nickname or args.fakeid:
        targets = [{"nickname": args.nickname or "未知", "fakeid": args.fakeid}]

    if not targets:
        print("[✗] 未指定目标公众号")
        print("  用法: python wechat_crawler_enhanced.py --nickname 公众号名称 --cookie '...' --token '...'")
        print("  或:   python wechat_crawler_enhanced.py --nickname 公众号名称 --login")
        sys.exit(1)

    # 获取凭证（优先级：--credentials > --cookie/--token > 已保存凭证 > --login 扫码）
    cookie, token = None, None

    if args.credentials:
        data = json.loads(args.credentials)
        cookie, token = data["cookie"], data["token"]
        save_credentials(cookie, token)
    elif args.cookie and args.token:
        cookie, token = args.cookie, args.token
        save_credentials(cookie, token)
    else:
        cookie, token = load_credentials()

    if not cookie or not token:
        if args.login and PLAYWRIGHT_LOGIN_AVAILABLE:
            print("\n🚀 启动浏览器扫码登录...")
            cookie, token = playwright_login(headless=args.headless)
            if not cookie or not token:
                print("[✗] 扫码登录失败")
                sys.exit(1)
            save_credentials(cookie, token)
        else:
            print("[✗] 未提供登录凭证，请使用以下任一方式：")
            print("  1. --cookie 'xxx' --token 'xxx'  （独立传参）")
            print("  2. --credentials '{\"cookie\":\"...\",\"token\":\"...\"}'  （JSON传参）")
            print("  3. --login  （自动启动浏览器扫码）")
            sys.exit(1)

    # 解析日期
    since_date = None
    if args.since:
        try:
            since_date = datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            print(f"[✗] 日期格式错误: {args.since}，请使用 YYYY-MM-DD")
            sys.exit(1)

    # 执行抓取
    for target in targets:
        nickname = target["nickname"]
        progress = ProgressTracker(nickname)

        crawl_enhanced(
            cookie=cookie,
            token=token,
            nickname=nickname,
            settings=settings,
            fakeid=target.get("fakeid"),
            max_articles=args.max_articles,
            since_date=since_date,
            resume=args.resume,
            progress_tracker=progress,
        )


if __name__ == "__main__":
    main()
