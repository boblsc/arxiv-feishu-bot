# arxiv_to_feishu.py  (web-search + classification)
import os
import sys
import re
import time
import html
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

# ====== 环境变量（Secrets / env 注入，不要加引号）======
WEBHOOK_URL   = os.getenv("WEBHOOK_URL")  # 必填：飞书 Incoming Webhook
ARXIV_QUERY   = os.getenv("ARXIV_QUERY", "dark matter OR neutrino OR TPC OR xenon OR argon OR WIMP OR CEvNS")
# 逗号或空格分隔的子分类；会统一加上 classification: 前缀
ARXIV_CLASSES = os.getenv("ARXIV_CLASSES", "hep-th,hep-ex,hep-ph,nucl-ex,physics.ins-det")
# 是否同时要求属于 physics 大组（网页搜索支持）
REQUIRE_PHYSICS_GROUP = os.getenv("REQUIRE_PHYSICS_GROUP", "1") in ("1","true","True","YES","yes")
# 网页搜索参数
RESULT_SIZE   = int(os.getenv("RESULT_SIZE", "50"))  # 每页条数（网页最大 200）
ORDER         = os.getenv("ORDER", "-announced_date_first")   # 见网页 search 的 order 参数
HIDE_ABS      = os.getenv("HIDE_ABSTRACTS", "True") in ("1","true","True","YES","yes")
# 业务控制
TOP_SEND      = int(os.getenv("TOP_SEND", "10"))  # 实际推送的上限
DAYS          = int(os.getenv("DAYS", "0"))       # 0 表示不按天过滤；>0 则按“公告日期”过滤

SEARCH_BASE   = "https://arxiv.org/search/"

def _normalize_class_tokens(raw: str):
    """把 'hep-ex, hep-ph ...' 统一成 ['classification:hep-ex', ...]"""
    tokens = [t.strip() for t in re.split(r"[,\s]+", raw) if t.strip()]
    out = []
    seen = set()
    for t in tokens:
        if not t.startswith("classification:"):
            t = f"classification:{t}"
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out

def build_web_query(query: str, classes: str, require_physics_group: bool = True) -> str:
    # 关键词块（允许 OR / 括号），不加 field 前缀，直接走网页 `searchtype=all`
    kw_block = f"({query})"
    # 分类块
    class_terms = _normalize_class_tokens(classes)
    if require_physics_group:
        class_terms = ["classification:physics"] + class_terms
    if len(class_terms) == 1:
        cls_block = class_terms[0]
    else:
        cls_block = "(" + " OR ".join(class_terms) + ")"
    # 合并
    full = f"{kw_block} AND {cls_block}"
    return full

def build_search_url(q: str, size: int, order: str, hide_abs: bool) -> str:
    # 注意：query 要 URL 编码
    params = [
        ("query", q),
        ("searchtype", "all"),
        ("abstracts", "hide" if hide_abs else "show"),
        ("order", order),
        ("size", str(size)),
    ]
    qs = "&".join([f"{k}={quote_plus(v)}" for k,v in params])
    return f"{SEARCH_BASE}?{qs}"

