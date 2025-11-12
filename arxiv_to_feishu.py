# arxiv_to_feishu.py
# 模式：网页搜索（classification）+ 以 https://arxiv.org/localtime 推断“最新公告日” + 含摘要
# 依赖：仅标准库（无需额外安装）

import argparse
import json
import os
import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

# ===== 环境变量（值不要加引号）=====
WEBHOOK_URL   = os.getenv("WEBHOOK_URL")  # 必填：飞书 Incoming Webhook

# 关键词（放 Secrets 更安全）
ARXIV_QUERY   = os.getenv(
    "ARXIV_QUERY",
    "dark matter"
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

# 推送条数上限（0 或负数表示不限制，默认发送全部匹配结果）
TOP_SEND = int(os.getenv("TOP_SEND", "0"))

# 公告窗口（单位：天），默认最近 7 天
ANNOUNCEMENT_WINDOW_DAYS = max(1, int(os.getenv("ANNOUNCEMENT_WINDOW_DAYS", "7")))

# 测试模式：仅打印最新公告日条目，不推送到飞书
DRY_RUN = os.getenv("DRY_RUN", "0").lower() in ("1", "true", "yes")

# Debug 模式：输出更多诊断信息，并将查询参数传递到飞书卡片
DEBUG_MODE = os.getenv("DEBUG", "0").lower() in ("1", "true", "yes")

# 离线兜底：当网络访问受限时是否允许回退到本地样例数据（auto=随 Dry-run 而定）
_OFFLINE_ENV = os.getenv("OFFLINE_FALLBACK", "auto").lower()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.join(BASE_DIR, "sample_data")
SAMPLE_SEARCH_FILE = os.path.join(SAMPLE_DIR, "sample_search.html")
SAMPLE_LOCALTIME_FILE = os.path.join(SAMPLE_DIR, "sample_localtime.html")

SEARCH_BASE = "https://arxiv.org/search/"
ARXIV_LOCALTIME = "https://arxiv.org/localtime"


def _load_sample_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def _http_get_text(
    url: str,
    *,
    headers=None,
    timeout: int = 30,
    fallback_text: Optional[str] = None,
    fallback_path: Optional[str] = None,
    allow_offline: bool = False,
) -> str:
    headers = headers or {}
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except HTTPError as exc:
        err = RuntimeError(f"HTTP {exc.code} when requesting {url}")
        orig_exc = exc
    except URLError as exc:
        err = RuntimeError(f"Failed to request {url}: {exc.reason}")
        orig_exc = exc

    if allow_offline:
        offline_text = fallback_text
        if offline_text is None and fallback_path:
            offline_text = _load_sample_text(fallback_path)
        if offline_text is not None:
            source = fallback_path if fallback_path else "inline sample"
            print(f"[offline fallback] {err}. Using {source}.", flush=True)
            return offline_text

    raise err from orig_exc


def _http_post_json(url: str, payload: Dict, *, headers=None, timeout: int = 30) -> str:
    data = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        req_headers.update(headers)
    req = Request(url, data=data, headers=req_headers, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} when posting to {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to post to {url}: {exc.reason}") from exc


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

def _get_et_now_from_localtime(*, allow_offline: bool = False) -> datetime:
    """
    从 https://arxiv.org/localtime 抓取 'Tue, 28 Oct 2025 22:40 EDT' 这行，解析成 ET 的 datetime（无 tz，仅用于日期/小时判断）。
    """
    headers = {"User-Agent": "Mozilla/5.0 (arxiv-feishu-bot; localtime-check)"}
    html = _http_get_text(
        ARXIV_LOCALTIME,
        headers=headers,
        timeout=20,
        fallback_path=SAMPLE_LOCALTIME_FILE,
        allow_offline=allow_offline,
    )
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


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class _ArxivResultParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items: List[Dict] = []
        self._in_item = False
        self._depth = 0
        self._current = None
        self._target_stack = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        classes = attrs_dict.get("class", "")
        class_list = classes.split()

        if tag == "li" and not self._in_item and "arxiv-result" in class_list:
            self._start_item()
            return

        if not self._in_item:
            return

        self._depth += 1

        def push_buffer(name: str):
            self._current["buffers"].setdefault(name, [])
            self._target_stack.append((tag, name))

        if tag == "p" and "title" in class_list:
            push_buffer("title")
        elif tag == "p" and "authors" in class_list:
            push_buffer("authors")
        elif tag in ("span", "p") and any(cls.startswith("abstract") for cls in class_list):
            push_buffer("abstract")
        elif tag == "span" and "tag" in class_list:
            if self._current["buffers"].get("cat"):
                self._current["buffers"]["cat"].append(" ")
            push_buffer("cat")
        elif tag == "p" and "is-size-7" in class_list:
            name = f"meta_{len(self._current['meta_names'])}"
            self._current["meta_names"].append(name)
            push_buffer(name)

        if tag == "a":
            href = attrs_dict.get("href", "")
            if href:
                if "/abs/" in href and not self._current.get("abs_link"):
                    self._current["abs_link"] = href
                if "/pdf/" in href and href.endswith(".pdf"):
                    self._current["pdf_link"] = href

    def handle_startendtag(self, tag, attrs):
        # Treat self-closing tags as start+end to keep depth balanced.
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self._in_item:
            return

        if tag == "li":
            self._depth -= 1
            if self._depth <= 0:
                item = self._finalize_item()
                if item:
                    self.items.append(item)
                self._in_item = False
                self._current = None
                self._target_stack = []
            return

        idx = len(self._target_stack) - 1
        while idx >= 0:
            entry_tag, _ = self._target_stack[idx]
            if entry_tag == tag:
                self._target_stack.pop(idx)
                break
            idx -= 1

        self._depth -= 1

    def handle_data(self, data):
        if not self._in_item or not data:
            return
        for _, name in self._target_stack:
            self._current["buffers"].setdefault(name, []).append(data)

    def _start_item(self):
        self._in_item = True
        self._depth = 1
        self._current = {
            "buffers": {},
            "meta_names": [],
            "abs_link": None,
            "pdf_link": None,
        }
        self._target_stack = []

    def _finalize_item(self):
        buffers = self._current["buffers"]

        def get_text(name: str) -> str:
            text = "".join(buffers.get(name, []))
            return _normalize_ws(text)

        title = get_text("title")
        if title.lower().startswith("title:"):
            title = title[len("title:"):].strip()
        authors = get_text("authors")
        if authors.lower().startswith("authors:"):
            authors = authors[len("authors:"):].strip()
        authors = _normalize_ws(authors)
        abstract = get_text("abstract")
        if abstract:
            abstract = re.sub(r"^Abstract:\s*", "", abstract, flags=re.I)
            abstract = re.sub(r"\s*(Show less|△ Less|▽ More)\s*$", "", abstract, flags=re.I)
        category = get_text("cat")
        category = _normalize_ws(category)

        announced_date = None
        for meta_name in self._current["meta_names"]:
            meta_text = get_text(meta_name)
            if not meta_text:
                continue
            m = _DATE_RE.search(meta_text)
            if m:
                try:
                    announced_date = datetime.strptime(m.group(2), "%B %d, %Y").date()
                    break
                except ValueError:
                    continue

        abs_link = self._current.get("abs_link") or ""
        pdf_link = self._current.get("pdf_link") or ""
        if abs_link and not abs_link.startswith("http"):
            abs_link = "https://arxiv.org" + abs_link
        if pdf_link and not pdf_link.startswith("http"):
            pdf_link = "https://arxiv.org" + pdf_link
        if not pdf_link and abs_link and "/abs/" in abs_link:
            pdf_link = abs_link.replace("/abs/", "/pdf/") + ".pdf"

        return {
            "title": title,
            "authors": authors,
            "abs": abs_link,
            "pdf": pdf_link,
            "cat": category,
            "announced_date": announced_date,
            "abstract": abstract,
        }


def parse_all_items(html_text: str):
    parser = _ArxivResultParser()
    parser.feed(html_text)
    parser.close()
    return parser.items

# ---------- 仅保留“最新公告日”的条目 ----------
def filter_by_date_window(items, start_date: Optional[date], end_date: Optional[date]):
    if start_date is None and end_date is None:
        return items

    def in_window(item_date: Optional[date]) -> bool:
        if item_date is None:
            return False
        if start_date and item_date < start_date:
            return False
        if end_date and item_date > end_date:
            return False
        return True

    return [it for it in items if in_window(it.get("announced_date"))]


def summarize_items(items: List[Dict]) -> str:
    if not items:
        return "(no items)"

    lines = []
    for idx, item in enumerate(items, 1):
        title = item.get("title") or "(no title)"
        date_obj = item.get("announced_date")
        date_str = date_obj.isoformat() if isinstance(date_obj, date) else "?"
        category = item.get("cat") or ""
        lines.append(f"{idx}. {title} [{date_str}] {category}")
    return "\n".join(lines)

# ---------- 生成飞书卡片 ----------
def build_card(
    items,
    *,
    debug_lines: Optional[List[str]] = None,
    intro_text: Optional[str] = None,
    header_title: str = "arXiv 最近公告（含摘要）",
):
    if not items:
        content = "没有符合条件的结果（指定时间范围内无匹配）。"
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

    elements: List[Dict] = []
    if debug_lines:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(debug_lines)}})
    if intro_text:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": intro_text}})
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": header_title[:80]},
                "template": "blue"
            },
            "elements": elements,
        }
    }

