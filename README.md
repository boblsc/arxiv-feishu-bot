# 📡 Feishu arXiv Bot (with Classification Filter & Abstracts)

A lightweight GitHub Action that pushes **daily arXiv updates** to a **Feishu (Lark)** group using a webhook.  
It filters papers by **physics classification** (e.g. `hep-ex`, `hep-ph`) and includes **abstracts** for each paper.  
Results are scraped from [arxiv.org/search](https://arxiv.org/search) using the `classification:` syntax (no API key required).

---

## 🚀 Features

✅ Uses **classification filters** (e.g., `hep-ex`, `hep-ph`, `nucl-ex`) — no cross-listing from `gr-qc`, `astro-ph`, etc.  
✅ Sends only **today’s new arXiv papers** (configurable).  
✅ Includes **abstracts**, authors, categories, and links (`abs` / `pdf`).  
✅ Runs automatically via **GitHub Actions** (daily schedule) — now with a **local dry-run mode** for quick verification.
✅ Fully configurable via repository **Secrets** and **Environment Variables**.

---

## 🧠 Example Query

By default, the bot searches for high-energy and detector-related topics:

```
(dark matter OR neutrino OR TPC OR xenon OR argon OR WIMP OR CEvNS)
AND (classification:hep-th OR classification:hep-ex OR classification:hep-ph
     OR classification:nucl-ex OR classification:physics.ins-det)
```

This ensures results are **only** from relevant HEP and instrumentation categories.

---

## ⚙️ Setup

### 1️⃣ Create a Feishu Webhook
1. In your Feishu group → “Integrations” → “Custom Bot”.
2. Copy the **Webhook URL** (e.g. `https://open.feishu.cn/open-apis/bot/v2/hook/...`).

### 2️⃣ Add Secrets to GitHub
Go to your repository → **Settings → Secrets and variables → Actions → New repository secret**, and add:

| Name | Example Value |
|------|----------------|
| `FEISHU_WEBHOOK_URL` | `https://open.feishu.cn/open-apis/bot/v2/hook/...` |
| `ARXIV_QUERY` | `dark matter OR neutrino OR TPC OR xenon OR argon OR WIMP OR CEvNS` |
| `ARXIV_CLASSES` | `hep-th,hep-ex,hep-ph,nucl-ex,physics.ins-det` |

*(Do not add quotes around the values.)*

### 3️⃣ Workflow Schedule

Edit `.github/workflows/arxiv-cron.yml`:

```yaml
on:
  schedule:
    - cron: '0 16 * * *'   # every day 16:00 UTC = 09:00 Los Angeles
```

---

## 🔧 Optional Environment Variables

| Variable | Default | Description |
|-----------|----------|-------------|
| `RESULT_SIZE` | `200` | Number of entries fetched from arXiv search |
| `TOP_SEND` | `10` | Max papers sent to Feishu or printed in dry-run |
| `ORDER` | `-announced_date_first` | Sort order on arXiv |
| `HIDE_ABSTRACTS` | `False` | Whether to hide abstracts on arXiv search page |
| `REQUIRE_PHYSICS_GROUP` | `1` | Restrict to physics main group in classification |
| `DRY_RUN` | `0` | When truthy, only prints summary instead of pushing to Feishu |
| `OFFLINE_FALLBACK` | `auto` | `auto` = enable fallback while dry-running; set `1`/`0` to force using or skipping bundled samples |

---

## 📄 Example Output in Feishu

> **1. A Search for Ultra-Light Vector Dark Matter with a Rotating Torsion Balance**  
> 作者：A. Smith et al.  |  日期：2025-10-28  |  类别：`hep-ex`  
> *We report results from a precision torsion-balance search for dark photons in the 10⁻¹⁵ – 10⁻¹² eV mass range…*  
> [abs](https://arxiv.org/abs/2510.21764) | [pdf](https://arxiv.org/pdf/2510.21764.pdf)

---

## 🧩 Local Testing

```bash
# 设置查询条件（可使用仓库 Secrets 覆盖）
export ARXIV_QUERY="dark matter OR neutrino OR TPC OR xenon OR argon OR WIMP OR CEvNS"
export ARXIV_CLASSES="hep-th,hep-ex,hep-ph,nucl-ex,physics.ins-det"

# Dry-run：仅打印结果不推送，可快速确认搜索是否合理。
# 在无网络或 arxiv.org 屏蔽时，脚本会提示并回退到 sample_data/ 下的样例页面。
python arxiv_to_feishu.py --dry-run --top 5

# 准备好后移除 --dry-run 或设置 WEBHOOK_URL 运行正式推送
export WEBHOOK_URL="https://open.feishu.cn/open-apis/bot/v2/hook/xxxx"
python arxiv_to_feishu.py
```

---

## 🛠 Dependencies

The script now relies only on Python's standard library, so no additional packages are required for a local dry-run.

---

## 📘 File Structure

```
├── arxiv_to_feishu.py          # main script (scrapes arXiv + posts to Feishu)
├── sample_data/
│   ├── sample_localtime.html   # /localtime 页面样例（离线 dry-run 使用）
│   └── sample_search.html      # arXiv 搜索结果样例（离线 dry-run 使用）
└── .github/
    └── workflows/
        └── arxiv-cron.yml      # daily GitHub Actions scheduler
```

---

## 🕰️ Scheduling Tips
- `0 16 * * *` → 09:00 AM Los Angeles (Summer)  
- `0 17 * * *` → 09:00 AM Los Angeles (Winter)

Use [crontab.guru](https://crontab.guru) to customize your schedule.
