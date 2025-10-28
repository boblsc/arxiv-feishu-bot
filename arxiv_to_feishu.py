# arxiv_to_feishu.py  (web-search + classification + today-only + abstracts)
import os
import sys
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

# ===== 环境变量（不要加引号）=====
WEBHOOK_URL   = os.getenv("WEBHOOK_URL")                        # 必填：飞书 Incoming Webhook
ARXIV_QUERY   = os.getenv("ARXIV_QUERY", "dark matter OR neutrino OR TPC OR xenon OR argon OR WIMP OR CEvNS")
ARXIV_CLASSES = os.getenv("ARXIV_CLASSES", "hep-th,hep-ex,hep-ph,nucl-ex,physics.ins-det")
REQUIRE_PHYSICS_GROUP = os.getenv("REQUIRE_PHYSICS_GROUP", "1") in ("1","true","True","YES","yes")

# 网页参数
RESULT_SIZE   = int(os.getenv("RESULT_SIZE", "50"))
ORDER         = os.getenv("ORDER", "-announced_date_first")
# 要“包含摘要”：把 HIDE_ABSTRACTS 设为 False（或删掉这个 env）
HIDE_ABSTRACTS = os.getenv("HIDE_ABSTRACTS", "False") in ("1","true","True","YES","yes")

# 业务控制
TOP_SEND    = int(os.getenv("TOP_SEND", "10"))
DAYS        = int(os.getenv("DAYS", "0"))            # 0=不限；>0=最近N天（仅当 TODAY_ONLY=0 时使用）
TODAY_ONLY  = os.getenv("TODAY_ONLY", "1") in ("1","true","True","YES","yes")
LOCAL_TZ    = os.getenv("LOCAL_TZ", "America/Los_Angeles")

SEARCH_BASE = "https://arxiv.org/search/"

def _normalize_class_tokens(raw: str):
    toks = [t.strip() for t in re.split(r"[,\s]+", raw) if t.strip()]
    out, seen = [], set()
    for t in toks:
        if not t.startswith("classification:"):
            t = f"classification:{t}"
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out

def build_web_query(query: str, classes: str, require_physics_group: bool = True) -> str:
    kw_block = f"({query})"
    class_terms = _normalize_class_tokens(classes)
    if require_physics_group:
        class_terms = ["classification:physics"] + class_terms
    cls_block = class_terms[0] if len(class_terms) == 1 else "(" + " OR ".join(class_terms) + ")"
    return f"{kw_block} AND {cls_block}"

def build_search_url(q: str, size: int, order: str, hide_abs: bool) -> str:
    params = [
        ("query", q),
        ("searchtype", "all"),
        ("abstracts", "hide" if hide_abs else "show"),
        ("order", order),
        ("size", str(size)),
    ]
    qs = "&".join([f"{k}={quote_plus(v)}" for k, v in params])
    return f"{SEARCH_BASE}?{qs}"

def _extract_abstract(li: BeautifulSoup) -> str:
    # 把网页显示的“摘要”抓出来（abstracts=show 时才有）
    node = li.select_one("span.abstract-full")
    if not node:
        node = li.select_one("p.abstract") or li.select_one("span.abstract-short")
    if not node:
        return ""
    txt = node.get_text(" ", strip=True)
    # 去除可能出现的前缀 “Abstract:” 或折叠控件残留字符
    txt = re.sub(r"^\s*Abstract:\s*", "", txt, flags=re.I)
    txt = re.sub(r"\s*(Show less|△ Less|▽ More)\s*$", "", txt, flags=re.I)
    return txt.strip()

