# Complaint Compass: Technical Report

**HackStellar PS-19: Customer Complaint Intelligence and Prioritization**

This report explains how the system works from end to end. It is written for the team members presenting the
project: read it once fully, then use section 12 (demo script) and section 13 (judge Q&A) on stage.

---

## 1. The problem in one paragraph

A company receives complaints through email, phone calls, chatbots and social media. Read one by one, they are
noise: the same problem is described in a hundred different ways, the same angry customer writes on four channels,
and nobody can say which problem to fix first or who it hurts. The problem statement asks for a system that:

| # | Requirement (from PS-19) | Where we handle it |
|---|---|---|
| 1 | Consolidate complaints across **email, voice, chatbot, social media** | Ingestion adapters, one per channel (§4) |
| 2 | Recognise **differently worded** complaints describing the **same problem** | Embeddings + clustering (§6) |
| 3 | **Avoid double-counting** complaints repeated across channels | Identity resolution + incident deduplication (§5, §8) |
| 4 | Identify the **top 10 recurring issues** | Issue discovery + ranking (§6, §9) |
| 5 | Rank with **transparent criteria** (affected customers, severity, …) | Published weighted formula with a per-issue breakdown (§9) |
| 6 | **Filters and comparison** by country, state, age group, student status, income | Filter bar on every page + Segments page (§10) |
| 7 | Show **which problems affect which customer groups** and **where to focus** | Issue × segment heatmap, issue pages with symptoms and root-cause hypotheses (§10, §11) |

---

## 2. Big picture

```
 INPUT CHANNELS                         PROCESSING PIPELINE (backend, Python)                    OUTPUT
 ──────────────                         ─────────────────────────────────────                    ──────
 CSV export        ─┐
 Email (.eml/.mbox)─┤                    1 Preprocess   clean text, mask PII, extract order IDs
 Chat transcripts  ─┼─► Ingestion  ───►  2 Embed        text → 384-number meaning vector (local model)
 Social posts      ─┤   adapters         3 Classify     category + severity + summary   (GPT)
 Voice calls ──► Whisper ┘   │           4 Cluster      group same-problem complaints → issues
                             │           5 Label        title, description, root causes  (GPT)
 Customer directory ─► Identity          6 Deduplicate  same customer + same problem → 1 incident
 (CRM export)          resolution        7 Score        transparent priority formula
                                                  │
                                    SQLite + ChromaDB (vector store)
                                                  │
                                         REST API (FastAPI)  ──►  React dashboard
                                                                   Overview · Issues · Segments ·
                                                                   Complaints · Upload
```

**Technology**

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.12, FastAPI | Fast to build, async API, automatic API docs at `/docs` |
| Storage | SQLite (relational data) + ChromaDB (vectors) | Zero setup, runs on a laptop, no cloud needed |
| Embeddings | `all-MiniLM-L6-v2` (sentence-transformers), runs locally | Small (90 MB), fast on CPU, good semantic quality |
| Clustering | HDBSCAN (scikit-learn) | Finds the number of issues itself; leaves outliers alone |
| LLM | GPT-4o-mini (OpenAI) with structured outputs, **optional** | Cheap, accurate classification and labeling; deterministic fallback without it |
| Speech-to-text | faster-whisper (`base` model), runs locally | Voice calls transcribed offline, no audio leaves the machine |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, Recharts | Modern, typed, fast dashboard |

---

## 3. Three core concepts (the most important slide)

Judges will ask "how do you avoid double counting?" The answer is that the system separates three things that are
usually mixed up:

| Concept | Meaning | Example |
|---|---|---|
| **Complaint** (report) | One message, as received | Vik's tweet about order ORD-396765 |
| **Incident** | One customer's one problem, however many times and channels they report it | Vik's undelivered order, reported 6 times on 4 channels → **1 incident** |
| **Issue** | A recurring problem shared by many customers | "Delayed deliveries", containing Vik's incident and many others |

