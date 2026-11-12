# Complaint Compass

Customer complaint analytics and prioritization (HackStellar PS-19 MVP).

Upload a CSV of complaints → text is cleaned and PII-masked → embedded with
`all-MiniLM-L6-v2` into ChromaDB → recurring issues discovered with HDBSCAN → complaints
classified and clusters labeled (GPT-4o-mini, or a deterministic fallback) → duplicate
reports collapsed into incidents → issues ranked with a transparent weighted priority
score → explored in a React dashboard.

- **Backend:** Python 3.11+ · FastAPI · SQLite · ChromaDB · sentence-transformers · scikit-learn (HDBSCAN) · OpenAI (optional)
- **Frontend:** React 18 · TypeScript · Vite · Tailwind CSS 4 · Recharts · lucide-react
- Docs: [Architecture](docs/ARCHITECTURE.md) · [API](docs/API.md) · [Evaluation results](docs/evaluation.md)

## Quick start

Prerequisites: Python 3.11+ (tested on 3.12), Node 20+ (tested on 24), ~1.5 GB disk for
PyTorch CPU + the embedding model. No Docker or cloud services required.

### 1. Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu   # small CPU-only wheel
pip install -r requirements.txt
cp .env.example .env              # optional: set OPENAI_API_KEY to enable GPT-4o-mini
uvicorn app.main:app --port 8000  # first start downloads the MiniLM model (~90 MB)
```

The API is now at http://localhost:8000 (interactive docs at http://localhost:8000/docs).
SQLite and ChromaDB data persist under `backend/storage/` (created automatically).

### 2. Frontend (separate terminal)

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 (proxies /api to the backend on :8000)
```

### 3. Load the sample data

Open http://localhost:5173/upload and drop `backend/data/sample_complaints.csv`, or:

```bash
curl -F file=@backend/data/sample_complaints.csv http://localhost:8000/api/v1/complaints/upload
# optional: population denominators so segment views show rates instead of counts
curl -F file=@backend/data/sample_population.csv http://localhost:8000/api/v1/analytics/populations
```

Processing runs in the background (a few seconds for ~600 rows on CPU). The upload
page shows progress per stage. Uploading the same file again returns `409`.

To regenerate the synthetic dataset: `python scripts/generate_dataset.py --seed 42` (from `backend/`).

## Tests and evaluation

```bash
cd backend && .venv/bin/pytest -q                        # 76 tests; OpenAI integration test skips without a key
cd backend && .venv/bin/python scripts/evaluate.py --write-report   # real metrics -> docs/evaluation.md
cd frontend && npm test                                  # Vitest unit tests
cd frontend && npm run build                             # type-check + production build
```

## Configuration

All settings are environment variables (see [`backend/.env.example`](backend/.env.example)):
storage paths, HDBSCAN parameters, cluster consolidation threshold, dedup thresholds,
priority weights (validated to sum to 1), trend-detection parameters and the segment
privacy threshold. `OPENAI_API_KEY` is read only by the backend and never sent to the browser.

## Using without an OpenAI key

Everything works without a key. Classification falls back to keyword rules, and cluster
titles fall back to the dominant category plus distinctive TF-IDF terms. No root-cause
hypotheses are generated in this mode, and the UI says so. Each classification and label
records its source (`llm` / `fallback` / `manual`). With a key, results are cached in SQLite,
so re-runs don't repeat API calls. If the API fails mid-run, the pipeline switches to the
fallback for the remaining items and records the error.

## Project layout

```
backend/
  app/
    core/           config (pydantic-settings), lifespan (singletons: model, Chroma, DB)
    ingestion/      IngestionAdapter ABC, CSV adapter, validator, normalizer
    preprocessing/  cleaner (unicode/markup/whitespace), PII masking + reference-ID extraction
    inference/      embeddings, vector_store (Chroma), similarity, clustering (HDBSCAN), classifier (LLM + fallback)
    deduplication/  identity_resolution, incident_matching
    analytics/      severity, prioritization, segmentation, trends
    database/       sqlite (schema), repository (all SQL), models (enums)
    schemas/        pydantic models
    services/       pipeline orchestrator, ingestion + analytics services
    api/            thin FastAPI routers
  scripts/          generate_dataset.py, evaluate.py
  data/             sample_complaints.csv, ground_truth.csv (evaluation only), sample_population.csv
  tests/
frontend/src/
  components/ pages/ hooks/ services/ types/ layouts/ lib/
docs/  ARCHITECTURE.md  API.md  evaluation.md
```

## Known limitations

- Severity from the keyword fallback is weak (macro-F1 ≈ 0.34 on the sample). Use an OpenAI key for meaningful severity.
- Clustering finds 18 issues for 10 true families on the synthetic set (some families split by phrasing, one refund/payment merge). Thresholds are configurable.
- Dedup is deliberately conservative: precision ≈ 0.97, recall ≈ 0.40. Uncertain matches go to a review queue (`GET /api/v1/complaints/review/duplicates`); there is no UI for resolving them yet.
- Only CSV ingestion is implemented. Email, chat and voice adapters would plug into `IngestionAdapter`.
- A single process runs a single pipeline at a time (in-process lock, FastAPI BackgroundTasks). There is no distributed worker.
- No authentication or authorization.
- The synthetic dataset is template-based, so the metrics in `docs/evaluation.md` are not a real-world benchmark.