def parse_results(html_text: str):
    soup = BeautifulSoup(html_text, "html.parser")
    items = []
    for li in soup.select("li.arxiv-result"):
        title_tag = li.select_one("p.title")
        title = title_tag.get_text(strip=True) if title_tag else ""

        auth_tag = li.select_one("p.authors")
        authors = re.sub(r"\s+", " ", auth_tag.get_text(strip=True).replace("Authors:", "").strip()) if auth_tag else ""

        abs_link, pdf_link = None, None
        for a in li.select("a"):
            href = a.get("href", "")
            if "/abs/" in href and not abs_link:
                abs_link = href
            if "/pdf/" in href and href.endswith(".pdf"):
                pdf_link = href
        if abs_link and not abs_link.startswith("http"):
            abs_link = "https://arxiv.org" + abs_link
        if pdf_link and not pdf_link.startswith("http"):
            pdf_link = "https://arxiv.org" + pdf_link

        # 主分类 tag
        cat = ""
        tag = li.select_one("span.tag")
        if tag:
            cat = tag.get_text(strip=True)

        # announced 日期（只有日期，无时间）
        date_text = ""
        for p in li.select("p.is-size-7"):
            t = p.get_text(" ", strip=True)
            if "announced" in t or "Submitted" in t:
                date_text = t
                break
        dt_date = None
        # 解析 "announced on Month DD, YYYY" 或 "Submitted on Month DD, YYYY"
        m = re.search(r"(announced|Submitted)\s+on\s+([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})", date_text)
        if m:
            try:
                dt_date = datetime.strptime(m.group(2), "%B %d, %Y").date()
            except Exception:
                dt_date = None

        abstract = _extract_abstract(li)

        items.append({
            "title": title,
            "authors": authors,
            "abs": abs_link,
            "pdf": pdf_link,
            "cat": cat,
            "announced_date": dt_date,   # 仅 date()
            "abstract": abstract,
        })
    return items

def filter_today(items, tz_name: str):
    # “当天”按用户本地时区的“日历日”来判定
    today_local = datetime.now(ZoneInfo(tz_name)).date()
    return [it for it in items if it.get("announced_date") == today_local]

def filter_by_days(items, days: int, tz_name: str):
    if days <= 0:
        return items
    # 用本地时区的“今天减 days-1 天”为最早日
    now_local = datetime.now(ZoneInfo(tz_name)).date()
    earliest = now_local  # days==1 → 只今天；days==2 → 今天/昨天
    if days > 1:
        from datetime import timedelta
        earliest = now_local - timedelta(days=days-1)
    out = []
    for it in items:
        d = it.get("announced_date")
        if d is None or d >= earliest:
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
            cat = it.get("cat", "")
            absu = it.get("abs") or ""
            pdfu = it.get("pdf") or (absu.replace("/abs/", "/pdf/") + ".pdf" if "/abs/" in absu else "")
            date_str = it["announced_date"].isoformat() if it.get("announced_date") else ""
            abstract = (it.get("abstract") or "").strip()
            if len(abstract) > 600:
                abstract = abstract[:600] + " …"
            head = f"**{i}. {title}**"
            meta = f"作者：{authors}"
            extras = []
            if date_str:
                extras.append(f"日期：{date_str}")
            if cat:
                extras.append(f"类别：`{cat}`")
            if extras:
                meta += "  |  " + "  |  ".join(extras)
            lines += [
                head,
                meta,
                abstract if abstract else "_(no abstract on list page)_",
                f"[abs]({absu})  |  [pdf]({pdfu})",
                ""
            ]
        content = "\n\n".join(lines)

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "arXiv 当日更新（含摘要）"},
                "template": "blue"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": content}}
            ],
        }
    }

def post_to_feishu(payload):
    r = requests.post(WEBHOOK_URL, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()

def main():
    if not WEBHOOK_URL:
        print("缺少 WEBHOOK_URL（飞书 Webhook）。", file=sys.stderr)
        sys.exit(2)

    query = build_web_query(ARXIV_QUERY, ARXIV_CLASSES, REQUIRE_PHYSICS_GROUP)
    url = build_search_url(query, RESULT_SIZE, ORDER, HIDE_ABSTRACTS)

    headers = {"User-Agent": "Mozilla/5.0 (arxiv-feishu-bot; classification-search)"}
    resp = requests.get(url, headers=headers, timeout=40)
    resp.raise_for_status()

    items = parse_results(resp.text)

    if TODAY_ONLY:
        items = filter_today(items, LOCAL_TZ)
    elif DAYS > 0:
        items = filter_by_days(items, DAYS, LOCAL_TZ)

    to_send = items[:TOP_SEND]
    payload = build_card(to_send)
    r = post_to_feishu(payload)

    print("Feishu response:", r)
    print(f"URL used: {url}")
    print(f"Found {len(items)} items after filter; sent {len(to_send)}.")

if __name__ == "__main__":
    main()