- *Different customers, same problem* → same **issue** (counted as more affected customers). Done by **clustering**.
- *Same customer, same problem, again* → same **incident** (counted once). Done by **deduplication**.

Every count on the dashboard says which one it is: *reports*, *incidents*, or *unique affected customers*.
Rankings use unique customers, never raw report counts.

---

## 4. Ingestion: getting complaints in from every channel

Each channel has its own adapter that turns the raw format into one standard complaint record (text, timestamp,
customer, source). Nothing after ingestion knows or cares which format the complaint came from.

| Channel | File types | What becomes the complaint text | Who the customer is | Rejected on purpose |
|---|---|---|---|---|
| CSV | `.csv` | `complaint_text` column | `customer_id` column | Rows with missing/invalid text or dates (reported per row) |
| Email | `.eml`, `.mbox` | Subject + body; HTML converted; quoted replies and signatures removed | `X-Customer-ID` header, else sender address via directory | Auto-replies, bounces, bulk mail |
| Chat | `.json`, `.jsonl` | Only the **customer's** turns (bot and agent text dropped) | `customer_id`, else email/phone via directory | Conversations with no customer message |
| Social media | `.json`, `.jsonl`, `.csv` | Post text | `customer_id`, else **handle** via directory | Reposts / retweets |
| Voice | Audio (`.wav`, …) + manifest CSV | **Whisper transcript**; spoken digits re-joined ("four eight one…" → 481…) | Manifest `customer_id`, else phone via directory | No speech / unreadable audio |

**Validation.** Every channel reuses the same row validator: text length, valid timestamps, valid customer IDs.
Bad items are reported individually ("row 12: unparseable timestamp") while the valid ones are kept. A file in
which *every* item is invalid is closed immediately with a hint ("Check that the file was uploaded on the right tab").
Uploading the exact same file twice is refused (HTTP 409), so data is never imported twice.

### 4.1 Identity resolution (who is this customer?)

The customer behind a complaint is decided in this order:

1. **Explicit customer ID** in the data → used directly.
2. **Customer directory match**: the sender's email, phone (last 10 digits) or social handle is looked up in
   the uploaded directory, which is the company's CRM export.
3. **Pseudonymous ID** (`EMAIL-…`, `PHONE-…`, `SOCIAL-…`) for unknown senders, so that one unknown person's own
   messages still group together.
4. Otherwise **anonymous**.

If the directory is uploaded *after* the complaints, earlier pseudonymous complaints are moved to the real
customer automatically, and deduplication and scoring re-run.

**Privacy by design.** Contact details are stored only as salted HMAC-SHA256 hashes, never as plain text. Names are
never stored. Demographics are only ever taken from customer records and **never guessed from complaint text**.

---

## 5. Preprocessing

For every complaint:

- **Cleaning**: Unicode normalisation, HTML and markup removal, quoted email replies and "Sent from my iPhone"
  signatures removed, repeated punctuation collapsed. Deliberately **not** lower-cased, and stop words and
  negations are kept, because "**not** delivered" must not become "delivered".
- **PII masking**: emails → `[EMAIL]`, phone numbers → `[PHONE]`, card numbers → `[CARD]` in the analysed text.
  The original text is preserved separately and shown on the Complaints page.
- **Reference-ID extraction**: order and transaction numbers are pulled out *before* masking and normalised:
  `ORD-396765`, "order number 396765" and "Order no. 396765" all become `ORD-396765`. This is how a typed or spoken
  order number matches the one in the CSV.

---

## 6. Finding recurring issues (semantic grouping)

### 6.1 Embeddings

Each complaint is turned into a 384-dimensional vector by the local MiniLM model. Complaints with similar
**meaning** get nearby vectors even when they share no words:

> "Payment failed but money was deducted" ≈ "Checkout error, amount debited from my account"

Vectors are stored in ChromaDB (cosine distance). Embedding is incremental: already-embedded complaints are skipped.
Measured quality: for a random complaint, **95.8 %** of its 10 nearest neighbours describe the same issue family.

