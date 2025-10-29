# arxiv_to_feishu.py
# 模式：网页搜索（classification）+ 以 https://arxiv.org/localtime 推断“最新公告日” + 含摘要
# 依赖：pip install requests beautifulsoup4

import os
import re
from datetime import datetime, timedelta
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

# ===== 环境变量（值不要加引号）=====
WEBHOOK_URL   = os.getenv("WEBHOOK_URL")  # 必填：飞书 Incoming Webhook

# 关键词（放 Secrets 更安全）
ARXIV_QUERY   = os.getenv(
    "ARXIV_QUERY",
    "dark matter OR neutrino OR TPC OR xenon OR argon OR WIMP OR CEvNS"
)
# 子分类列表（逗号或空格分隔），脚本自动补全为 classification:xxx
ARXIV_CLASSES = os.getenv(
    "ARXIV_CLASSES",
    "hep-th,hep-ex,hep-ph,nucl-ex,physics.ins-det"
)
# 是否同时要求 physics 大组
REQUIRE_PHYSICS_GROUP = os.getenv("REQUIRE_PHYSICS_GROUP", "1").lower() in ("1","true","yes")

# 网页搜索参数
RESULT_SIZE = int(os.getenv("RESULT_SIZE", "200"))            # 建议 200，保证“同一天”能装下一页
ORDER       = os.getenv("ORDER", "-announced_date_first")     # 按 announced 倒序
HIDE_ABSTRACTS = os.getenv("HIDE_ABSTRACTS", "False").lower() in ("1","true","yes")  # 需要摘要 → False/0

# 推送条数上限
TOP_SEND = int(os.getenv("TOP_SEND", "10"))

SEARCH_BASE = "https://arxiv.org/search/"
ARXIV_LOCALTIME = "https://arxiv.org/localtime"

# ---------- 查询构造 ----------
def _normalize_class_tokens(raw: str):
    tokens = [t.strip() for t in re.split(r"[,\s]+", raw) if t.strip()]
    out, seen = [], set()
    for t in tokens:
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

# ---------- 从 /localtime 推断“最新公告日”（按 ET） ----------
_ET_LINE_RE = re.compile(r">\s*([A-Za-z]{3}),\s*(\d{1,2})\s*([A-Za-z]{3})\s*(\d{4})\s*(\d{2}):(\d{2})\s*(EDT|EST)\s*<")

_MONTH_MAP = {
    "Jan":1, "Feb":2, "Mar":3, "Apr":4, "May":5, "Jun":6,
    "Jul":7, "Aug":8, "Sep":9, "Oct":10, "Nov":11, "Dec":12
}

def _get_et_now_from_localtime() -> datetime:
    """
    从 https://arxiv.org/localtime 抓取 'Tue, 28 Oct 2025 22:40 EDT' 这行，解析成 ET 的 datetime（无 tz，仅用于日期/小时判断）。
    """
    headers = {"User-Agent": "Mozilla/5.0 (arxiv-feishu-bot; localtime-check)"}
    r = requests.get(ARXIV_LOCALTIME, headers=headers, timeout=20)
    r.raise_for_status()
    html = r.text
    m = _ET_LINE_RE.search(html)
    if not m:
        # 兜底：若解析失败，就用当前 UTC 日期（近似），但强烈建议确保正则匹配
        raise RuntimeError("Failed to parse arxiv localtime page.")
    # weekday = m.group(1)  # 'Tue'，这里不需要
    day = int(m.group(2))
    mon = _MONTH_MAP[m.group(3)]
    year = int(m.group(4))
    hour = int(m.group(5))
    minute = int(m.group(6))
    # EDT/EST 对我们只影响“是否 >=20 点”的判断；我们仅用本地日期/小时，不做时区换算。
    return datetime(year, mon, day, hour, minute)

def _most_recent_announcement_date(et_now: datetime):
    """
    arXiv 公告通常在工作日 20:00 ET：
    - 若 et_now.hour >= 20 → 当天就是最新公告日；
    - 若 et_now.hour < 20 → 最新公告日为“上一工作日”（遇到周末回退至周五）。
    """
    # 如果是周末（Sat=5, Sun=6），即使 >=20 也没有新公告，回退到周五
    if et_now.weekday() >= 5:
        # 回退到上一个周五
        delta = (et_now.weekday() - 4)
        latest = (et_now.date() - timedelta(days=delta))
        return latest

    if et_now.hour >= 20:
        return et_now.date()
    # 否则取上一个工作日
    prev = et_now.date() - timedelta(days=1)
    while prev.weekday() >= 5:  # 跨周末处理
        prev -= timedelta(days=1)
    return prev

# ---------- 解析搜索结果 ----------
_DATE_RE = re.compile(r"(announced|Submitted)\s+on\s+([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})")

