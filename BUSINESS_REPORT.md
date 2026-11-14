# Complaint Compass: Business Q&A with Evidence

**For the team presenting to company representatives.** Each answer below has three parts: what to say (short),
the detail behind it, and **evidence**, meaning the exact source file, function and line that implements it, so a
technical reviewer can verify every claim. Where the honest answer is "not yet", it says so and gives the plan.

Links are relative to the repository root and open in VS Code or GitHub. Line numbers match the working tree on
13 Nov 2026; if code moves, search for the function name instead.

---

## Executive summary

Complaint Compass turns complaints from **email, voice calls, chatbots, social media and CSV exports** into a
**ranked top 10 of problems to fix**, showing **which customer groups** each problem hurts. It counts **people,
not messages**, so one customer complaining on four channels is counted once, and every ranking comes from a
**published formula**, not from the AI.

| | Measured on the 616-complaint sample |
|---|---|
| Processing time | ~9 s without GPT · ~23 s with GPT answers cached · ~3–4 min with GPT and nothing cached |
| AI cost | ≈ US$0.05 for the whole sample (≈ US$0.08 per 1,000 complaints) · ≈ US$0 on re-runs (cache) |
| Duplicate detection | 22/22 cross-channel re-reports merged · 0/39 new complaints wrongly merged · 0 incidents ever mixing two customers |
| Ranking quality | NDCG@10 = 0.83 against the ranking that perfect labels would give |
| Privacy | Emails, phones, cards, SSNs masked before any AI call · contacts stored only as hashes · demographics never inferred |

---

## A. Business value

### Q1. What exactly do we get, and how is it different from reading a complaints inbox?

**Say:** "A ranked list of the 10 problems costing you the most customers, with who is affected, how severe it is,
whether it is growing, and possible root causes, refreshed every time new complaints arrive."

**Detail:** The system does three things a person reading an inbox cannot do at scale:
1. Groups **differently worded** complaints about the same problem into one issue.
2. Recognises the **same customer** repeating a complaint across channels and counts them once.
3. Ranks issues with a transparent formula on **unique affected customers**, severity, recurrence, growth and business impact.