### 6.2 Clustering into issues

- **HDBSCAN** groups dense regions of similar complaints into clusters. It decides the number of issues itself,
  and complaints that fit nowhere are left as "unclustered" instead of being forced into a wrong group.
- **Consolidation**: HDBSCAN often splits one problem into phrasing variants, so clusters whose centres are close
  (cosine ≥ 0.5) are merged. This raised clustering agreement (ARI) from 0.32 to 0.68.
- **Stable issue IDs**: when new data arrives and clustering re-runs, each new cluster is matched to the previous
  issue it most resembles (member overlap ≥ 50 % or centre similarity ≥ 0.8). Issues keep their ID, title and
  analyst edits across re-runs, and issues that disappear are marked *stale*.

### 6.3 Small-data mode

Density-based clustering needs volume. With only a few dozen complaints, for example a company uploading just its
emails, it tends to group by shared wording ("Hi team… Regards") instead of by problem. So **below 80 complaints**
(configurable), issues are formed from each complaint's category (Delivery, Billing, …), which is reliable at any
size. A category needs at least 2 complaints to become an issue. The dashboard says when this mode is active.
At 80+ complaints, semantic clustering takes over.

Example: uploading only the 17 sample emails produces 4 issues: *Delayed Package Deliveries*, *Billing Issues with
Invoices*, *Damaged Product Upon Arrival*, *Delayed Refunds Not Received*.

---

## 7. AI classification and labeling (GPT-4o-mini)

Only two of the seven pipeline stages call the LLM:

| Stage | What GPT does | Calls for the 616-complaint sample |
|---|---|---|
| **Classification** | For each complaint: category (11 fixed options), severity (CRITICAL / HIGH / MEDIUM / LOW), one-sentence summary, entities (order IDs, amounts, products), evidence quote | ~62 (10 complaints per call) |
| **Labeling** | For each issue: title, factual description, observed symptoms, and **separately** possible root-cause hypotheses (clearly marked as speculation) | ~20 (one per issue) |

**Guardrails**

- **Structured outputs**: GPT must answer in a fixed JSON schema, validated with Pydantic. Category and severity
  can only be values from the allowed list.
- **Grounding**: GPT may only cite complaint IDs that were in its input; invented IDs are dropped.
- **No counting or ranking**: the LLM never counts anything. All numbers come from deterministic code.
- **No demographics**: the prompts forbid inferring age, income, location or student status from text.
- **Provenance**: every classification and label records its source: `llm`, `fallback` or `manual`
  (an analyst edit, which is never overwritten).

**Speed and cost**

- Requests run **in parallel** (6 at a time). If the API fails, unsent work falls back to the rules and answers
  already received are kept.
- Every response is **cached** by the complaint's text, in a file that survives an application reset. Re-uploading
  the same data costs nothing and takes seconds.
- Measured: the full CSV with an empty database but a warm cache finishes in about **23 s**; uncached it takes
  about 2 minutes. Cost for the full sample is about **US$0.05** (~50k input + ~60k output tokens).

**Without an API key**, everything still works: keyword rules classify complaints, and issue titles are built from
the dominant category plus distinctive TF-IDF keywords. Root-cause hypotheses are then not generated, and the UI says so.

### 7.1 How complaints and issues are classified

**Complaint level: category and severity**

Each complaint gets exactly one **category** from a fixed list of 11:

> Payments · Billing · Refunds · Delivery · Product Quality · Account Access · Account Status ·
> Customer Support · Subscription · App Stability · Other

It also gets a **severity** level. GPT is given these definitions in its instructions:

| Severity | Definition given to GPT | Example |
|---|---|---|
| **CRITICAL** | Fraud, security, safety, legal threat, or severe financial harm | "Someone made unauthorised purchases on my card"; "the blender started smoking" |
| **HIGH** | Money lost or charged incorrectly, account inaccessible, repeated unresolved problem | "Charged twice for the same order"; "locked out of my account for a week" |
| **MEDIUM** | Service failure or delay with moderate impact | "Delivery is 3 days late"; "the app crashed once" |
| **LOW** | Minor inconvenience or feedback | "Would be nice to have dark mode" |

