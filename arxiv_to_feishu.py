# arxiv_to_feishu.py
# Mode: web search (classification) + latest-day-only + abstracts + early-stop
# Requires: pip install requests beautifulsoup4

import os
import re
from datetime import datetime
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

# ===== Environment (values should be raw text, no quotes) =====
WEBHOOK_URL   = os.getenv("WEBHOOK_URL")  # REQUIRED: Feishu Incoming Webhook

# Query terms (classification syntax on arxiv.org/search)
ARXIV_QUERY   = os.getenv(
    "ARXIV_QUERY",
    "dark matter OR neutrino OR TPC OR xenon OR argon OR WIMP OR CEvNS",
)
# Comma/space-separated classes (auto-prefixed with classification:)
ARXIV_CLASSES = os.getenv(
    "ARXIV_CLASSES",
    "hep-th,hep-ex,hep-ph,nucl-ex,physics.ins-det",
)
# Also require physics group?
REQUIRE_PHYSICS_GROUP = os.getenv("REQUIRE_PHYSICS_GROUP", "1").lower() in ("1","true","yes")

# Search page params
RESULT_SIZE = int(os.getenv("RESULT_SIZE", "200"))          # one page; 200 fits a full batch
ORDER       = os.getenv("ORDER", "-announced_date_first")   # newest announced first
# We need abstracts → set to False (show)
HIDE_ABSTRACTS = os.getenv("HIDE_ABSTRACTS", "False").lower() in ("1","true","yes")

# Message shaping
TOP_SEND = int(os.getenv("TOP_SEND", "10"))  # limit messages sent to Feishu

SEARCH_BASE = "https://arxiv.org/search/"


# ---------- Query construction ----------
def _normalize_class_tokens(raw: str):
    """Normalize 'hep-ex, hep-ph' → ['classification:hep-ex', 'classification:hep-ph', ...]"""
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


# ---------- Parsing helpers ----------
def _extract_announced_date(li: BeautifulSoup):
    """
    Extract date from a result item, e.g. "announced on Month DD, YYYY" or "Submitted on ..."
    Returns date() or None.
    """
    date_text = ""
    for p in li.select("p.is-size-7"):
        t = p.get_text(" ", strip=True)
        if "announced" in t or "Submitted" in t:
            date_text = t
            break
    m = re.search(r"(announced|Submitted)\s+on\s+([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})", date_text)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(2), "%B %d, %Y").date()
    except Exception:
        return None

def _extract_abstract(li: BeautifulSoup) -> str:
    """
    Abstract is present only when abstracts=show.
    Try several containers, clean minor artifacts.
    """
    node = (li.select_one("span.abstract-full")
            or li.select_one("p.abstract")
            or li.select_one("span.abstract-short"))
    if not node:
        return ""
    txt = node.get_text(" ", strip=True)
    txt = re.sub(r"^\s*Abstract:\s*", "", txt, flags=re.I)
    txt = re.sub(r"\s*(Show less|△ Less|▽ More)\s*$", "", txt, flags=re.I)
    return txt.strip()


# ---------- Core: parse only latest day (early-stop) ----------
def parse_results_only_latest_day(html_text: str):
    """
    Parse just enough to collect results from the latest announced day.
    Assumes page sorted by -announced_date_first.
    Stops as soon as it encounters an item from a different date.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    items = []
    latest_date = None

    for li in soup.select("li.arxiv-result"):
        d = _extract_announced_date(li)
        # Set the target date based on the first item that has a date
        if d and latest_date is None:
            latest_date = d

        # If we know the latest_date and this item is older → stop
        if latest_date is not None and d is not None and d != latest_date:
            break

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
            "announced_date": d,   # may be None for some entries
            "abstract": abstract,
        })

    return items


# ---------- Feishu card ----------
def build_card(items):
    if not items:
        content = "没有符合条件的结果（最新批次为空）。"
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
            if len(abstract) > 700:  # keep messages compact
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

            lines += [
                head,
                meta,
                abstract if abstract else "_(no abstract on list page)_",
                links,
                ""
            ]
        content = "\n\n".join(lines)

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "arXiv 最新批次（含摘要）"},
                "template": "blue"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": content}}
            ],
        }
    }


# ---------- Runner ----------
def main():
    if not WEBHOOK_URL:
        print("Missing WEBHOOK_URL (Feishu Incoming Webhook).", flush=True)
        raise SystemExit(2)

    # Build query strictly with classification:
    full_query = build_web_query(ARXIV_QUERY, ARXIV_CLASSES, REQUIRE_PHYSICS_GROUP)
    url = build_search_url(full_query, RESULT_SIZE, ORDER, HIDE_ABSTRACTS)

    headers = {"User-Agent": "Mozilla/5.0 (arxiv-feishu-bot; classification-search)"}
    resp = requests.get(url, headers=headers, timeout=40)
    resp.raise_for_status()

    # Parse only the newest announced day (early-stop)
    items = parse_results_only_latest_day(resp.text)
    to_send = items[:TOP_SEND]

    payload = build_card(to_send)
    r = requests.post(WEBHOOK_URL, json=payload, timeout=30)
    r.raise_for_status()

    print("Feishu response:", r.text)
    print(f"URL used: {url}")
    print(f"Found {len(items)} items (latest day only); sent {len(to_send)}.")


if __name__ == "__main__":
    main()