| Evidence | Where |
|---|---|
| Pipeline: preprocess → embed → classify → cluster → label → deduplicate → score | [pipeline.py:34](backend/app/services/pipeline.py#L34) `STAGES`, [pipeline.py:142](backend/app/services/pipeline.py#L142) `_global_stages()` |
| Top-10 endpoint | [analytics_service.py:143](backend/app/services/analytics_service.py#L143) `top10()` · `GET /api/v1/issues/top10` in [issues.py:30](backend/app/api/issues.py#L30) |
| Root-cause hypotheses (marked as unverified) | [classifier.py:59](backend/app/inference/classifier.py#L59) `ClusterLabel.root_cause_hypotheses` |

### Q2. Why should we trust the ranking? Is the AI deciding what we fix?

**Say:** "No. The AI only reads and summarises text. Every number in the ranking comes from a fixed formula you can
read, change and audit, and every issue shows its full score breakdown."

**Detail:** `Priority = 0.35·Impact + 0.25·Severity + 0.20·Recurrence + 0.10·Growth + 0.10·Business`. Each issue stores
each component's score, weight and contribution, the raw inputs, and an explanation generated from a fixed template.

| Evidence | Where |
|---|---|
| Deterministic scoring (no AI call anywhere in it) | [prioritization.py:131](backend/app/analytics/prioritization.py#L131) `compute_issue_scores()` |
| Stored breakdown per issue | [prioritization.py:196](backend/app/analytics/prioritization.py#L196) `breakdown = {…}` |
| Template-based explanation (not LLM) | [prioritization.py:250](backend/app/analytics/prioritization.py#L250) `explain()` |
| AI is never used for counting | [classifier.py:7](backend/app/inference/classifier.py#L7) module docstring; the AI outputs only labels via [classifier.py:264](backend/app/inference/classifier.py#L264) `_classify_chunk()` |

### Q3. Our priorities are different. Can we change how issues are ranked?

**Say:** "Yes, from the dashboard itself. The **Priority settings** page has a slider for each of the five
factors and for the business value of every complaint category. Press Save and every issue is re-ranked instantly
(no AI involved); Reset brings back the defaults."

**Detail:** a bank might push Severity up, a fast-growing startup Growth. On the sample data, moving Severity from
25 % to 60 % lifts "Payments: card declined, unauthorized charges" from #3 to #1. Saving and re-scoring all 18 issues
took 0.09 s. Weights must add up to 100 %; the page has a "Balance to 100 %" button.

| Evidence | Where |
|---|---|
| Settings page (sidebar → Priority settings) | [PrioritySettingsPage.tsx](frontend/src/pages/PrioritySettingsPage.tsx) |
| Save / read / reset endpoints (`PUT`, `GET`, `DELETE /api/v1/settings/priority`) | [settings.py:30](backend/app/api/settings.py#L30) `update_priority()`, [settings.py:25](backend/app/api/settings.py#L25), [settings.py:42](backend/app/api/settings.py#L42) |
| Validation (weights 0–1 and summing to 1, category values 0–100) and storage | [priority_settings.py:52](backend/app/services/priority_settings.py#L52) `update()` |
| Every score uses the saved settings | [pipeline.py:323](backend/app/services/pipeline.py#L323) in `_score()`; filtered views: [analytics_service.py:66](backend/app/services/analytics_service.py#L66) `weights()` |
| Defaults: environment variables (`WEIGHT_IMPACT`, …), validated at startup | [config.py:73](backend/app/core/config.py#L73), [config.py:91](backend/app/core/config.py#L91) `_check_weights()` |
| Default business value per category (Payments 90 … Other 30) | [prioritization.py:40](backend/app/analytics/prioritization.py#L40) `BUSINESS_CATEGORY_WEIGHTS` |
| Dedup, clustering and privacy thresholds (configuration) | [config.py:57](backend/app/core/config.py#L57) (clustering), [config.py:67](backend/app/core/config.py#L67) (dedup), [config.py:86](backend/app/core/config.py#L86) (segment privacy) |

**Gap:** the settings are company-wide; there are no per-user profiles or change history yet.

### Q4. Can we see the top 10 for a specific market or customer group?

**Say:** "Yes. Filter by country, age group, income bracket, channel, dates or severity, and the whole ranking is
recomputed for that group with the same formula."

**Detail:** the backend also supports state and student status; they are hidden in the dashboard by choice and can be
shown again by adding them to one list ([dimensions.ts:6](frontend/src/lib/dimensions.ts#L6) `UI_DIMENSIONS`).

| Evidence | Where |
|---|---|
| Scores recomputed on the filtered subset | [analytics_service.py:50](backend/app/services/analytics_service.py#L50) `_scores()` (line 55 calls `compute_issue_scores` on the filtered frame) |
| Filters → SQL | [repository.py:46](backend/app/database/repository.py#L46) `ComplaintFilters.to_sql()` |
| Per-group breakdown and issue × group heatmap | [segmentation.py:21](backend/app/analytics/segmentation.py#L21) `segment_breakdown()`, [segmentation.py:84](backend/app/analytics/segmentation.py#L84) `issue_segment_matrix()` |
| Early warning for rising issues | [trends.py:69](backend/app/analytics/trends.py#L69) `detect_emerging_issues()` (last 3 days vs 14-day baseline, z ≥ 2) |

---

## B. Integration and onboarding

### Q5. What data do we need to provide, and in what format?

**Say:** "Exports you already have: a CSV of tickets, email exports (.eml / .mbox), chatbot transcripts (JSON),
social media exports, and call recordings with a small manifest. Only the complaint text is mandatory."

| Evidence | Where |
|---|---|
| One standard record every channel is converted into | [complaint.py:22](backend/app/schemas/complaint.py#L22) `StandardComplaint` |
| Adapter interfaces (new channels plug in here) | [base.py:28](backend/app/ingestion/base.py#L28) `IngestionAdapter`, [base.py:50](backend/app/ingestion/base.py#L50) `MultiFileAdapter` |
| Channel adapters | `backend/app/ingestion/` `csv_ingestion.py`, `email_ingestion.py`, `chat_ingestion.py`, `social_ingestion.py`, `voice_ingestion.py` |
| Same validation rules for every channel | [base.py:79](backend/app/ingestion/base.py#L79) `ResultBuilder` |

### Q6. Can it connect to our CRM / customer database?

**Say:** "Today it reads a CRM export (customer ID, contact details, demographics). In production that becomes a
scheduled sync; nothing else in the system changes, because the rest only sees the standard format."

**Detail:** Contacts in the export (email, phone, social handle) let the system recognise customers who write
without their customer ID. If the export arrives after the complaints, earlier complaints are re-linked automatically.

| Evidence | Where |
|---|---|
| Directory import | [directory.py:36](backend/app/ingestion/directory.py#L36) `parse_directory()`, [ingestion_service.py:111](backend/app/services/ingestion_service.py#L111) `import_directory()` |
| Re-link earlier complaints to the real customer | [repository.py:227](backend/app/database/repository.py#L227) `relink_customers()` |
| Missing values never overwrite stored ones | [repository.py:180](backend/app/database/repository.py#L180) `_upsert_customers()` (`COALESCE`, lines 186–190) |

**Gap / roadmap:** live connectors (IMAP mailbox, chatbot webhook, social APIs, CRM sync job) are not built yet.
The adapter interface above is where they plug in.

### Q7. What does it take to deploy?

**Say:** "A single machine: Python and Node, no cloud services required, no Docker. The OpenAI key is optional."

| Evidence | Where |
|---|---|
| Local storage paths (SQLite + ChromaDB on disk) | [config.py:19](backend/app/core/config.py#L19)–20 `db_path`, `chroma_dir` |
| Optional AI | [config.py:29](backend/app/core/config.py#L29) `openai_api_key`, [config.py:108](backend/app/core/config.py#L108) `llm_available` |
| Setup steps | [README.md](README.md) "Quick start" |

---

## C. Scaling and performance

### Q8. How much volume can it handle today?

**Say:** "Measured: 616 complaints in about 9 seconds without GPT, about 23 seconds with cached GPT answers, and
3–4 minutes when every complaint needs a fresh GPT call. Eight voice calls transcribe in about 10 seconds on a laptop CPU."

**Detail, what makes it fast:**
- **Incremental embedding:** only complaints without a vector are embedded.
- **Incremental classification:** only unclassified complaints go to GPT.
- **Parallel AI calls:** 10 complaints per request, 6 requests at a time.
- **Persistent AI cache:** a complaint text seen before is never paid for again.

| Evidence | Where |
|---|---|
| Incremental embedding | [embeddings.py:93](backend/app/inference/embeddings.py#L93)–94 `generate_embeddings()` skips existing IDs; [pipeline.py:169](backend/app/services/pipeline.py#L169) `_embed()` |
| Only unclassified complaints sent to GPT | [repository.py:539](backend/app/database/repository.py#L539) `unclassified_complaints()`, [pipeline.py:234](backend/app/services/pipeline.py#L234) `_classify()` |
| Batching and parallelism (configurable) | [config.py:34](backend/app/core/config.py#L34)–35 `llm_batch_size`, `llm_concurrency`; [classifier.py:203](backend/app/inference/classifier.py#L203) `_run_parallel()` |
| Cache read before any call | [classifier.py:240](backend/app/inference/classifier.py#L240) in `classify_complaints()`; store: [llm_cache.py:30](backend/app/inference/llm_cache.py#L30) `LLMCache` |
| Timing measurements | [evaluate.py:183](backend/scripts/evaluate.py#L183) `run()` reports `runtime_seconds` and `seconds_per_channel` |

### Q9. What happens at 10× or 100× our volume?

**Say (honest):** "The MVP is built for one machine and one processing job at a time. The design separates the
stages cleanly, so scaling means swapping infrastructure, not rewriting logic."

**Current limits, with evidence:**

| Limit today | Evidence | Scale-up path |
|---|---|---|
| One pipeline run at a time (process-wide lock) | [pipeline.py:53](backend/app/services/pipeline.py#L53) `_lock`, taken at [pipeline.py:82](backend/app/services/pipeline.py#L82) | Job queue (e.g. Celery/RQ) with separate workers |
| Background work runs inside the web process | [complaints.py:49](backend/app/api/complaints.py#L49) `background.add_task(run_batch_pipeline…)` | Same job queue; the API only enqueues |
| Clustering, dedup and scoring re-run over **all** complaints on each upload | [pipeline.py:176](backend/app/services/pipeline.py#L176) `_cluster()` reads every embedded complaint | Assign new complaints to the nearest existing issue; full re-cluster nightly |
| SQLite (single writer, WAL mode) | [sqlite.py:200](backend/app/database/sqlite.py#L200)–203 | PostgreSQL; all SQL is already isolated in [repository.py](backend/app/database/repository.py) |
| Local ChromaDB vector store | [vector_store.py](backend/app/inference/vector_store.py) | Managed vector DB or pgvector |
| Upload size caps: 20 MB per file, 200 MB per multi-file upload, 500 files | [config.py:41](backend/app/core/config.py#L41)–43, enforced in [channels.py:49](backend/app/api/channels.py#L49)–60 | Stream from connectors instead of uploads |
| GPT classifications capped at 2,000 per run (cost guard) | [config.py:33](backend/app/core/config.py#L33), applied at [pipeline.py:241](backend/app/services/pipeline.py#L241) | Raise the cap; the rest are picked up on the next run |

**We have not load-tested beyond ~700 complaints**, so do not quote numbers above that.

### Q10. What will the AI cost at our volume?

**Say:** "About 8 cents per 1,000 complaints with GPT-4o-mini, and each complaint is paid for once: repeats and
re-runs come from the cache. One million complaints would cost on the order of US$80. Without a key it costs nothing."

**Detail:** ~1,100 input + ~1,800 output tokens per call of 10 complaints, plus one labeling call per issue.
These are estimates from measured token sizes at GPT-4o-mini list prices; check OpenAI's current pricing.

| Evidence | Where |
|---|---|
| Cost guard per run | [config.py:33](backend/app/core/config.py#L33) `llm_max_complaint_classifications` |
| Cache key = task + model + text (re-runs are free) | [classifier.py:181](backend/app/inference/classifier.py#L181) `_key()` |
| Issue labels re-generated only when the issue's representative complaints change | [pipeline.py:250](backend/app/services/pipeline.py#L250) `_label()` (signature check) |
| Cache survives an application reset | [config.py:38](backend/app/core/config.py#L38) `llm_cache_path` (outside `storage/`) |

### Q11. Does a big upload block the dashboard?

**Say:** "No. The upload returns immediately and processing runs in the background with a live progress bar.
The dashboard stays usable and updates when the run finishes."

| Evidence | Where |
|---|---|
| Background job | [complaints.py:21](backend/app/api/complaints.py#L21) `run_batch_pipeline()`, queued at [complaints.py:49](backend/app/api/complaints.py#L49) |
| Progress reported per stage | [pipeline.py:78](backend/app/services/pipeline.py#L78) `process_batch()` (`progress()` updates the batch row) |
| Status endpoint polled by the UI | [batches.py:58](backend/app/api/batches.py#L58) `pipeline_status()` |

---

## D. Privacy and security

### Q12. Does our customer data go to OpenAI? Exactly what?

**Say:** "Only the complaint text, after emails, phone numbers, card numbers and SSNs are replaced by placeholders.
OpenAI never receives customer IDs, names from your records, contact details, demographics, the original text or audio."

**What is sent:**
- **Classification:** the masked text of 10 complaints per request.
- **Labeling:** about 5 masked sample texts per issue, plus keywords, the issue size and random internal IDs.

| Evidence | Where |
|---|---|
| The only chat-model call | [classifier.py:188](backend/app/inference/classifier.py#L188) `client.chat.completions.parse(…)` in `_parse()` |
| Classification prompt built from masked text only | [classifier.py:275](backend/app/inference/classifier.py#L275) in `_classify_chunk()`; input query selects only `complaint_id, normalized_text`: [repository.py:539](backend/app/database/repository.py#L539) |
| Labeling prompt built from masked text only | [pipeline.py:275](backend/app/services/pipeline.py#L275) builds samples from `normalized_text`; [classifier.py:347](backend/app/inference/classifier.py#L347) in `_label_call()` |
| Masking runs before anything is stored as `normalized_text` | [pipeline.py:156](backend/app/services/pipeline.py#L156) `_preprocess()` → [cleaner.py:46](backend/app/preprocessing/cleaner.py#L46) → [pii_detection.py:56](backend/app/preprocessing/pii_detection.py#L56) `mask_pii()` |
| What is masked | [pii_detection.py:18](backend/app/preprocessing/pii_detection.py#L18) `_PII_PATTERNS` (EMAIL, CARD, SSN, PHONE) |

**Gap (say it before they find it):** names and other free-text personal details written *inside* the message,
for example "Thanks, Vik", are not masked; the masking matches patterns, not names. Planned fix: strip greetings
and sign-offs before sending, and/or run a local model (Q13).

### Q13. Can it run without sending any data outside our network?

**Say:** "Yes. Turn the AI off and everything runs locally on keyword rules, or point it at a model hosted inside
your network. Speech-to-text and the semantic model already run locally."

| Evidence | Where |
|---|---|
| AI switch: no key or `LLM_ENABLED=false` → no OpenAI client is created | [config.py:31](backend/app/core/config.py#L31), [classifier.py:172](backend/app/inference/classifier.py#L172)–175 |
| Keyword fallback classifier | [classifier.py:94](backend/app/inference/classifier.py#L94) `fallback_classify()`, [classifier.py:124](backend/app/inference/classifier.py#L124) `fallback_cluster_label()` |
| With the AI off, cached AI answers are not used either | [classifier.py:240](backend/app/inference/classifier.py#L240) (`if self.available`) |
| Local speech-to-text (default) | [config.py:46](backend/app/core/config.py#L46) `transcriber = "local"`, [transcription.py:69](backend/app/ingestion/transcription.py#L69) `LocalWhisperTranscriber`, chosen in [transcription.py:137](backend/app/ingestion/transcription.py#L137) `build_transcriber()` |
| Local embedding model | [config.py:25](backend/app/core/config.py#L25), [embeddings.py:23](backend/app/inference/embeddings.py#L23) `SentenceTransformerEncoder` |

**Trade-off:** keyword rules are less accurate for severity (Q19). **Roadmap:** a self-hosted model through an
OpenAI-compatible server is a small configuration change (base URL + model name).

### Q14. How is personal data stored?

**Say:** "Emails, phone numbers and social handles from your customer list are stored only as salted one-way
hashes, used for matching. Names are not stored at all. Demographics are stored only as the brackets you provide."

| Evidence | Where |
|---|---|
| HMAC-SHA256 with a secret salt | [identity.py:58](backend/app/ingestion/identity.py#L58) `contact_hash()` |
| Salt is configuration (set your own in production) | [config.py:54](backend/app/core/config.py#L54) `identity_salt` |
| Contacts normalised before hashing (case, last 10 phone digits, handle without @) | [identity.py:35](backend/app/ingestion/identity.py#L35) `normalize_contact()` |
| Customer table has no name or contact columns | [sqlite.py:11](backend/app/database/sqlite.py#L11)–19 `CREATE TABLE customers` |
| Original complaint text is kept locally for the analyst view; only masked text is used for analysis | `complaints.original_text` / `normalized_text`, written in [pipeline.py:156](backend/app/services/pipeline.py#L156) `_preprocess()` |

### Q15. Could someone identify an individual customer from the dashboard?

**Say:** "Segment views never show customer IDs, and any segment with fewer than 3 customers is hidden."

| Evidence | Where |
|---|---|
| Small segments suppressed | [segmentation.py:44](backend/app/analytics/segmentation.py#L44)–45 in `segment_breakdown()`; threshold [config.py:86](backend/app/core/config.py#L86) |
| No customer identifiers returned by segment analytics | [segmentation.py:7](backend/app/analytics/segmentation.py#L7)–8 (design rule), rows built at lines 46–70 contain only counts |

**Gap:** the Complaints page shows individual complaints to whoever can open the dashboard; that needs access
control (Q17).

### Q16. Does the system guess customers' age, income or location from what they write? Could it discriminate?

**Say:** "No. Demographics come only from your customer records; missing values show as 'Unknown'. The AI is
explicitly forbidden from inferring them, and it never ranks anything."

| Evidence | Where |
|---|---|
| Prompt rule for classification | [classifier.py:272](backend/app/inference/classifier.py#L272) "Never infer or mention demographics…" |
| Prompt rule for labeling | [classifier.py:342](backend/app/inference/classifier.py#L342) "Never mention customer demographics." |
| Missing values become "Unknown", never inferred | [segmentation.py:38](backend/app/analytics/segmentation.py#L38) |
| Demographics only from explicit columns | [directory.py:36](backend/app/ingestion/directory.py#L36) `parse_directory()` (explicit demographic columns), [sqlite.py:11](backend/app/database/sqlite.py#L11) |

### Q17. Who can access it? Is there login and role-based access?

**Say (honest):** "Not in the MVP: it is designed to run inside your network. Authentication and roles are the first
production item."

| Evidence | Where |
|---|---|
| No authentication dependency on any route | `backend/app/api/*.py` (no auth dependency is declared on any router) |
| Browser access limited to configured origins (CORS) | [main.py:27](backend/app/main.py#L27), origins in [config.py:88](backend/app/core/config.py#L88) |
| The OpenAI key exists only in the backend, never sent to the browser | [config.py:29](backend/app/core/config.py#L29); the frontend contains no API-key handling (`frontend/src` only mentions the key in a help note) |

### Q18. Can we delete a customer's data on request (GDPR "right to erasure")? How long is data kept?

**Say (honest):** "Data stays until you delete it; there is no automatic retention policy and no per-customer
delete yet. Today you can wipe everything. Per-customer erasure is a straightforward addition, because all data
access goes through one repository module."

| Evidence | Where |
|---|---|
| No delete endpoints exist | `backend/app/api/*.py` (no `@router.delete`) |
| Full wipe = delete the storage folder (documented reset) | [README.md](README.md) "Resetting the application", storage paths [config.py:19](backend/app/core/config.py#L19)–20 |
| Cascading deletes are enforced by the schema (helps a future per-customer delete) | `ON DELETE CASCADE` in [sqlite.py](backend/app/database/sqlite.py), `PRAGMA foreign_keys = ON` at [sqlite.py:202](backend/app/database/sqlite.py#L202) |
| All SQL in one module | [repository.py](backend/app/database/repository.py) |

### Q19. Is it secure against bad input (malicious files, SQL injection)?

**Say:** "All database queries are parameterised, uploads are size- and type-checked, and every row is validated."

| Evidence | Where |
|---|---|
| Parameterised SQL (example: search uses `?` placeholders) | [repository.py:77](backend/app/database/repository.py#L77), [repository.py:101](backend/app/database/repository.py#L101) `find_batch_by_hash()` |
| File type checks | [base.py:58](backend/app/ingestion/base.py#L58) `check_extensions()` |
| Size limits → HTTP 413 | [channels.py:49](backend/app/api/channels.py#L49)–60, [complaints.py:36](backend/app/api/complaints.py#L36) |
| Per-row validation | [base.py:79](backend/app/ingestion/base.py#L79) `ResultBuilder`, `backend/app/ingestion/validator.py` |

---

## E. Accuracy and trust

### Q20. How accurate is it, and how do you know?

**Say:** "We measure it with a repeatable evaluation script against a hidden answer key. The pipeline never sees
the key. The data is synthetic, so treat these as indicative."

| Result | Value |
|---|---|
| Similar-complaint precision@10 | 0.958 |
| Clustering ARI / NMI (18 issues found for 10 true families) | 0.684 / 0.827 |
| Duplicate merges that were correct (precision) | 0.986 |
| Cross-channel re-reports merged | 22/22 |
| New complaints wrongly merged | 0/39 |
| Ranking NDCG@10 | 0.829 |
| Severity macro-F1: keyword rules / GPT-4o-mini | 0.34 / 0.51 |

| Evidence | Where |
|---|---|
| Evaluation script | [evaluate.py:183](backend/scripts/evaluate.py#L183) `run()` (`--channels` adds multi-channel checks) |
| Answer keys (never read by the pipeline) | `backend/data/ground_truth.csv`, `backend/data/channels/channel_ground_truth.csv` |
| Published results | [docs/evaluation.md](docs/evaluation.md) |

Note: the GPT severity figure (0.51) was measured with a one-off comparison using cached GPT answers; it is not yet
part of `evaluate.py`. GPT tends to rate MEDIUM complaints as HIGH.

### Q21. What if the AI makes something up?

**Say:** "Its output is forced into a fixed format and checked, it can only cite complaints we gave it, its root
causes are explicitly labelled as hypotheses, and it never produces a number we rank on."

| Evidence | Where |
|---|---|
| Structured output, temperature 0 | [classifier.py:191](backend/app/inference/classifier.py#L191)–192 (`response_format=schema`, `temperature=0`) |
| Allowed values only (categories, severities) | [classifier.py:40](backend/app/inference/classifier.py#L40) `ComplaintClassification`; category coerced at [classifier.py:358](backend/app/inference/classifier.py#L358) |
| Invented complaint IDs dropped | [classifier.py:362](backend/app/inference/classifier.py#L362) in `_label_call()`, [classifier.py:300](backend/app/inference/classifier.py#L300) `_bind_label()` |
| Root causes labelled as unverified | [classifier.py:59](backend/app/inference/classifier.py#L59), prompt at [classifier.py:342](backend/app/inference/classifier.py#L342) |
| Every result records its source (`llm` / `fallback` / `manual`) | `source` field set in [classifier.py:264](backend/app/inference/classifier.py#L264) `_classify_chunk()` and [classifier.py:94](backend/app/inference/classifier.py#L94) `fallback_classify()` |

### Q22. Could it wrongly merge two different customers, or double-count one?

**Say:** "Merging only happens within one verified customer, so two people are never merged; across the evaluation,
zero incidents mixed two customers. Text similarity alone never merges anything; uncertain cases go to a review queue."

| Evidence | Where |
|---|---|
| Grouping by verified customer; anonymous complaints never merged | [identity_resolution.py:21](backend/app/deduplication/identity_resolution.py#L21) `group_by_customer()`; [incident_matching.py:187](backend/app/deduplication/incident_matching.py#L187)–188 |
| Rule T1: shared order ID → merge (0.95) | [incident_matching.py:133](backend/app/deduplication/incident_matching.py#L133)–134 in `evaluate_match()` |
| Different order IDs → never merge | [incident_matching.py:95](backend/app/deduplication/incident_matching.py#L95) `conflicting_references()`, applied at line 135 |
| Rule T2: time window + meaning + topic | [incident_matching.py:147](backend/app/deduplication/incident_matching.py#L147)–149 |
| Rule T3: partial evidence → review queue | [incident_matching.py:154](backend/app/deduplication/incident_matching.py#L154); queue API [complaints.py:69](backend/app/api/complaints.py#L69) `GET /complaints/review/duplicates` |
| Thresholds configurable | [incident_matching.py:40](backend/app/deduplication/incident_matching.py#L40) `DedupConfig`, [config.py:67](backend/app/core/config.py#L67) |

**Trade-off:** being conservative means some true duplicates are missed (recall 0.42) and slightly over-counted;
we chose that over hiding a real customer. **Gap:** the review queue has an API but no screen yet.

### Q23. Can our analysts correct the system?

**Say:** "Yes. Analysts can rename issues and change their status, and their edits are never overwritten by later
AI runs."

| Evidence | Where |
|---|---|
| Edit endpoint | [issues.py:43](backend/app/api/issues.py#L43) `patch_issue()` |
| Manual labels preserved on re-runs | [pipeline.py:269](backend/app/services/pipeline.py#L269)–267 in `_label()` |
| Issue identity stable across re-runs | [clustering.py:193](backend/app/inference/clustering.py#L193) `resolve_stable_ids()` |

---

## F. Reliability and operations

### Q24. What happens if OpenAI is down or rate-limits us?

**Say:** "Processing continues. Answers already received are kept, the rest fall back to keyword rules and are
marked as such, and they are upgraded to AI answers on the next run."

| Evidence | Where |
|---|---|
| Any API error → `LLMUnavailable`, logged | [classifier.py:194](backend/app/inference/classifier.py#L194)–197 in `_parse()` |
| First request sent alone as a probe; after a failure, pending requests are skipped | [classifier.py:203](backend/app/inference/classifier.py#L203) `_run_parallel()`, stop flag at lines 214–222 |
| Fallback for anything unanswered | [classifier.py:232](backend/app/inference/classifier.py#L232) `classify_complaints()` (final loop) |
| Fallback answers retried with the AI on the next run | [repository.py:539](backend/app/database/repository.py#L539) `unclassified_complaints()` includes `source = 'fallback'` |
| Client retries + timeout | [classifier.py:175](backend/app/inference/classifier.py#L175) (`max_retries=2`), [config.py:32](backend/app/core/config.py#L32) |

### Q25. What if processing crashes or the server restarts mid-run?

**Say:** "The batch is marked failed with the reason, nothing is half-counted, and it can be re-run with one click;
cached AI answers are reused."

| Evidence | Where |
|---|---|
| Errors recorded on the batch | [pipeline.py:104](backend/app/services/pipeline.py#L104)–106 in `process_batch()` |
| Interrupted runs detected at startup | [repository.py:282](backend/app/database/repository.py#L282) `fail_interrupted_batches()`, called in [lifespan.py:68](backend/app/core/lifespan.py#L68) |
| Re-run endpoint and Retry button | [batches.py:32](backend/app/api/batches.py#L32) `process_batch()`; [UploadPage.tsx:197](frontend/src/pages/UploadPage.tsx#L197) `retry()` |

### Q26. What if the same file is uploaded twice, or the wrong file is uploaded?

**Say:** "Exact re-uploads are refused, so nothing is counted twice. A file with no valid rows is rejected with a
hint, and valid rows are kept even when others in the same file are bad."

| Evidence | Where |
|---|---|
| Duplicate file → HTTP 409 | [ingestion_service.py:66](backend/app/services/ingestion_service.py#L66) `_check_duplicate()` |
| "No valid complaints… check the right tab" | [ingestion_service.py:127](backend/app/services/ingestion_service.py#L127) `no_valid_rows_message()` |
| Partial acceptance with per-row errors | [ingestion_service.py:77](backend/app/services/ingestion_service.py#L77) `_persist()` |

### Q27. How do we monitor it?

**Say (honest):** "Today: a status endpoint, per-batch status and errors in the UI, and server logs. Production
monitoring (metrics, alerts) is on the roadmap."

| Evidence | Where |
|---|---|
| Pipeline status | [batches.py:58](backend/app/api/batches.py#L58) `pipeline_status()` |
| Per-batch status, stage, progress, error | [repository.py](backend/app/database/repository.py) `update_batch()`; shown on the Upload page |

---

## G. Roadmap at a glance

| Area | Today (MVP) | Next |
|---|---|---|
| Data sources | File exports + CRM export | Live connectors: IMAP, chatbot webhooks, social APIs, scheduled CRM sync |
| Scale | One machine, one job at a time, SQLite | Job queue + workers, PostgreSQL/pgvector, incremental issue assignment |
| Privacy | Pattern-based masking, hashed contacts | Strip names and sign-offs before AI; optional self-hosted model; per-customer erasure; retention policy |
| Security | Runs inside your network, no login | Authentication, roles, audit log |
| Workflow | Review queue via API | Review screen for suggested merges; "merge issues" action |
| Insight | Filters and segment heatmap | Side-by-side comparison of segments (e.g. India vs US top 10) |