GPT must also quote the words that justify its severity (the *evidence*), so every judgement can be checked.

**Without GPT (keyword fallback)**, deterministic rules are used:

- **Category**: keyword hits are counted per category ("refund", "money back" → Refunds; "login", "OTP",
  "password" → Account Access; "crash", "freeze" → App Stability; …). The category with the most hits wins; no
  hits → Other.
- **Severity**: levels are checked from most to least severe, and the first level with a matching cue wins:
  - CRITICAL: "fraud", "unauthorised", "hacked", "smoke", "lawyer", …
  - HIGH: "charged twice", "deducted", "locked out", "still no refund", …
  - MEDIUM: "delay", "crash", "failed", "damaged", …
  - LOW: "minor", "suggestion", "would be nice", …

  With no cue words at all the default is MEDIUM. Fallback results get a lower confidence (0.35–0.7) and are
  marked `fallback`.

**Issue level: category**

- With GPT, the issue's category is chosen during labeling, from the same 11 options, after reading the issue's
  most representative complaints.
- Without GPT, it is the most common category among the issue's complaints.
- In small-data mode (§6.3) the issue *is* a category group.

**Issue level: severity (calculated, never chosen by GPT)**

An issue's severity is computed from the severities of all its complaints:

1. Each complaint's level is converted to points: CRITICAL 100, HIGH 75, MEDIUM 45, LOW 15.
2. Score = 0.6 × average + 0.4 × 90th-percentile value. This score is also the *Severity* component of the
   priority formula (§9).
3. The score is mapped back to a level: ≥ 87.5 → CRITICAL, ≥ 60 → HIGH, ≥ 30 → MEDIUM, otherwise LOW.

The 90th-percentile term means a real tail of severe complaints pushes an issue up, while a single stray one
cannot:

| Issue's complaints | Average | 90th percentile | Score | Issue severity |
|---|---|---|---|---|
| 7 MEDIUM + 3 CRITICAL | 61.5 | 100 | **76.9** | **HIGH** |
| 9 MEDIUM + 1 CRITICAL | 50.5 | 50.5 | **50.5** | **MEDIUM** |

With a dashboard filter active, issue severity is recomputed on the filtered complaints only. For example, an
issue can be HIGH for one country and MEDIUM for another.

---

## 8. Deduplication: not double-counting the same customer

Deduplication only ever compares complaints of the **same verified customer**. Two different customers are never
merged, however similar their text, and anonymous complaints are never merged. For each complaint, in time order,
against that customer's existing incidents:

| Rule | Condition | Result |
|---|---|---|
| **T1** | Shares an order / transaction ID with the incident | **Merge**, confidence 0.95 |
| (guard) | Both cite *different* order IDs of the same type | **Never merge** (two different orders) |
| **T2** | Within 7 days **and** similar meaning (cosine ≥ 0.55) **and** same issue or category | **Merge**, confidence 0.75–0.90 |
| **T3** | Only two of: within the window, similarity ≥ 0.45, same topic | **New incident + review queue** (a human decides) |

Text similarity alone never merges anything. Every link stores its rule, confidence and evidence, and the
Complaints page shows them in plain language.

**Worked example: Vik, order ORD-396765 (in the demo data)**

| Channel | Date | How we know it is Vik | Linked because |
|---|---|---|---|
| Email (in CSV export) | 16 Oct | customer ID | first report → opens the incident |
| Social media (in CSV export) | 16 Oct | customer ID | same order, 95 % |
| Email follow-up | 17 Oct | sender email → directory | same order, 95 % |
| Email follow-up (reworded) | 17 Oct | sender email → directory | same order, 95 % |
| Tweet | 18 Oct | **handle only** → directory | same order, 95 % |
| Chat | 19 Oct | **phone only** → directory; typed "order number 396765" | same order, 95 % |