def parse_results(html_text: str):
    """
    解析 https://arxiv.org/search/ 返回的结果列表。
    结构（以 2025 年样式为准）：
      <li class="arxiv-result">
        <p class="title is-5 mathjax">...</p>
        <p class="authors">...</p>
        <p class="is-size-7">Submitted ...; originally announced ...</p>
        <span class="tag is-small is-link tooltip is-tooltip-top" data-tooltip="...">hep-ex</span>
        ...
      </li>
    """
    soup = BeautifulSoup(html_text, "html.parser")
    items = []
    for li in soup.select("li.arxiv-result"):
        # 标题
        title_tag = li.select_one("p.title")
        title = title_tag.get_text(strip=True) if title_tag else ""

        # 作者
        auth_tag = li.select_one("p.authors")
        authors = re.sub(r"\s+", " ", auth_tag.get_text(strip=True).replace("Authors:", "").strip()) if auth_tag else ""

        # 链接（abs / pdf）
        abs_link = None
        pdf_link = None
        for a in li.select("a"):
            href = a.get("href","")
            if "/abs/" in href and not abs_link:
                abs_link = href
            if "/pdf/" in href and href.endswith(".pdf"):
                pdf_link = href
        if abs_link and not abs_link.startswith("http"):
            abs_link = "https://arxiv.org" + abs_link
        if pdf_link and not pdf_link.startswith("http"):
            pdf_link = "https://arxiv.org" + pdf_link

        # 主分类（显示在标签上）
        cat = ""
        tag = li.select_one("span.tag")
        if tag:
            cat = tag.get_text(strip=True)

        # 公告/提交日期（用于 DAYS 过滤）
        date_str = ""
        for p in li.select("p.is-size-7"):
            txt = p.get_text(" ", strip=True)
            if "announced" in txt or "Submitted" in txt:
                date_str = txt
                break

        # 从文本里尽量提取 "announced" 或 "Submitted on" 的日期
        announced_date = None
        # 尝试多种样式（en）
        m = re.search(r"(announced|Submitted)\s+on\s+([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})", date_str)
        if m:
            try:
                announced_date = datetime.strptime(m.group(2), "%B %d, %Y").replace(tzinfo=timezone.utc)
            except Exception:
                pass

        items.append({
            "title": title,
            "authors": authors,
            "abs": abs_link,
            "pdf": pdf_link,
            "cat": cat,
            "announced": announced_date,
        })
    return items

def filter_by_days(items, days: int):
    if days <= 0:
        return items
    since = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for it in items:
        dt = it.get("announced")
        if dt is None or dt >= since:
            out.append(it)
    return out

def build_card(items):
    if not items:
        content = "没有符合条件的结果。"
    else:
        lines = []
        for i, it in enumerate(items, 1):
            title = it["title"]
            authors = it["authors"]
            cat = it.get("cat","")
            absu = it.get("abs") or ""
            pdfu = it.get("pdf") or (absu.replace("/abs/","/pdf/") + ".pdf" if "/abs/" in absu else "")
            # announced 日期（可选展示）
            date_str = ""
            if it.get("announced"):
                date_str = it["announced"].strftime("%Y-%m-%d")
            head = f"**{i}. {title}**"
            meta = f"作者：{authors}"
            if date_str or cat:
                meta += "  |  "
                if date_str:
                    meta += f"日期：{date_str}"
                if date_str and cat:
                    meta += "  |  "
                if cat:
                    meta += f"类别：`{cat}`"
            lines += [
                head,
                meta,
                f"[abs]({absu})  |  [pdf]({pdfu})",
                ""
            ]
        content = "\n\n".join(lines)

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "arXiv（classification 过滤）"},
                "template": "blue"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": content}}
            ],
        }
    }

def post_to_feishu(payload):
    r = requests.post(WEBHOOK_URL, json=payload, timeout=15)
    r.raise_for_status()
    return r.json()

def main():
    if not WEBHOOK_URL:
        print("缺少 WEBHOOK_URL（飞书 Webhook）。", file=sys.stderr)
        sys.exit(2)

    # 构造 classification 查询
    full_query = build_web_query(ARXIV_QUERY, ARXIV_CLASSES, REQUIRE_PHYSICS_GROUP)
    url = build_search_url(full_query, RESULT_SIZE, ORDER, HIDE_ABS)

    # 抓取网页并解析
    headers = {"User-Agent": "Mozilla/5.0 (arxiv-feishu-bot; classification-search)"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    items = parse_results(resp.text)
    items = filter_by_days(items, DAYS)
    to_send = items[:TOP_SEND]

    payload = build_card(to_send)
    r = post_to_feishu(payload)
    print("Feishu response:", r)
    print(f"URL used: {url}")
    print(f"Found {len(items)} items after filter; sent {len(to_send)}.")

if __name__ == "__main__":
    main()
