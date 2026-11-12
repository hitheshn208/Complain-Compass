# Architecture

## Pipeline

```
CSV upload ──► IngestionAdapter (CSV) ──► StandardComplaint objects ──► SQLite (batch, customers, complaints, row errors)
                                                                              │  BackgroundTasks
                                                                              ▼
 preprocess ─► embed ─► cluster ─► classify ─► label ─► deduplicate ─► score      (PipelineOrchestrator)
   │            │         │          │           │          │            │
 cleaner      MiniLM    HDBSCAN    gpt-4o-mini  gpt-4o-mini  identity+    weighted
 PII mask     ChromaDB  +consol.   / fallback   / fallback   time+refs+   formula
 ref IDs      (cosine)  stable IDs                           semantics
                                                                              ▼
                                                     AnalyticsService ─► REST API ─► React dashboard
```

**Separation of concerns.** Ingestion adapters produce source-agnostic `StandardComplaint`
objects (`app/schemas/complaint.py`), and nothing downstream sees the raw format. Each
stage lives in its own package. `services/pipeline.py` orchestrates them, and the
routers in `api/` only parse requests and call services. All SQL is in `database/repository.py`
and uses parameterized queries.

**Singletons.** The embedding model, Chroma client, SQLite handle and OpenAI client are
created once in `core/lifespan.py`, held in a `Container`, and injected through FastAPI
`Depends(get_container)`.

## Storage

| Store | Contents |
|---|---|
| SQLite (`storage/complaint_compass.db`, WAL, `PRAGMA foreign_keys=ON`) | customers, ingestion_batches, batch_row_errors, complaints, issues, cluster_assignments, complaint_classifications, incidents, complaint_incident_links, duplicate_review_candidates, issue_metrics, segment_populations, llm_cache |
| ChromaDB (`storage/chroma`, collection `complaints`, `hnsw:space=cosine`) | one 384-d L2-normalized vector per complaint. **Vector ID = `complaint_id` (UUID)**. Metadata: customer_id, source, timestamp, batch_id |

`complaints.embedded` mirrors whether a vector exists. Embedding is incremental: IDs
already in Chroma are skipped.

## Three distinct concepts

| Concept | Meaning | Produced by |
|---|---|---|
| **Complaint** | one report as received (original text preserved verbatim) | ingestion |
| **Incident** | one underlying problem for one customer, possibly reported several times / channels | `deduplication/` |
| **Issue** | a recurring problem shared by many customers | `inference/clustering.py` |

## Issue discovery

1. HDBSCAN (`sklearn.cluster.HDBSCAN`, Euclidean on unit vectors, equivalent to cosine ordering) with
   configurable `min_cluster_size` / `min_samples`. Label `-1` is noise and is never forced into a cluster.
2. **Consolidation:** HDBSCAN often splits one issue into dense phrasing variants. Clusters
   whose centroids have cosine ≥ `CLUSTER_MERGE_THRESHOLD` (0.5) are merged with average-linkage
   agglomerative clustering. On the sample this raised ARI from 0.32 to 0.68. Noise stays noise.
3. **Stable IDs:** temporary HDBSCAN labels are never stored. Each new cluster is matched
   one-to-one (greedy) to existing issues by member Jaccard ≥ 0.5 or centroid cosine ≥ 0.8. Matched
   clusters keep their `issue_id` / `cluster_id` (`CL-xxxxxxxxxx`), unmatched clusters get new ones, and
   issues that disappear become `STALE`.
4. **Representatives:** the members closest to the centroid, skipping near-duplicates (cosine > 0.92).

## Classification and labeling

`inference/classifier.py` uses OpenAI structured outputs (`chat.completions.parse` with
Pydantic schemas). Complaints go in batches of 20. Clusters are labeled from representative samples,
with symptoms (facts) and root-cause hypotheses (explicitly speculative) kept separate. Responses
are validated: supporting IDs that weren't in the provided samples are dropped. Every response
is cached in `llm_cache` (key = sha256(kind | model | payload)). Labels are only regenerated when the
representative set changes, and labels an analyst edited (`manual`) are never overwritten.
Without a key or on API failure, deterministic fallbacks are used and marked `fallback`.
The LLM never counts or aggregates anything and is never asked about demographics.

## Incident deduplication

`deduplication/incident_matching.py` only compares complaints from the **same verified
customer_id**. Different customers and anonymous complaints are never merged. For each complaint,
in time order, against that customer's existing incidents:

| Rule | Condition | Outcome |
|---|---|---|
| T1 | shared order / transaction ID | merge (confidence 0.95) |
| — | same-type reference IDs that differ (two different orders) | never merge |
| T2 | within 7 days **and** cosine ≥ 0.55 **and** same issue cluster or category | merge (0.75–0.90) |
| T3 | two of: near time window, cosine ≥ 0.45, same topic | new incident + review candidate |

Embedding similarity on its own never merges anything. Every link stores its confidence and an
evidence JSON. Incident IDs are deterministic (uuid5 of the canonical complaint), so recomputation is stable.

## Prioritization (`analytics/prioritization.py`)

`P = 0.35·I + 0.25·S + 0.20·R + 0.10·G + 0.10·B` (weights configurable, must sum to 1). Each component is 0–100:

- **I (impact)** = 100·ln(1+u)/ln(1+u_max), where u = unique affected customers (verified IDs + anonymous incidents). Using customers rather than reports, plus log scaling, stops large or duplicate-heavy clusters from dominating.
- **S (severity)** = 0.6·mean + 0.4·p90 of complaint severity points (CRITICAL 100, HIGH 75, MEDIUM 45, LOW 15).
- **R (recurrence)** = 100·(0.5·share of customers with ≥2 incidents + 0.5·share of weeks active).
- **G (growth)** = 100·sigmoid(1.5·log2((recent7d+1)/(prior weekly avg+1))). 50 = flat. Fixed at a neutral 50 when there are fewer than 14 days of history.
- **B (business)** = 0.7·category weight + 0.3·share of reports citing money/orders.

Each issue stores the full breakdown and a template-generated (non-LLM) explanation. With
dashboard filters active, scores are recomputed on the filtered subset using the same function.

## Trends and emerging issues (`analytics/trends.py`)

Daily incident counts per issue. The baseline is the 14 days before the 3-day recent window.
`z = (recent_mean − μ) / (max(σ, √μ, 0.5)/√3)`. An issue is flagged when z ≥ 2 and it has ≥ 3 recent incidents.
If there are fewer than 17 days of history, nothing is flagged and responses carry `limited_data: true`.

## Segmentation (`analytics/segmentation.py`)

Unique customers, complaints and incidents per segment value. Missing values are reported as "Unknown" and
never inferred. Segments with fewer than 3 customers are suppressed, and customer IDs are never returned.
Rates (per 1,000) are reported only when population denominators have been uploaded. Otherwise the
response says `metric_type: "count"` and includes an explicit note.

## Processing and errors

`POST /complaints/upload` persists the batch synchronously (valid rows are kept even when other rows
fail) and schedules the pipeline with FastAPI `BackgroundTasks`. The batch row tracks
`status / stage / progress / error_message`, and the UI polls it. A process-wide lock means only one pipeline
runs at a time. Exceptions are logged with a traceback, recorded on the batch (`FAILED`) and re-raised.