# ---------- 主流程 ----------
def _resolve_offline_flag(cli_flag: Optional[bool]) -> bool:
    if cli_flag is not None:
        return cli_flag

    if _OFFLINE_ENV in ("1", "true", "yes", "on"):
        return True
    if _OFFLINE_ENV in ("0", "false", "no", "off"):
        return False

    # auto → 跟随 Dry-run
    return DRY_RUN


def fetch_latest_announcements(
    *,
    allow_offline: bool = False,
    window_days: Optional[int] = None,
    top_limit: Optional[int] = None,
):
    """Fetch and parse arXiv search results, returning metadata for later processing."""

    window_days = window_days if window_days and window_days > 0 else ANNOUNCEMENT_WINDOW_DAYS
    top_limit = top_limit if top_limit and top_limit > 0 else TOP_SEND if TOP_SEND > 0 else None

    et_now = _get_et_now_from_localtime(allow_offline=allow_offline)
    target_date = _most_recent_announcement_date(et_now)

    full_query = build_web_query(ARXIV_QUERY, ARXIV_CLASSES, REQUIRE_PHYSICS_GROUP)
    url = build_search_url(full_query, RESULT_SIZE, ORDER, HIDE_ABSTRACTS)
    headers = {"User-Agent": "Mozilla/5.0 (arxiv-feishu-bot; classification-search)"}
    resp_text = _http_get_text(
        url,
        headers=headers,
        timeout=40,
        fallback_path=SAMPLE_SEARCH_FILE,
        allow_offline=allow_offline,
    )

    all_items = parse_all_items(resp_text)

    window_start = target_date - timedelta(days=window_days - 1) if window_days > 1 else target_date
    window_end = target_date
    filtered_items = filter_by_date_window(all_items, window_start, window_end)
    if top_limit:
        filtered_items = filtered_items[:top_limit]

    return {
        "all_items": all_items,
        "filtered_items": filtered_items,
        "target_date": target_date,
        "window_start": window_start,
        "window_end": window_end,
        "et_now": et_now,
        "query": full_query,
        "url": url,
    }