def _extract_announced_date(li: BeautifulSoup):
    for p in li.select("p.is-size-7"):
        t = p.get_text(" ", strip=True)
        if "announced" in t or "Submitted" in t:
            m = _DATE_RE.search(t)
            if m:
                try:
                    return datetime.strptime(m.group(2), "%B %d, %Y").date()
                except Exception:
                    return None
    return None

def _extract_abstract(li: BeautifulSoup) -> str:
    node = (li.select_one("span.abstract-full")
            or li.select_one("p.abstract")
            or li.select_one("span.abstract-short"))
    if not node:
        return ""
    txt = node.get_text(" ", strip=True)
    txt = re.sub(r"^\s*Abstract:\s*", "", txt, flags=re.I)
    txt = re.sub(r"\s*(Show less|△ Less|▽ More)\s*$", "", txt, flags=re.I)
    return txt.strip()

def parse_all_items(html_text: str):
    soup = BeautifulSoup(html_text, "html.parser")
    items = []
    for li in soup.select("li.arxiv-result"):
        d = _extract_announced_date(li)

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
        if not pdf_link and abs_link and "/abs/" in abs_link:
            pdf_link = abs_link.replace("/abs/", "/pdf/") + ".pdf"

        cat = li.select_one("span.tag").get_text(strip=True) if li.select_one("span.tag") else ""
        abstract = _extract_abstract(li)

        items.append({
            "title": title,
            "authors": authors,
            "abs": abs_link or "",
            "pdf": pdf_link or "",
            "cat": cat,
            "announced_date": d,   # date() 或 None
            "abstract": abstract,
        })
    return items

# ---------- 仅保留“最新公告日”的条目 ----------
def filter_by_target_date(items, target_date):
    if target_date is None:
        return items
    return [it for it in items if it.get("announced_date") == target_date]

# ---------- 生成飞书卡片 ----------
def build_card(items):
    if not items:
        content = "没有符合条件的结果（最新公告日为空或该日无匹配）。"
    else:
        lines = []
        for i, it in enumerate(items, 1):
            title = it["title"]
            authors = it["authors"]
            cat = it.get("cat", "")
            absu = it.get("abs", "")
            pdfu = it.get("pdf", "")
            date_str = it["announced_date"].isoformat() if it.get("announced_date") else ""
            abstract = (it.get("abstract") or "").strip()
            if len(abstract) > 700:
                abstract = abstract[:700] + " …"

            head = f"**{i}. {title}**"
            meta = f"作者：{authors}"
            extras = []
            if date_str:
                extras.append(f"日期：{date_str}")
            if cat:
                extras.append(f"类别：`{cat}`")
            if extras:
                meta += "  |  " + "  |  ".join(extras)

            links = ""
            if absu:
                links += f"[abs]({absu})"
            if pdfu:
                links += ("  |  " if links else "") + f"[pdf]({pdfu})"

            lines += [head, meta, abstract if abstract else "_(no abstract on list page)_", links, ""]
        content = "\n\n".join(lines)

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "arXiv 最近公告日（含摘要）"},
                "template": "blue"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": content}}
            ],
        }
    }

# ---------- 主流程 ----------
def main():
    if not WEBHOOK_URL:
        print("缺少 WEBHOOK_URL（飞书 Incoming Webhook）。", flush=True)
        raise SystemExit(2)

    # A) 读取 /localtime → 推断“最新公告日”（ET）
    et_now = _get_et_now_from_localtime()
    target_date = _most_recent_announcement_date(et_now)

    # B) 构造 classification 查询 + 抓取搜索页（含摘要）
    full_query = build_web_query(ARXIV_QUERY, ARXIV_CLASSES, REQUIRE_PHYSICS_GROUP)
    url = build_search_url(full_query, RESULT_SIZE, ORDER, HIDE_ABSTRACTS)
    headers = {"User-Agent": "Mozilla/5.0 (arxiv-feishu-bot; classification-search)"}
    resp = requests.get(url, headers=headers, timeout=40)
    resp.raise_for_status()

    # C) 解析整页 → 仅保留“最新公告日”
    all_items = parse_all_items(resp.text)
    items = filter_by_target_date(all_items, target_date)

    # D) 推送
    to_send = items[:TOP_SEND]
    payload = build_card(to_send)
    r = requests.post(WEBHOOK_URL, json=payload, timeout=30)
    r.raise_for_status()

    print("Feishu response:", r.text)
    print(f"Localtime ET now: {et_now}  -> latest announcement date: {target_date}")
    print(f"URL used: {url}")
    print(f"Parsed {len(all_items)} items; kept {len(items)} on {target_date}; sent {len(to_send)}.")

if __name__ == "__main__":
    main()