Result: **6 reports, 4 channels → 1 incident, 1 affected customer.**

A second example without any order number: a customer chats "Shipping is taking forever, nothing has arrived" and two
days later "My shipment is stuck at the warehouse with no update". These are merged by rule T2: same customer, 2 days
apart, 60 % similar meaning, same category.

The design is deliberately **conservative**: precision 0.99 (a merge is almost always right) at recall 0.42. A
wrong merge would hide a real customer, while a missed merge only slightly over-counts and goes to the review queue.

---

## 9. Prioritisation: the transparent ranking formula

```
Priority = 0.35 × Impact + 0.25 × Severity + 0.20 × Recurrence + 0.10 × Growth + 0.10 × Business impact
```

Every component is scaled 0–100. The weights are configurable and validated to sum to 1.

| Component | How it is computed | Why |
|---|---|---|
| **Impact** (35 %) | 100 × ln(1 + u) / ln(1 + u_max), where *u* = **unique affected customers** | Customers, not reports, so duplicates cannot inflate it; log scale so one giant issue does not flatten the rest |
| **Severity** (25 %) | 0.6 × mean + 0.4 × 90th percentile of complaint severity (CRITICAL 100, HIGH 75, MEDIUM 45, LOW 15) | The p90 term stops a few critical cases from being averaged away |
| **Recurrence** (20 %) | 50 % share of customers hit more than once + 50 % share of weeks the issue was active | Persistent, repeat problems matter more than one-off spikes |
| **Growth** (10 %) | Sigmoid of last 7 days vs prior weekly average (50 = flat); neutral 50 with < 14 days of history | Rewards rising issues without overreacting to small data |
| **Business impact** (10 %) | 70 % category weight (Payments 90 … Other 30) + 30 % share of reports mentioning money or orders | Money and account problems hurt revenue and trust most |

**Transparency.** Every issue page shows each component's score, weight and contribution, the raw inputs (customers,
reports, incidents, duplicates collapsed, weeks active, …) and a plain-English explanation generated from a
template, not by the LLM. Example: *"Priority 75.2/100, driven mainly by unique customer impact and severity…"*

**Filters change the ranking.** With a filter active (say *country = India*), all scores are recomputed on that subset,
so a company sees *India's* top 10, not the global one.

---

## 10. Segments and filters: who is affected?

- **Five dimensions**: country, state, age group, student status, income bracket. They come from customer records
  (the CSV columns or the customer directory) and are **never inferred**. Missing values show as "Unknown".
- **Filter bar** on every page: any combination of the five dimensions, channel, date range and severity. Every
  chart, KPI and the top 10 update, and filters live in the URL, so a filtered view can be shared as a link.
- **Segments page**: affected customers, complaints and incidents per segment, the top issues per segment, and an
  **issue × segment heatmap** showing which problems hit which groups.
- **Privacy threshold**: segments with fewer than 3 customers are hidden, so no individual can be identified.
- **Counts vs rates**: if population sizes are uploaded (e.g. "India: 52,000 customers"), segments show affected
  customers per 1,000; otherwise the page explicitly says it shows counts, because a large segment naturally has
  more complaints.

---

## 11. The dashboard (what judges will see)

| Page | Shows |
|---|---|
| **Overview** | KPIs (reports, incidents, unique customers, duplicates collapsed, issues, critical/high issues, emerging issues), top 10 priorities, severity mix, volume over time, by channel / country / category |
| **Issues** | All issues, sortable by priority, customers, reports, severity, growth |
| **Issue detail** | Title, description, symptoms, **root-cause hypotheses** (marked speculative), the score breakdown and explanation, growth trend, representative complaints, segment breakdown; analysts can edit the title and status |
| **Segments** | Per-dimension breakdown and the issue × segment heatmap |
| **Complaints** | Every complaint with original text, severity, channel, issue, and a **"N reports · 1 incident"** badge. Click one to see the **Same incident** panel (every linked report, its channel and why it was linked) and semantically similar complaints |
| **Upload** | Tabs per channel and for the customer directory, live progress through the 7 pipeline stages, per-row errors, upload history with retry |

