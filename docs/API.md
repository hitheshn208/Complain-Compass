# API reference

Base URL: `http://localhost:8000`. Interactive OpenAPI docs: `/docs`.

**Response format.** Successful responses are `{"data": ..., "meta": {...}}`. Errors are
`{"error": {"code": <status or slug>, "message": "...", "details": ...}}`.

**Common filters.** These apply to analytics, issue and complaint list endpoints. All are optional,
combine with AND, and take comma-separated lists:
`date_from`, `date_to` (ISO dates, inclusive), `country`, `state`, `age_group`, `student_status`,
`income_bracket`, `source` (EMAIL, CHAT, VOICE, SOCIAL_MEDIA, CSV, OTHER), `severity` (CRITICAL, HIGH, MEDIUM, LOW).

## Complaints

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/complaints/upload` | multipart `file` (.csv, ≤ 20 MB). Query `auto_process` (default true). **201** with batch stats + row errors · **400** bad file · **409** identical file already imported (`details.batch_id`) · **413** too large · **422** missing `complaint_text` column |
| GET | `/api/v1/complaints` | paginated (`page`, `page_size` ≤ 200) + filters + `issue_id`, `batch_id`, `search`. `meta`: page, page_size, total, pages |
| GET | `/api/v1/complaints/{id}` | complaint with original + normalized text, classification, issue, incident link with evidence, and the other reports in the same incident |
| GET | `/api/v1/complaints/{id}/similar?top_k=10` | nearest neighbours from ChromaDB (cosine). Similarity does not mean same customer or incident |
| GET | `/api/v1/complaints/review/duplicates` | possible duplicates flagged for human review, with confidence and evidence |

CSV columns: `complaint_text` (required), plus optional `customer_id, source, timestamp, country, state, age_group, student_status, income_bracket`.

## Other channels and the customer directory

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/ingest/channels` | channels with accepted extensions, endpoint, availability; for voice the transcriber status. `meta.directory` = directory stats |
| POST | `/api/v1/ingest/email` | multipart `files` (repeat the field): `.eml` and/or `.mbox`. One message = one complaint (subject + body, `Date` header). Sender resolved through the directory. Auto-replies / bounces rejected |
| POST | `/api/v1/ingest/chat` | `files`: `.json` / `.jsonl` conversations with a `messages` list (`role`, `text`, optional `timestamp`). Only customer turns are used. Optional `customer_id`, `customer_email`, `customer_phone`, demographics |
| POST | `/api/v1/ingest/social` | `files`: `.json` / `.jsonl` / `.csv` posts (`text`, `handle`, `created_at`, optional `platform`, `customer_id`, demographics). Reposts rejected |
| POST | `/api/v1/ingest/voice` | `files`: audio (`.wav .mp3 .m4a .ogg .flac .webm .mp4 .aac .opus`) + optional one manifest `.csv` (`filename, customer_id, phone, timestamp`, demographics). Transcribed during the request. **503** if transcription is unavailable |
| POST | `/api/v1/customers/directory` | multipart `file`: CSV `customer_id` + any of `email`, `phone`, `social_handle` (`;`-separated for several) + optional demographics. Stores salted hashes only. Relinks earlier pseudonymous complaints and, if any moved, re-runs the pipeline (`auto_recluster`, default true) |
| GET | `/api/v1/customers/directory` | number of customers with contacts, contacts per type |

All `/ingest/{channel}` uploads take `auto_process` (default true) and return the CSV upload fields plus
`channel`, `customer_links` (accepted items per `explicit` / `directory` / `pseudonymous` / `anonymous`) and
`items` (per email / conversation / post / call: label, accepted, identity link, detail). Item-level
problems are row errors whose `row_number` is the item number. **409** if the same set of files was already
imported, **404** unknown channel, **400** wrong file type, **413** too many / too large files.

## Batches and pipeline

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/batches` | recent batches |
| GET | `/api/v1/batches/{id}` | `channel` (CSV, EMAIL, CHAT, SOCIAL_MEDIA, VOICE), status (`UPLOADED/PROCESSING/PROCESSED/FAILED`), `stage`, `progress` 0–1, counts, `row_errors`, `error_message` |
| POST | `/api/v1/pipeline/process/{batch_id}` | **202**, (re)process a batch in the background · 404 · 409 if already running |
| POST | `/api/v1/pipeline/recluster` | **202**, re-embed anything pending, then recluster, relabel, dedup and rescore everything |
| GET | `/api/v1/pipeline/status` | running flag, stage, last summary/error, table counts, `llm_available` |

## Issues

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/issues` | issues ranked by priority (`page`, `page_size`, `include_stale`, filters). Each row: scores, counts, `is_emerging`, `z_score`, `explanation` |
| GET | `/api/v1/issues/top10` | top 10 by priority. `meta.weights`. Recomputed on the filtered subset when filters are set |
| GET | `/api/v1/issues/{id}` | full detail: description, symptoms, root-cause hypotheses, keywords, representative complaints with classification evidence, `breakdown` (components, weights, contributions, raw inputs), `trend`, `severity_distribution`, `by_source`, `demographics` per dimension, `emerging` |
| PATCH | `/api/v1/issues/{id}` | JSON body with any of `title, description, category, severity, status (OPEN/INVESTIGATING/RESOLVED)`. Text edits set `label_source=manual`, which automatic relabeling never overwrites |

## Analytics

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/analytics/overview` | total reports, incidents, duplicate reports, unique affected customers (verified + anonymous), issue clusters, noise, critical/high issues, emerging count, severity distribution, by source / country / category, top issues |
| GET | `/api/v1/analytics/trends?granularity=D\|W` | volume series (reports, incidents, unique customers, per severity) + emerging-issue detection (`method`, `params`, `limited_data`, per-issue z-scores) |
| GET | `/api/v1/analytics/segments?dimension=` | per-segment unique customers, complaints, incidents, top issues, rates if populations are known (`metric_type: count\|rate`), suppressed count, plus an issue × segment matrix |
| GET | `/api/v1/analytics/filters` | distinct values for filter dropdowns + date range |
| POST | `/api/v1/analytics/populations` | multipart CSV `dimension,value,population`. Replaces all denominators and enables rates |

## Meta

`GET /health` → `{"status": "ok", "llm_available": bool, "embedding_model": str, "vectors": int, "transcriber": {...}}`

## Example

```bash
curl -F file=@backend/data/sample_complaints.csv localhost:8000/api/v1/complaints/upload
curl -F file=@backend/data/channels/customer_directory.csv localhost:8000/api/v1/customers/directory
curl -F files=@backend/data/channels/email/followup_01.eml -F files=@backend/data/channels/email/inbox_export.mbox localhost:8000/api/v1/ingest/email
curl "localhost:8000/api/v1/issues/top10?country=India&date_from=2026-10-15"
curl -X PATCH localhost:8000/api/v1/issues/<id> -H 'Content-Type: application/json' -d '{"status":"INVESTIGATING"}'
```
