<div align="center">

# 🧭 Complaint Compass

**Customer complaint intelligence and prioritization**

Turn complaints from email, voice calls, chatbots, social media and CSV exports into a
**ranked top 10 of problems to fix**, and see **which customer groups** each problem hurts.

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white)
![Tests](https://img.shields.io/badge/tests-106%20backend%20%C2%B7%2011%20frontend-brightgreen)

*Built for HackStellar, problem statement PS-19.*

</div>

---

## 🎯 The problem

A company hears about the same problem in a hundred different ways, from the same angry customer on four
channels, with no reliable way to tell which problem to fix first or whom it affects. Complaint Compass:

- 🧩 **groups differently worded complaints** that describe the same underlying problem,
- 🔁 **never double-counts** a customer who repeats a complaint across channels,
- 🏆 **ranks issues with a transparent formula** based on affected customers, severity, recurrence, growth and business impact,
- 👥 **shows which customer groups** (country, age group, income bracket) each issue affects.

## ✨ Features

| | |
|---|---|
| 📥 **Multi-channel ingestion** | CSV, email (`.eml` / `.mbox`), chatbot transcripts, social media exports and voice calls, transcribed **offline** with Whisper |
| 🪪 **Cross-channel identity** | A CRM-style customer directory links emails, phone numbers and social handles to customers, stored only as salted hashes |
| 🧠 **Semantic issue discovery** | Local sentence embeddings + HDBSCAN clustering group complaints by meaning, not keywords |
| 🔁 **Incident deduplication** | One customer's one problem is counted once, however many times and channels they report it |
| 🏆 **Transparent top 10** | Published scoring formula; every issue shows its full score breakdown and a plain-English explanation |
| 🎛️ **Priority settings** | Companies tune the formula weights and the business value of each category from the UI; the ranking updates instantly |
| 🤖 **AI where it helps** | GPT-4o-mini classifies complaints and names issues (with root-cause hypotheses); it never counts or ranks |
| 🔒 **Privacy by design** | PII masked before any AI call, contacts hashed, demographics never inferred, small segments hidden |
| 💸 **Cheap and resilient** | Parallel, cached AI calls (≈ US$0.05 for 616 complaints); works fully offline with keyword rules and no API key |

## 🧠 Core idea: complaint → incident → issue

| Concept | Meaning | Built by |
|---|---|---|
| 📨 **Complaint** | One message, as received | Ingestion |
| 🧷 **Incident** | One customer's one problem, reported any number of times on any channel | Deduplication |
| 📌 **Issue** | A recurring problem shared by many customers | Clustering |

> *Different customers, same problem* → same **issue** (more affected customers).
> *Same customer, same problem again* → same **incident** (counted once).
> Rankings use **unique affected customers**, never raw message counts.

## ⚙️ How it works

```
 CSV · Email · Chat · Social · Voice (Whisper, local)
                     │
                     ▼
   Ingestion + validation + identity resolution  ──►  SQLite
                     │  (background job)
                     ▼
 1 Preprocess   clean text · extract order IDs · mask PII              code
 2 Embed        meaning vector per complaint (all-MiniLM-L6-v2)        local model
 3 Classify     category · severity · summary per complaint            GPT-4o-mini (or keyword rules)
 4 Cluster      group similar complaints into issues (HDBSCAN)         algorithm
 5 Label        issue title · description · root-cause hypotheses     GPT-4o-mini (or TF-IDF keywords)
 6 Deduplicate  same customer + same problem → one incident            rules
 7 Score        transparent priority formula                           code
                     │
                     ▼
        FastAPI REST API  ──►  React dashboard
```

**Priority formula** (default weights, editable on the Priority settings page):

```
Priority = 0.35 × Impact + 0.25 × Severity + 0.20 × Recurrence + 0.10 × Growth + 0.10 × Business impact
```

| Component | Measures |
|---|---|
| **Impact** | Unique affected customers (log scale, so duplicates cannot inflate it) |
| **Severity** | 0.6 × average + 0.4 × 90th percentile of complaint severity (CRITICAL 100 · HIGH 75 · MEDIUM 45 · LOW 15) |
| **Recurrence** | Customers hit more than once + share of weeks the issue was active |
| **Growth** | Last 7 days vs the prior weekly average (neutral with < 14 days of data) |
| **Business impact** | Business value of the category + share of complaints mentioning money or orders |

**Deduplication rules** (only ever within one verified customer):

| Rule | Condition | Result |
|---|---|---|
| T1 | Shared order / transaction ID (typed, written or spoken: "order number 396765" → `ORD-396765`) | Merge |
| Guard | Two *different* order IDs | Never merge |
| T2 | Within 7 days **and** similar meaning **and** same issue or category | Merge |
| T3 | Partial evidence only | Separate incident + review queue |

📐 A full, editable diagram of the system is in [`docs/working_flow.excalidraw`](docs/working_flow.excalidraw)
(open it at [excalidraw.com](https://excalidraw.com) → Open).

## 🖥️ Dashboard

| Page | What you get |
|---|---|
| 📊 **Overview** | KPIs (reports, incidents, unique customers, duplicates collapsed, emerging issues), top 10, severity mix, volume over time |
| 📌 **Issues / Issue detail** | Sortable issue list; score breakdown with explanation, symptoms, root-cause hypotheses, representative complaints, segment breakdown, analyst edits |
| 👥 **Segments** | Breakdown by country, age group and income bracket, plus an issue × segment heatmap |
| 💬 **Complaints** | Every complaint with a "N reports · 1 incident" badge; click one to see every linked report and *why* it was linked, plus similar complaints |
| 🎛️ **Priority settings** | Sliders for the five formula weights and the value of each category; Save re-ranks every issue and shows rank changes |
| 📤 **Upload** | One tab per channel plus the customer directory, live progress through the 7 stages, per-row errors, history with retry |

A **filter bar** on every page (country, age group, income, channel, dates, severity) recomputes every score for the
selected group, and filters live in the URL so a view can be shared as a link.

## 🚀 Quick start

**Requirements:** Python 3.11+ (tested on 3.12), Node 20+ (tested on 24), ~1.5 GB of disk for PyTorch (CPU) and the
models. No Docker or cloud services needed; an OpenAI API key is optional.

### 1️⃣ Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu   # small CPU-only wheel
pip install -r requirements.txt
cp .env.example .env              # optional: set OPENAI_API_KEY to enable GPT-4o-mini
uvicorn app.main:app --port 8000  # first start downloads the embedding model (~90 MB)
```

API: http://localhost:8000 · interactive API docs: http://localhost:8000/docs

### 2️⃣ Frontend (second terminal)

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 (proxies /api to the backend)
```

### 3️⃣ Load the demo data

Open **Upload** in the dashboard and upload the files in [`uploads/`](uploads/), in folder order:

| Step | Upload tab | Files |
|---|---|---|
| 1 | CSV | `1_complaints_csv/sample_complaints.csv` (~620 complaints) |
| 2 | *(optional, terminal)* | `curl -F file=@uploads/2_population_optional/sample_population.csv http://localhost:8000/api/v1/analytics/populations`, so segments show rates instead of counts |
| 3 | Customer directory | `3_customer_directory/customer_directory.csv` |
| 4 | Email | everything in `4_email/` |
| 5 | Chat | `5_chat/chat_transcripts.json` |
| 6 | Social media | `6_social_media/social_posts.jsonl` |
| 7 | Voice calls | all `.wav` files **plus** `voice_manifest.csv` from `7_voice_calls/` |

The first voice upload downloads the Whisper `base` model (~150 MB). More detail is in [`uploads/README.md`](uploads/README.md).

### 🔎 Things to try

- **Complaints → search `396765`**: one customer reported a lost delivery 6 times on 4 channels (CSV, email, a tweet
  with only his handle, a chat with only his phone number typing "order number 396765"). All are **one incident,
  one affected customer**. Click any of them to see why each was linked.
- **Complaints → search `743937`**: the same customer writes again, with completely different wording, about being charged twice.
- **Priority settings → raise Severity**: the fraud / unauthorized-charges issue climbs the top 10.
- **Filter by country**: the whole top 10 is recomputed for that market.
- **Upload only the emails** to a fresh instance: with under 80 complaints the system switches to category grouping
  and still produces a meaningful top list.

## 🔐 Privacy and data handling

| | |
|---|---|
| ✅ **Sent to OpenAI** (only if a key is configured) | Complaint text **after** masking of emails, phone numbers, card numbers and SSNs; for issue labels, a few masked sample texts and keywords |
| 🚫 **Never sent** | Customer IDs, contact details, demographics, the original unmasked text, audio (Whisper runs locally), any counts or scores |
| 🔑 **Stored** | Contacts only as salted HMAC-SHA256 hashes; no names; demographics only as the brackets provided, never inferred from text |
| 🙈 **Shown** | Segments with fewer than 3 customers are hidden; segment views never return customer IDs |
| ⚠️ **Not masked** | Names written *inside* a message (e.g. a "Thanks, Vik" sign-off) are not detected by the pattern-based masking |

Run with `LLM_ENABLED=false` (or no key) and no text leaves the machine.

## 🔧 Configuration

All settings are environment variables, documented in [`backend/.env.example`](backend/.env.example). The most useful:

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | *(empty)* | Enables GPT-4o-mini; read only by the backend, never sent to the browser |
| `LLM_ENABLED` | `true` | Set `false` to force keyword rules |
| `LLM_CONCURRENCY` / `LLM_BATCH_SIZE` | `6` / `10` | Parallel GPT requests / complaints per request |
| `TRANSCRIBER` | `local` | `local` (offline Whisper), `openai` or `none` |
| `IDENTITY_SALT` | sample value | **Set your own** secret salt for contact hashing |
| `SEMANTIC_CLUSTERING_MIN_COMPLAINTS` | `80` | Below this, issues are formed per category |
| `WEIGHT_IMPACT` … `WEIGHT_BUSINESS` | `0.35` … `0.10` | Default priority weights (must sum to 1; the UI can override them) |
| `DEDUP_TIME_WINDOW_DAYS` / `DEDUP_SEMANTIC_THRESHOLD` | `7` / `0.55` | Deduplication rule T2 |
| `SEGMENT_MIN_CUSTOMERS` | `3` | Privacy threshold for segment views |

**💾 Caching and reset.** GPT answers are cached in `backend/cache/llm_cache.db`, keyed by complaint text, so
re-processing the same data costs nothing. To reset the application, **stop the backend**, run
`rm -rf backend/storage` (SQLite + vectors + custom priority settings), and start it again. Keep `backend/cache/`
unless you want fresh GPT answers.

## 📈 Results

Measured by [`backend/scripts/evaluate.py`](backend/scripts/evaluate.py) on the 616-complaint sample against a
hidden answer key the pipeline never reads (keyword-rule mode, so anyone can reproduce it):

| Area | Metric | Result |
|---|---|---|
| 🔍 Semantic similarity | Precision@10 (neighbours describing the same issue family) | **0.958** |
| 📌 Clustering | ARI / NMI (18 issues found for 10 true families) | 0.684 / 0.827 |
| 🔁 Deduplication | Pairwise precision / recall | **0.986** / 0.423 |
| 🔁 Deduplication | Incidents wrongly mixing two customers | **0** |
| 🏆 Ranking | NDCG@10 against the ranking from perfect labels | **0.829** |
| 📥 Multi-channel | Cross-channel re-reports merged into the original incident | **22 / 22** |
| 📥 Multi-channel | New complaints wrongly merged | **0 / 39** |
| 🎙️ Voice | Spoken order numbers recovered | 4 / 4 |
| ⚡ Speed | Full pipeline, 616 complaints, no GPT | ~9 s |

Severity accuracy depends on the classifier: keyword rules reach macro-F1 0.34; GPT-4o-mini reached 0.51 in a
one-off comparison on cached answers (catching 45 % vs 16 % of critical complaints). Full results:
[`docs/evaluation.md`](docs/evaluation.md).

> ⚠️ The dataset is synthetic and template-based, so these numbers are indicative, not a real-world benchmark.

## 🧪 Tests

```bash
cd backend && .venv/bin/pytest -q                  # 106 tests (1 OpenAI integration test skips without a key)
cd backend && .venv/bin/python scripts/evaluate.py --channels --write-report   # metrics → docs/evaluation.md
cd frontend && npm test                            # 11 Vitest unit tests
cd frontend && npm run build                       # type-check + production build
```

## 🗂️ Project layout

```
backend/
  app/
    api/            FastAPI routers (complaints, channels, batches, issues, analytics, settings)
    core/           configuration, application startup (models, vector store, database)
    ingestion/      CSV / email / chat / social / voice adapters, Whisper, identity resolution, customer directory
    preprocessing/  text cleaning, PII masking, order-ID extraction
    inference/      embeddings, vector store, clustering, GPT classifier + keyword fallback, LLM cache
    deduplication/  incident matching
    analytics/      severity, prioritization, segmentation, trends
    services/       pipeline orchestrator, ingestion, analytics, priority settings
    database/       SQLite schema and all SQL (repository)
  scripts/          dataset generators, evaluation
  data/             sample data + answer keys (evaluation only)
  tests/
frontend/src/       pages, components, hooks, API client, types
docs/               ARCHITECTURE.md · API.md · evaluation.md · working_flow.excalidraw
uploads/            demo files, numbered in upload order
```

## ⚠️ Known limitations

- 📁 Channels are **file uploads**; there are no live connectors (IMAP, chatbot webhooks, social APIs) yet.
- 🖥️ One machine, **one processing job at a time** (SQLite, in-process background tasks); not load-tested beyond ~700 complaints.
- 🔑 **No authentication** or roles, and no per-customer data deletion yet.
- 🧷 Deduplication is deliberately conservative (recall 0.42); uncertain matches go to a review queue that has an API (`GET /api/v1/complaints/review/duplicates`) but no screen yet.
- 📌 Clustering can split one problem by phrasing (18 issues for 10 true families on the sample).
- 🙈 Names written inside messages are not masked before being sent to GPT.
- 🎙️ Voice has no speaker separation, so the agent's words are part of the transcript.
- 👥 State and student status are supported by the backend but hidden in the UI; add them back in [`frontend/src/lib/dimensions.ts`](frontend/src/lib/dimensions.ts).

## 🗺️ Roadmap

- 🔌 Live channel connectors and a scheduled CRM sync
- ⚙️ Job queue with workers, PostgreSQL + pgvector, incremental issue assignment
- 🛡️ Authentication, roles and audit log; per-customer erasure and retention policies
- ✂️ Strip names and sign-offs before AI calls; optional self-hosted LLM for fully on-premise use
- 🧷 Review screen for suggested merges and an analyst "merge issues" action
- ⚖️ Side-by-side segment comparison (e.g. top 10 for India vs the US)

## 📚 Documentation

- [Architecture](docs/ARCHITECTURE.md): design decisions and data model
- [API](docs/API.md): REST endpoints (interactive version at `/docs` when the backend runs)
- [Evaluation](docs/evaluation.md): measured results and caveats
- [System diagram](docs/working_flow.excalidraw): complete working flow, open in Excalidraw