**Emerging issues.** Daily incident counts per issue are compared with a 14-day baseline. An issue is flagged when its
last 3 days are ≥ 2 standard deviations above normal with at least 3 recent incidents. With under 17 days of history,
nothing is flagged and the UI says the data is limited.

---

## 12. Demo script (about 5 minutes)

**Before going on stage**: do one full practice run in the exact order below (this fills the GPT cache), then reset
(stop the backend → `rm -rf backend/storage` → start the backend). Never delete `backend/cache/`.

1. **Upload → CSV** (`uploads/1_complaints_csv`). ~620 complaints are processed live through 7 stages in about 20 s.
   *"Three rows were rejected and we show exactly why."*
2. **Overview**: *"616 complaints became 18 issues. Here is the top 10 a company should fix first."*
3. **Click the #1 issue**: the score breakdown and plain-English explanation. *"Nothing here is a black box: every
   number comes from a published formula; GPT only writes the titles and descriptions."* Show the root-cause hypotheses.
4. **Upload → Customer directory**, then **Email, Chat, Social, Voice** (`uploads/3…7`).
   *"Voice calls are transcribed offline with Whisper."*
5. **Overview again**: reports went up much more than incidents. *"Customers repeating themselves are not counted twice."*
6. **Complaints → search `396765` → click the chat message**: the Same incident panel shows 6 reports on 4 channels.
   *"He tweeted with only his handle and chatted with only his phone number, typing the order number without its
   prefix. We still know it is the same customer and the same problem."*
7. **Filter bar → Country = India**: the top 10 re-ranks. **Segments → Age group**: the heatmap. *"Which problems
   hurt which customer groups."*

---

## 13. Likely judge questions

**"How do you know two complaints are the same problem?"**
Meaning vectors from a sentence-embedding model. Complaints that say the same thing in different words land close
together, and clustering groups them. For each issue we can show the similarity scores.

**"How do you avoid double-counting across channels?"**
We resolve the customer behind every message (ID, or email / phone / handle via the CRM directory), then merge
reports of the same customer that share an order number, or are close in time, meaning and topic. Rankings use
unique customers, not message counts.

**"What if there is no customer ID?"**
Unknown senders get a stable pseudonymous ID, so their own messages still group. Truly anonymous complaints are
counted individually and never merged: we prefer slight over-counting to wrongly merging two people.

**"Why not let GPT do everything?"**
LLMs are poor at counting and inconsistent at ranking, so all numbers come from deterministic code. GPT does what it
is good at, reading text, and its outputs are schema-validated, cached and labelled with their source.

**"What does it cost? Does it work without OpenAI?"**
About 5 cents for 616 complaints, and near zero on re-runs thanks to the cache. Without a key everything runs on
keyword rules (lower severity accuracy, no root-cause hypotheses). A local LLM through an OpenAI-compatible server
(e.g. Ollama) is a small configuration change, for companies that cannot send data out.

**"Is customer data safe?"**
PII is masked before analysis; contact details are stored only as salted hashes; names are not stored;
demographics are never inferred; small segments are hidden; the OpenAI key stays on the backend.

**"How would this connect to a real company?"**
Today channels are file exports and the customer directory is a CRM export. In production the same adapters would
read from IMAP, chatbot webhooks, social APIs, and a nightly CRM sync; nothing downstream changes, because
everything after ingestion uses one standard complaint format.

**"How accurate is it?"** See the next section, and be honest that the dataset is synthetic.

---

## 14. Evaluation (measured, not hand-entered)

`backend/scripts/evaluate.py` runs the real pipeline on the 616-complaint sample and compares it with a hidden
ground-truth file that the pipeline never reads. The numbers below are from the keyword-fallback mode (no GPT), so
they are reproducible by anyone.

