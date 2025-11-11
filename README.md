# 📡 Feishu arXiv Bot (with Classification Filter & Abstracts)

A lightweight GitHub Action that pushes **weekly arXiv updates** (每周一) to a **Feishu (Lark)** group using a webhook.
It filters papers by **physics classification** (e.g. `hep-ex`, `hep-ph`) and includes **abstracts** for each paper.  
Results are scraped from [arxiv.org/search](https://arxiv.org/search) using the `classification:` syntax (no API key required).

---

## 🚀 Features

✅ Uses **classification filters** (e.g., `hep-ex`, `hep-ph`, `nucl-ex`) — no cross-listing from `gr-qc`, `astro-ph`, etc.
✅ Sends only **the most recent arXiv announcements** within a configurable window（默认最近 7 天）。
✅ Fans out keywords so **each query runs independently**, 确保分类过滤清晰。
✅ Includes **abstracts**, authors, categories, and links (`abs` / `pdf`).
✅ Runs automatically via **GitHub Actions** (weekly Monday schedule) — now with a **local dry-run mode** for quick verification.
✅ Fully configurable via repository **Secrets** and **Environment Variables**.

---

## 🧠 Example Keywords & Classification

Define each keyword separately and let the workflow fan out:

- Keywords: `"dark matter"`, `"neutrino"`, `"TPC"`, `"xenon"`, `"argon"`, `"WIMP"`, `"CEvNS"`
- Classifications: `hep-th`, `hep-ex`, `hep-ph`, `nucl-ex`, `physics.ins-det`

每个关键词都会被单独查询，并强制套用上述分类限制，避免跨领域噪声。

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
| `ARXIV_CLASSES` | `hep-th,hep-ex,hep-ph,nucl-ex,physics.ins-det` |

*(Do not add quotes around the values.)*

### 3️⃣ Workflow Schedule & Keywords

Update `.github/workflows/arxiv-cron.yml` to set your keyword矩阵与运行频率：

1. 在 **Repository variables** 中新增 `ARXIV_KEYWORDS_JSON`，内容类似：
   ```json
   ["dark matter", "neutrino", "TPC", "xenon", "argon", "WIMP", "CEvNS"]
   ```
   > *每个关键词将单独运行一次工作流。*

2. 工作流默认在 **每周一 01:00 UTC** 触发，可根据需要调整 `cron`。
   运行时会自动把 `matrix.keyword` 写入 `ARXIV_QUERY` 环境变量，因此无需再维护 `ARXIV_QUERY` Secret。

---

## 🔧 Optional Environment Variables

| Variable | Default | Description |
|-----------|----------|-------------|
| `RESULT_SIZE` | `200` | Number of entries fetched from arXiv search |
| `TOP_SEND` | `0` | Max papers sent to Feishu or printed in dry-run (`0` = 不限制) |
| `ORDER` | `-announced_date_first` | Sort order on arXiv |
| `HIDE_ABSTRACTS` | `False` | Whether to hide abstracts on arXiv search page |
| `REQUIRE_PHYSICS_GROUP` | `1` | Restrict to physics main group in classification |
| `DRY_RUN` | `0` | When truthy, only prints summary instead of pushing to Feishu |
| `OFFLINE_FALLBACK` | `auto` | `auto` = enable fallback while dry-running; set `1`/`0` to force using or skipping bundled samples |
| `ANNOUNCEMENT_WINDOW_DAYS` | `7` | 最近多少天的公告会被保留 |

---

## 📄 Example Output in Feishu

> **1. A Search for Ultra-Light Vector Dark Matter with a Rotating Torsion Balance**  
> 作者：A. Smith et al.  |  日期：2025-10-28  |  类别：`hep-ex`  
> *We report results from a precision torsion-balance search for dark photons in the 10⁻¹⁵ – 10⁻¹² eV mass range…*  
> [abs](https://arxiv.org/abs/2510.21764) | [pdf](https://arxiv.org/pdf/2510.21764.pdf)

---

## 🧩 Local Testing

```bash
# 设置查询条件（每次 dry-run 可替换成任意单个关键词）
export ARXIV_QUERY="dark matter"
export ARXIV_CLASSES="hep-th,hep-ex,hep-ph,nucl-ex,physics.ins-det"
export ANNOUNCEMENT_WINDOW_DAYS="7"

# Dry-run：仅打印结果不推送，可快速确认搜索是否合理。
# 在无网络或 arxiv.org 屏蔽时，脚本会提示并回退到 sample_data/ 下的样例页面。
python arxiv_to_feishu.py --dry-run

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
        └── arxiv-cron.yml      # weekly GitHub Actions scheduler with keyword matrix
```

---

## 🕰️ Scheduling Tips
- `0 1 * * 1` → Monday 09:00 in Beijing / Sunday 17:00 in Los Angeles (Summer)
- `0 2 * * 1` → Monday 10:00 in Beijing / Sunday 18:00 in Los Angeles (Winter)

Use [crontab.guru](https://crontab.guru) to customize your schedule.
