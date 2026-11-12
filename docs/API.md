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

## Batches and pipeline

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/batches` | recent batches |
| GET | `/api/v1/batches/{id}` | status (`UPLOADED/PROCESSING/PROCESSED/FAILED`), `stage`, `progress` 0–1, counts, `row_errors`, `error_message` |
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

`GET /health` → `{"status": "ok", "llm_available": bool, "embedding_model": str, "vectors": int}`

## Example

```bash
curl -F file=@backend/data/sample_complaints.csv localhost:8000/api/v1/complaints/upload
curl "localhost:8000/api/v1/issues/top10?country=India&date_from=2026-10-15"
curl -X PATCH localhost:8000/api/v1/issues/<id> -H 'Content-Type: application/json' -d '{"status":"INVESTIGATING"}'
```
