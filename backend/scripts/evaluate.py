"""Evaluate the pipeline against the synthetic ground truth.

Runs the real pipeline on data/sample_complaints.csv in a fresh temporary storage
directory and compares outputs with data/ground_truth.csv (which the pipeline never reads).

Metrics:
  * Semantic similarity: Precision@10 and R-Precision of nearest neighbours (relevant = same issue family)
  * Clustering: ARI / NMI on clustered points + ARI with noise as its own label, noise ratio
  * Deduplication: pairwise precision / recall / F1 of "same incident" pairs
  * Severity classification: macro-F1 (and per-class report)
  * Ranking: NDCG@10 and Precision@10 against the ground-truth family priority ranking
  * Multi-channel (``--channels``): after the CSV metrics are computed, ingests the customer
    directory + email / chat / social / voice samples from data/channels/ and checks identity
    linking, cross-channel duplicate merging, rejected items and voice reference-ID recovery
    against data/channels/channel_ground_truth.csv

Usage:  python scripts/evaluate.py [--write-report] [--channels] [--sample 400]
Settings can be overridden via env vars (e.g. DEDUP_SEMANTIC_THRESHOLD=0.6).
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import adjusted_rand_score, classification_report, f1_score, normalized_mutual_info_score

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.analytics.prioritization import compute_issue_scores  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.lifespan import build_container  # noqa: E402

FAMILY_CATEGORY = {
    "payment_failure": "Payments", "refund_delay": "Refunds", "delivery_delay": "Delivery",
    "damaged_product": "Product Quality", "login_problem": "Account Access", "account_suspension": "Account Status",
    "poor_support": "Customer Support", "subscription_cancel": "Subscription", "incorrect_billing": "Billing",
    "app_crash": "App Stability",
}


def pairwise_prf(pred_groups: pd.Series, true_groups: pd.Series) -> dict[str, float]:
    """Pairwise P/R/F1 over items that share a predicted group vs a true group."""
    def pairs(groups: pd.Series) -> set[tuple[str, str]]:
        out = set()
        for _, idx in groups.groupby(groups).groups.items():
            out.update(itertools.combinations(sorted(idx), 2))
        return out

    p, t = pairs(pred_groups), pairs(true_groups)
    tp = len(p & t)
    prec = tp / len(p) if p else 0.0
    rec = tp / len(t) if t else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4), "predicted_pairs": len(p),
            "true_pairs": len(t)}


def ndcg_at_k(relevances: list[float], ideal: list[float], k: int = 10) -> float:
    def dcg(rels: list[float]) -> float:
        return sum((2 ** r - 1) / np.log2(i + 2) for i, r in enumerate(rels[:k]))

    idcg = dcg(sorted(ideal, reverse=True))
    return dcg(relevances) / idcg if idcg else 0.0


CHANNEL_DIR = BACKEND / "data" / "channels"


def evaluate_channels(c) -> dict:
    """Ingest the multi-channel samples on top of the processed CSV and score them vs channel ground truth."""
    from app.ingestion.base import UploadedFile
    from app.preprocessing.cleaner import clean_text

    conn = c.db.connect()
    from app.database.repository import ComplaintFilters

    before = c.analytics.overview(ComplaintFilters())
    directory = c.ingestion.import_directory((CHANNEL_DIR / "customer_directory.csv").read_bytes(),
                                             "customer_directory.csv")
    inputs = {
        "email": sorted((CHANNEL_DIR / "email").iterdir()),
        "chat": [CHANNEL_DIR / "chat_transcripts.json"],
        "social": [CHANNEL_DIR / "social_posts.jsonl"],
        "voice": sorted((CHANNEL_DIR / "voice").glob("*.wav")) + [CHANNEL_DIR / "voice" / "voice_manifest.csv"],
    }
    truth = pd.read_csv(CHANNEL_DIR / "channel_ground_truth.csv", dtype=str, keep_default_na=False)
    label_to_complaint: dict[tuple[str, str], str | None] = {}
    timings: dict[str, float] = {}
    for channel, paths in inputs.items():
        t0 = time.time()
        res = c.ingestion.ingest_files(channel, [UploadedFile(p.name, p.read_bytes()) for p in paths])
        c.pipeline.process_batch(res["batch_id"])
        timings[channel] = round(time.time() - t0, 1)
        by_row = dict(conn.execute("SELECT source_row, complaint_id FROM complaints WHERE batch_id = ?",
                                   (res["batch_id"],)).fetchall())
        mbox_names = iter(t for t in truth[truth.channel == "email"]["item"] if not (CHANNEL_DIR / "email" / t).exists())
        for it in res["items"]:
            label = it["label"]
            if "#" in label:
                file, key = label.split("#", 1)
                label = next(mbox_names) if file.endswith(".mbox") else key
            label_to_complaint[(channel, label)] = by_row.get(it["item"]) if it["accepted"] else None

    links = pd.DataFrame(conn.execute(
        "SELECT l.complaint_id, l.incident_id, c.batch_id, c.customer_id, c.reference_ids FROM complaint_incident_links l "
        "JOIN complaints c USING (complaint_id)").fetchall(), columns=["cid", "incident", "batch", "customer", "refs"])
    first_batch = conn.execute("SELECT batch_id FROM ingestion_batches WHERE channel = 'CSV'").fetchone()[0]
    incident_of = dict(zip(links.cid, links.incident))
    members = links.groupby("incident")
    review = {r[0] for r in conn.execute("SELECT complaint_id FROM duplicate_review_candidates").fetchall()}

    out: dict = {"directory": {k: directory[k] for k in ("customers", "contacts_stored", "complaints_relinked")},
                 "seconds_per_channel": timings, "per_channel": {}}
    dup_ok = dup_total = dup_review = new_false_merge = new_total = id_ok = id_total = 0
    rej_ok = rej_total = voice_ref_ok = voice_ref_total = 0
    for ch in inputs:
        t = truth[truth.channel == ch]
        stats = {"items": len(t), "accepted": 0, "rejected": 0}
        for _, row in t.iterrows():
            cid = label_to_complaint.get((ch, row["item"]), "MISSING")
            if cid == "MISSING":
                raise RuntimeError(f"{ch}/{row['item']} not found in ingestion output")
            stats["accepted" if cid else "rejected"] += 1
            if row["kind"] == "reject":
                rej_total += 1
                rej_ok += cid is None
                continue
            if cid is None:
                continue
            stored = conn.execute("SELECT customer_id, reference_ids FROM complaints WHERE complaint_id = ?", (cid,)).fetchone()
            exp = row["customer_id"]
            id_total += 1
            id_ok += (stored[0] == exp) if exp.startswith("CUST-") else \
                (stored[0] is None) if exp == "anonymous" else bool(stored[0]) and not stored[0].startswith("CUST-")
            members_df = members.get_group(incident_of[cid]) if cid in incident_of else links.iloc[0:0]
            if row["kind"] == "cross_channel_duplicate":
                dup_total += 1
                hit = ((members_df.batch == first_batch) & members_df.refs.str.contains(row["reference"])).any()
                dup_ok += bool(hit)
                dup_review += (not hit) and cid in review
                if ch == "voice":
                    voice_ref_total += 1
                    voice_ref_ok += row["reference"] in json.loads(stored[1])
            else:
                new_total += 1
                new_false_merge += len(members_df) > 1
        out["per_channel"][ch] = stats
    after = c.analytics.overview(ComplaintFilters())
    out.update({
        "rejects_correct": f"{rej_ok}/{rej_total}",
        "identity_link_accuracy": round(id_ok / id_total, 4) if id_total else None,
        "cross_channel_duplicates_merged": f"{dup_ok}/{dup_total}",
        "cross_channel_duplicates_flagged_for_review": dup_review,
        "new_complaints_wrongly_merged": f"{new_false_merge}/{new_total}",
        "voice_reference_ids_recovered": f"{voice_ref_ok}/{voice_ref_total}",
    })
    out["overview_delta"] = {k: after[k] - before[k] for k in ("total_reports", "total_incidents", "duplicate_reports")}
    return out


def run(sample: int, write_report: bool, channels: bool = False) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="cc_eval_"))
    settings = Settings(db_path=tmp / "eval.db", chroma_dir=tmp / "chroma", llm_cache_path=tmp / "llm_cache.db")
    t0 = time.time()
    c = build_container(settings)
    content = (BACKEND / "data" / "sample_complaints.csv").read_bytes()
    batch = c.ingestion.ingest(content, "sample_complaints.csv")
    summary = c.pipeline.process_batch(batch["batch_id"])
    runtime = time.time() - t0

    gt = pd.read_csv(BACKEND / "data" / "ground_truth.csv").set_index("row_number")
    frame = c.repo.complaint_frame()
    rows = dict(c.db.connect().execute("SELECT complaint_id, source_row FROM complaints").fetchall())
    frame["row"] = frame["complaint_id"].map(rows)
    frame = frame.join(gt, on="row", rsuffix="_true")
    assert frame["issue_family"].notna().all(), "every stored complaint must map to a ground-truth row"
    results: dict = {"runtime_seconds": round(runtime, 1), "complaints": len(frame), "llm_used": c.classifier.available,
                     "pipeline_summary": summary, "settings": {
                         "hdbscan_min_cluster_size": settings.hdbscan_min_cluster_size,
                         "hdbscan_min_samples": settings.hdbscan_min_samples,
                         "cluster_merge_threshold": settings.cluster_merge_threshold,
                         "dedup_semantic_threshold": settings.dedup_semantic_threshold,
                         "dedup_time_window_days": settings.dedup_time_window_days}}

    # ------------------------------------------------ semantic similarity
    fam = dict(zip(frame["complaint_id"], frame["issue_family"]))
    fam_sizes = Counter(fam.values())
    rng = np.random.default_rng(0)
    eligible = [cid for cid, f in fam.items() if f != "isolated"]
    probe = rng.choice(eligible, size=min(sample, len(eligible)), replace=False)
    p10, rprec = [], []
    for cid in probe:
        f = fam[cid]
        r = fam_sizes[f] - 1
        hits = c.similarity.find_similar_complaints(cid, top_k=max(10, r))
        p10.append(np.mean([fam.get(h["complaint_id"]) == f for h in hits[:10]]))
        rprec.append(np.mean([fam.get(h["complaint_id"]) == f for h in hits[:r]]) if r else 1.0)
    results["similarity"] = {"precision_at_10": round(float(np.mean(p10)), 4),
                             "r_precision": round(float(np.mean(rprec)), 4), "probes": len(probe)}

    # ------------------------------------------------ clustering
    pred = frame["issue_id"].fillna("NOISE")
    m = frame["issue_id"].notna()
    iso = frame["issue_family"] == "isolated"
    results["clustering"] = {
        "clusters": int(frame["issue_id"].nunique()),
        "true_families": int(frame.loc[~iso, "issue_family"].nunique()),
        "noise_ratio": round(float((~m).mean()), 4),
        "ari_clustered_points": round(adjusted_rand_score(frame.loc[m, "issue_family"], frame.loc[m, "issue_id"]), 4),
        "nmi_clustered_points": round(normalized_mutual_info_score(frame.loc[m, "issue_family"], frame.loc[m, "issue_id"]), 4),
        "ari_all_noise_as_label": round(adjusted_rand_score(frame["issue_family"], pred), 4),
        "isolated_left_as_noise": round(float((~m[iso]).mean()), 4) if iso.any() else None,
        "family_noise_ratio": round(float((~m[~iso]).mean()), 4),
    }

    # ------------------------------------------------ deduplication
    dd = pairwise_prf(frame.set_index("complaint_id")["incident_id"],
                      frame.set_index("complaint_id")["incident_key"])
    true_dups = int(frame["is_duplicate_report"].sum())
    pred_dups = int((frame["link_type"] == "DUPLICATE").sum())
    # cross-customer merges must never happen
    cross = frame.groupby("incident_id")["customer_id"].nunique(dropna=True)
    # Coverage: true duplicate reports that were either auto-merged OR flagged for human review
    review_ids = {r["complaint_id"] for r in c.repo.review_candidates(10000)}
    dup_rows = frame[frame["is_duplicate_report"] == 1]
    covered = dup_rows["complaint_id"].isin(review_ids) | (dup_rows["link_type"] == "DUPLICATE")
    dd["duplicate_reports_merged_or_flagged"] = round(float(covered.mean()), 4) if len(dup_rows) else None
    results["deduplication"] = {**dd, "true_duplicate_reports": true_dups, "predicted_duplicate_reports": pred_dups,
                                "incidents_spanning_multiple_customers": int((cross > 1).sum()),
                                "review_candidates": len(c.repo.review_candidates(10000))}

    # ------------------------------------------------ severity
    sev = frame.dropna(subset=["severity"])
    labels = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    results["severity"] = {
        "macro_f1": round(f1_score(sev["severity_true"], sev["severity"], labels=labels, average="macro"), 4),
        "accuracy": round(float((sev["severity_true"] == sev["severity"]).mean()), 4),
        "classifier_source": dict(c.repo.conn.execute(
            "SELECT source, COUNT(*) FROM complaint_classifications GROUP BY source").fetchall()),
        "report": classification_report(sev["severity_true"], sev["severity"], labels=labels, zero_division=0,
                                         output_dict=True),
    }

    # ------------------------------------------------ ranking
    # Ground-truth priority: same deterministic formula, but fed TRUE families/incidents/severities.
    truth_frame = frame[~iso].copy()
    truth_frame["issue_id"] = truth_frame["issue_family"]
    truth_frame["incident_id"] = truth_frame["incident_key"]
    truth_frame["severity"] = truth_frame["severity_true"]
    truth_frame["category"] = truth_frame["issue_family"].map(FAMILY_CATEGORY)
    truth_issues = [{"issue_id": f, "title": f, "category": FAMILY_CATEGORY[f]} for f in FAMILY_CATEGORY]
    true_scores = {s.issue_id: s.priority_score for s in compute_issue_scores(truth_frame, truth_issues, settings.weights)}
    true_rank = sorted(true_scores, key=lambda f: -true_scores[f])
    lo, hi = min(true_scores.values()), max(true_scores.values())
    rel = {f: 3.0 * (v - lo) / (hi - lo) if hi > lo else 1.0 for f, v in true_scores.items()}  # graded 0-3

    predicted = c.repo.issues_with_metrics()
    majority = frame[m].groupby("issue_id")["issue_family"].agg(lambda s: s.value_counts().index[0]).to_dict()
    seen: set[str] = set()
    pred_rels = []
    for issue in predicted[:10]:
        f = majority.get(issue["issue_id"])
        # A family credited once: later fragments of the same family add no new gain.
        gain = rel.get(f, 0.0) if f not in seen else 0.0
        seen.add(f)
        pred_rels.append(gain)
    top5 = set(true_rank[:5])
    results["ranking"] = {
        "ndcg_at_10": round(ndcg_at_k(pred_rels, list(rel.values()), 10), 4),
        "precision_at_10_in_true_top5_families": round(
            float(np.mean([majority.get(i["issue_id"]) in top5 for i in predicted[:10]])), 4),
        "true_family_ranking": [(f, round(true_scores[f], 1)) for f in true_rank],
        "predicted_top10": [(i["title"], majority.get(i["issue_id"]), round(i["priority_score"], 1))
                            for i in predicted[:10]],
    }
    if channels:
        results["channels"] = evaluate_channels(c)
    if write_report:
        write_markdown(results)
    return results


def write_markdown(r: dict) -> None:
    out = BACKEND.parent / "docs" / "evaluation.md"
    sev = r["severity"]
    lines = [
        "# Evaluation results",
        "",
        f"_Generated by `backend/scripts/evaluate.py` on {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}. "
        "All numbers below are computed from an actual pipeline run on `backend/data/sample_complaints.csv` "
        "against `backend/data/ground_truth.csv`; nothing is hand-entered._",
        "",
        f"- Complaints evaluated: **{r['complaints']}** · runtime {r['runtime_seconds']} s · "
        f"LLM used: **{'yes (gpt-4o-mini)' if r['llm_used'] else 'no - deterministic fallback'}**",
        f"- Settings: `{json.dumps(r['settings'])}`",
        "",
        "| Area | Metric | Value |",
        "|---|---|---|",
        f"| Semantic similarity | Precision@10 (same issue family) | {r['similarity']['precision_at_10']} |",
        f"| Semantic similarity | R-Precision | {r['similarity']['r_precision']} |",
        f"| Clustering | Clusters found / true families | {r['clustering']['clusters']} / {r['clustering']['true_families']} |",
        f"| Clustering | ARI (clustered points) | {r['clustering']['ari_clustered_points']} |",
        f"| Clustering | NMI (clustered points) | {r['clustering']['nmi_clustered_points']} |",
        f"| Clustering | ARI (noise as its own label) | {r['clustering']['ari_all_noise_as_label']} |",
        f"| Clustering | Noise ratio (all / family complaints) | {r['clustering']['noise_ratio']} / {r['clustering']['family_noise_ratio']} |",
        f"| Clustering | Isolated complaints left as noise | {r['clustering']['isolated_left_as_noise']} |",
        f"| Deduplication | Pairwise precision | {r['deduplication']['precision']} |",
        f"| Deduplication | Pairwise recall | {r['deduplication']['recall']} |",
        f"| Deduplication | Pairwise F1 | {r['deduplication']['f1']} |",
        f"| Deduplication | True duplicate reports merged or flagged for review | {r['deduplication']['duplicate_reports_merged_or_flagged']} |",
        f"| Deduplication | Duplicate reports true / predicted | {r['deduplication']['true_duplicate_reports']} / {r['deduplication']['predicted_duplicate_reports']} |",
        f"| Deduplication | Incidents spanning >1 customer (must be 0) | {r['deduplication']['incidents_spanning_multiple_customers']} |",
        f"| Severity | Macro-F1 | {sev['macro_f1']} |",
        f"| Severity | Accuracy | {sev['accuracy']} |",
        f"| Ranking | NDCG@10 | {r['ranking']['ndcg_at_10']} |",
        f"| Ranking | Precision@10 (issue in true top-5 families) | {r['ranking']['precision_at_10_in_true_top5_families']} |",
        "",
        "## Severity per class",
        "",
        "| Class | Precision | Recall | F1 | Support |",
        "|---|---|---|---|---|",
    ]
    for lab in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
        x = sev["report"][lab]
        lines.append(f"| {lab} | {x['precision']:.3f} | {x['recall']:.3f} | {x['f1-score']:.3f} | {int(x['support'])} |")
    lines += ["", "## Ranking detail", "", "Ground-truth family ranking (same formula on true labels):", ""]
    lines += [f"{i + 1}. `{f}` - {s}" for i, (f, s) in enumerate(r["ranking"]["true_family_ranking"])]
    lines += ["", "Predicted top 10 (title -> majority true family, priority):", ""]
    lines += [f"{i + 1}. {t} -> `{f}` ({p})" for i, (t, f, p) in enumerate(r["ranking"]["predicted_top10"])]
    lines += [
        "",
        "## Caveats",
        "",
        "- The dataset is synthetic and template-based: each issue family is written with a limited set of "
        "paraphrase templates, so embeddings form tighter sub-clusters than real-world text. Real data will differ.",
        "- Without an OpenAI key, severity comes from the keyword fallback in `app/inference/classifier.py`. "
        "Those rules and the dataset templates were written by the same author, so the fallback severity score "
        "is optimistic and should not be read as a general-purpose benchmark.",
        "- The ground-truth ranking uses the same priority formula as the system, so NDCG measures how well "
        "clustering + dedup + classification recover the ranking that perfect labels would produce - not whether "
        "the formula itself matches business judgement.",
        "- GPT-based severity/labels are only evaluated when `OPENAI_API_KEY` is set; that run is not included here "
        "unless 'LLM used' above says yes.",
    ]
    if "channels" in r:
        ch = r["channels"]
        lines += [
            "",
            "## Multi-channel ingestion",
            "",
            "Run after the metrics above: the customer directory and the email / chat / social / voice samples in "
            "`backend/data/channels/` are ingested and processed on top of the CSV, then compared with "
            "`channel_ground_truth.csv` (never read by the pipeline). Voice uses real Whisper transcription of "
            "synthesized (text-to-speech) calls.",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| Cross-channel re-reports merged into the original CSV incident | {ch['cross_channel_duplicates_merged']} |",
            f"| ... of the unmerged, flagged for review | {ch['cross_channel_duplicates_flagged_for_review']} |",
            f"| New complaints wrongly merged into another incident | {ch['new_complaints_wrongly_merged']} |",
            f"| Customer identity resolved as expected (explicit / directory / pseudonymous / anonymous) | {ch['identity_link_accuracy']} |",
            f"| Items that should be rejected (auto-reply, bot-only chat, retweet) and were | {ch['rejects_correct']} |",
            f"| Order / transaction IDs recovered from voice transcripts | {ch['voice_reference_ids_recovered']} |",
            f"| Dashboard change (reports / incidents / duplicate reports) | {ch.get('overview_delta')} |",
            f"| Processing time per channel (s, incl. transcription) | {ch['seconds_per_channel']} |",
            "",
            "Per channel: " + ", ".join(f"{k}: {v['accepted']} accepted / {v['rejected']} rejected of {v['items']}"
                                        for k, v in ch["per_channel"].items()),
        ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=300)
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--channels", action="store_true", help="also evaluate multi-channel ingestion (data/channels)")
    a = ap.parse_args()
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    res = run(a.sample, a.write_report, a.channels)
    printable = {k: v for k, v in res.items() if k not in ("pipeline_summary",)}
    printable["severity"] = {k: v for k, v in res["severity"].items() if k != "report"}
    print(json.dumps(printable, indent=1, default=str))
