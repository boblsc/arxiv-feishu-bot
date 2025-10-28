# arxiv_to_feishu.py
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
import feedparser

# ========= 配置（均可用 GitHub Secrets / env 覆盖） =========
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # 必填：飞书 Incoming Webhook URL
DAYS        = int(os.getenv("DAYS", "1"))           # 回看天数（建议 1）
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "20"))   # 向 arXiv 请求的上限
TOP_SEND    = int(os.getenv("TOP_SEND", "5"))       # 实际发送的条数上限

# 搜索关键词与分类（从 Secrets 读取）。ARXIV_CATS 支持多分类，用逗号或空格分隔
ARXIV_QUERY = os.getenv("ARXIV_QUERY", "(dark matter) OR neutrino")
ARXIV_CATS  = os.getenv("ARXIV_CATS", os.getenv("ARXIV_CAT", ""))  # 兼容旧名 ARXIV_CAT

ARXIV_API   = "https://export.arxiv.org/api/query"


def _build_search_query(q: str, cats: str) -> str:
    """构造 arXiv API 的 search_query。关键词用 all:，多分类用 OR 拼接 (cat:a OR cat:b)。"""
    qparts = []
    if q:
        # q 原样传入；这里不做 URL 编码，交给 requests 处理。
        qparts.append(f"all:{q}")

    if cats:
        # 支持逗号或空格分隔；去重并保持顺序
        raw = cats.replace(" ", ",").split(",")
        seen = set()
        cat_list = []
        for c in raw:
            c = c.strip()
            if c and c not in seen:
                seen.add(c)
                cat_list.append(c)
        if len(cat_list) == 1:
            qparts.append(f"cat:{cat_list[0]}")
        elif len(cat_list) > 1:
            cat_expr = " OR ".join([f"cat:{c}" for c in cat_list])
            qparts.append(f"({cat_expr})")

    return "+AND+".join(qparts) if qparts else "all:physics"


def query_arxiv(q: str, cats: str, days: int, max_results: int):
    """查询 arXiv，并按 days 进行本地时间窗过滤，返回列表结果。"""
    search_query = _build_search_query(q, cats)

    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    r = requests.get(ARXIV_API, params=params, timeout=20)
    r.raise_for_status()
    feed = feedparser.parse(r.text)

    since = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for e in feed.entries:
        # 取发布时间（优先 published，其次 updated）
        if e.get("published_parsed"):
            dt = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
        elif e.get("updated_parsed"):
            dt = datetime(*e.updated_parsed[:6], tzinfo=timezone.utc)
        else:
            dt = since

        if dt < since:
            continue

        title   = (e.title or "").strip().replace("\n", " ")
        summary = (e.summary or "").strip().replace("\n", " ")
        authors = ", ".join([a.name for a in e.get("authors", [])])

        abs_url = next((l.href for l in e.get("links", []) if l.rel == "alternate"), e.get("id", ""))
        pdf_url = next((l.href for l in e.get("links", []) if getattr(l, "type", "") == "application/pdf"), None)
        primary_cat = e.tags[0]["term"] if e.get("tags") else ""

        out.append({
            "id": e.get("id", abs_url),
            "title": title,
            "summary": summary,
            "authors": authors,
            "date": dt.strftime("%Y-%m-%d"),
            "abs": abs_url,
            "pdf": pdf_url or (abs_url.replace("/abs/", "/pdf/") + ".pdf"),
            "cat": primary_cat,
        })
    return out


def build_card(items):
    """构造飞书 webhook 的交互式卡片（简单 markdown 内容）。"""
    if not items:
        content = "今天没有符合条件的新论文。"
    else:
        lines = []
        for i, it in enumerate(items, 1):
            desc = it["summary"]
            if len(desc) > 300:
                desc = desc[:300] + " …"
            lines += [
                f"**{i}. {it['title']}**",
                f"作者：{it['authors']}  |  日期：{it['date']}  |  类别：`{it['cat']}`",
                desc,
                f"[abs]({it['abs']})  |  [pdf]({it['pdf']})",
                ""
            ]
        content = "\n\n".join(lines)

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "arXiv 每日推送"},
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
        print("缺少 WEBHOOK_URL 环境变量（应配置在 GitHub Secrets）。", file=sys.stderr)
        sys.exit(2)

    hits = query_arxiv(ARXIV_QUERY, ARXIV_CATS, DAYS, MAX_RESULTS)
    to_send = hits[:TOP_SEND]  # 控制实际发送条数
    payload = build_card(to_send)
    resp = post_to_feishu(payload)
    print("Feishu response:", resp)
    print(f"Pushed {len(to_send)} items.")


if __name__ == "__main__":
    main()
