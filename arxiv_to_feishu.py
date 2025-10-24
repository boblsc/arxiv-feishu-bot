# arxiv_to_feishu.py
import os, sys, json
from datetime import datetime, timedelta, timezone
import requests, feedparser

WEBHOOK_URL = os.getenv("WEBHOOK_URL")              # 飞书 Incoming Webhook（放在 GitHub Secrets）
DAYS         = int(os.getenv("DAYS", "1"))          # 只看最近 N 天（每天跑一次，设 1 正好）
MAX_RESULTS  = int(os.getenv("MAX_RESULTS", "20"))  # 从 arXiv 拉取的上限
TOP_SEND     = int(os.getenv("TOP_SEND", "5"))      # 实际推送的条数（避免太多）
# 关键词：dark matter OR neutrino
ARXIV_QUERY  = os.getenv("ARXIV_QUERY", "(dark matter) OR neutrino")
# 可选类别（留空表示不限；常用：hep-ex, hep-ph, astro-ph.CO, physics.ins-det）
ARXIV_CAT    = os.getenv("ARXIV_CAT", "")

ARXIV_API = "https://export.arxiv.org/api/query"

def query_arxiv(q, cat, days, max_results):
    qparts = []
    if q:   qparts.append(f"all:{q}")
    if cat: qparts.append(f"cat:{cat}")
    query = "+AND+".join(qparts) if qparts else "all:physics"
    params = {
        "search_query": query,
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
        # 取发布时间
        if e.get("published_parsed"):
            dt = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
        elif e.get("updated_parsed"):
            dt = datetime(*e.updated_parsed[:6], tzinfo=timezone.utc)
        else:
            dt = since
        if dt < since:
            continue

        title   = (e.title or "").strip().replace("\n"," ")
        summary = (e.summary or "").strip().replace("\n"," ")
        authors = ", ".join([a.name for a in e.get("authors", [])])
        abs_url = next((l.href for l in e.get("links", []) if l.rel == "alternate"), e.get("id",""))
        pdf_url = next((l.href for l in e.get("links", []) if getattr(l,"type","")=="application/pdf"), None)
        primary_cat = e.tags[0]["term"] if e.get("tags") else ""

        out.append({
            "id": e.get("id", abs_url),
            "title": title,
            "summary": summary,
            "authors": authors,
            "date": dt.strftime("%Y-%m-%d"),
            "abs": abs_url,
            "pdf": pdf_url or (abs_url.replace("/abs/","/pdf/") + ".pdf"),
            "cat": primary_cat,
        })
    return out

def build_card(items):
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
            "header": {"title": {"tag": "plain_text", "content": "arXiv 每日推送：dark matter / neutrino"}, "template": "blue"},
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
        print("缺少 WEBHOOK_URL 环境变量。", file=sys.stderr)
        sys.exit(2)

    hits = query_arxiv(ARXIV_QUERY, ARXIV_CAT, DAYS, MAX_RESULTS)
    # 取最新 TOP_SEND 篇
    to_send = hits[:TOP_SEND]
    payload = build_card(to_send)
    resp = post_to_feishu(payload)
    print("Feishu response:", resp)
    print(f"Pushed {len(to_send)} items.")

if __name__ == "__main__":
    main()