def main():
    parser = argparse.ArgumentParser(description="Fetch latest arXiv announcements and post to Feishu.")
    parser.add_argument("--offline", dest="allow_offline", action="store_true", help="允许在网络失败时使用本地样例数据")
    parser.add_argument("--no-offline", dest="allow_offline", action="store_false", help="禁用离线兜底")
    parser.add_argument("--window-days", type=int, default=None, help="公告窗口天数（覆盖环境变量）")
    parser.add_argument("--top", type=int, default=None, help="推送前保留的条目数量（覆盖 TOP_SEND）")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help="仅打印结果，不推送飞书")
    parser.add_argument("--send", dest="dry_run", action="store_false", help="强制推送至飞书")
    parser.add_argument("--intro", default=None, help="卡片开头补充说明")
    parser.set_defaults(allow_offline=None, dry_run=DRY_RUN)
    args = parser.parse_args()

    allow_offline = _resolve_offline_flag(args.allow_offline)
    result = fetch_latest_announcements(
        allow_offline=allow_offline,
        window_days=args.window_days,
        top_limit=args.top,
    )

    items = result["filtered_items"]
    summary = summarize_items(items)
    print("Summary:\n" + summary)

    debug_lines = None
    if DEBUG_MODE:
        debug_lines = [
            f"Query: `{result['query']}`",
            f"URL: {result['url']}",
            f"ET now: {result['et_now'].isoformat(sep=' ')}",
            f"Window: {result['window_start']} → {result['window_end']}",
            f"Fetched: {len(result['all_items'])} | Selected: {len(items)}",
        ]

    header_title = f"arXiv 公告 {result['window_start']} - {result['window_end']}"
    payload = build_card(
        items,
        debug_lines=debug_lines,
        intro_text=args.intro,
        header_title=header_title,
    )

    dry_run = args.dry_run
    if dry_run:
        print("Dry-run mode: 不推送飞书。")
        return

    if not WEBHOOK_URL:
        raise RuntimeError("WEBHOOK_URL 未配置，无法推送到飞书。")

    response_text = _http_post_json(WEBHOOK_URL, payload, timeout=30)
    print("Feishu response:", response_text)


if __name__ == "__main__":
    main()