| Area | Metric | Result |
|---|---|---|
| Semantic similarity | Precision@10 (neighbours from the same issue family) | **0.958** |
| Clustering | Issues found / true issue families | 18 / 10 (some families split by phrasing) |
| Clustering | Adjusted Rand Index / NMI | 0.684 / 0.827 |
| Clustering | Isolated one-off complaints left unclustered | 100 % |
| Deduplication | Pairwise precision / recall | **0.986** / 0.423 |
| Deduplication | True duplicate reports merged or sent for review | 89.9 % |
| Deduplication | Incidents wrongly spanning two customers | **0** |
| Ranking | NDCG@10 vs the ranking from perfect labels | **0.829** |
| Severity (keyword fallback) | Macro-F1 | 0.339 (GPT is used for this in the demo) |
| Multi-channel | Cross-channel re-reports merged into the original incident | **22 / 22** |
| Multi-channel | Same-channel repeat conversations merged | 2 / 2 |
| Multi-channel | New complaints wrongly merged | **0 / 39** |
| Multi-channel | Customer identity links correct | 100 % |
| Multi-channel | Spoken order numbers recovered from voice calls | 4 / 4 |
| Multi-channel | Auto-replies / reposts / empty chats rejected | 3 / 3 |

**Caveats to state openly**: the dataset is synthetic and template-based, so real data will be messier; the
ground-truth ranking uses our own formula, so NDCG measures how well the pipeline recovers it, not whether the formula
matches business judgement; the GPT-mode severity is not included in these numbers.

---

## 15. Limitations and next steps

| Today | Next |
|---|---|
| Channels are file uploads | Live connectors: IMAP, chatbot webhooks, social APIs, scheduled CRM sync |
| Uncertain duplicates go to a review queue with API access only | A review screen to confirm or reject suggested merges |
| No side-by-side comparison view yet | Compare page: top 10 for India vs US, students vs non-students, with rank changes |
| No login | Authentication and per-team access control |
| One pipeline at a time, in-process | Background worker queue for large volumes |
| Voice: no speaker separation (agent speech is in the transcript) | Diarisation to keep only the customer's voice |
| Clustering may split one problem by phrasing | "Merge issues" action for analysts; threshold tuning on real data |
| GPT-4o-mini via OpenAI | Optional local LLM for fully offline, on-premise use |

---

## 16. Running it

```bash
# Backend
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu && pip install -r requirements.txt
cp .env.example .env            # optional: add OPENAI_API_KEY
uvicorn app.main:app --port 8000

# Frontend (second terminal)
cd frontend && npm install && npm run dev      # http://localhost:5173
```

- **Demo files**: `uploads/` (numbered in upload order; see `uploads/README.md`).
- **Reset**: stop the backend, `rm -rf backend/storage`, start again. Keep `backend/cache/` (saved GPT answers).
- **Tests**: `cd backend && .venv/bin/pytest -q` (105 tests) · `cd frontend && npm test`.
- **Further reading**: `docs/ARCHITECTURE.md` (design detail), `docs/API.md` (endpoints), `docs/evaluation.md`.

---

## Glossary

| Term | Meaning |
|---|---|
| Embedding | A list of numbers representing a text's meaning; similar meanings give similar numbers |
| Cosine similarity | How close two embeddings are, from 0 (unrelated) to 1 (same meaning) |
| HDBSCAN | A clustering algorithm that finds dense groups and leaves outliers ungrouped |
| ARI / NMI | Scores (0–1) for how well found clusters match the true groups |
| Precision / recall | Of what we merged, how much was right / of what should be merged, how much we found |
| NDCG@10 | How close our top-10 ordering is to the ideal ordering (1 = identical) |
| PII | Personally identifiable information (emails, phone numbers, card numbers) |
| Incident | One customer's one problem, however many times it was reported |
| Issue | A problem shared by many customers |
