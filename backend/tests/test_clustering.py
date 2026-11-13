import numpy as np

from app.inference.clustering import ClusteringEngine, cluster_keywords, resolve_stable_ids


def _blobs(seed=0, n_per=30, n_noise=12, dim=32):
    rng = np.random.default_rng(seed)
    centers = np.eye(dim)[:3] * 1.0
    pts, labels = [], []
    for k, c in enumerate(centers):
        p = c + rng.normal(0, 0.05, size=(n_per, dim))
        pts.append(p)
        labels += [k] * n_per
    noise = rng.normal(0, 1, size=(n_noise, dim))
    pts.append(noise)
    labels += [-1] * n_noise
    X = np.vstack(pts)
    X /= np.linalg.norm(X, axis=1, keepdims=True)
    ids = [f"c{i}" for i in range(len(X))]
    return ids, X.astype(np.float32), np.array(labels)


def test_hdbscan_finds_clusters_and_keeps_noise():
    ids, X, truth = _blobs()
    res = ClusteringEngine(min_cluster_size=8, min_samples=4, merge_threshold=0).cluster(ids, X)
    assert len(res.clusters) == 3
    noise_ids = {ids[i] for i in np.where(truth == -1)[0]}
    # the large majority of random points must stay noise (HDBSCAN may absorb a few boundary points)
    assert len(noise_ids & set(res.noise_ids)) >= 0.75 * len(noise_ids)
    for cl in res.clusters:
        member_truth = {truth[ids.index(m)] for m in cl.member_ids} - {-1}
        assert len(member_truth) == 1  # pure clusters
        assert len(cl.representative_ids) == 5
        assert set(cl.representative_ids) <= set(cl.member_ids)
        assert 0 <= min(cl.member_confidence.values()) <= 1


def test_representatives_are_closest_to_centroid():
    ids, X, _ = _blobs()
    res = ClusteringEngine(min_cluster_size=8, min_samples=4, representatives=1, merge_threshold=0).cluster(ids, X)
    for cl in res.clusters:
        sims = {m: float(X[ids.index(m)] @ cl.centroid) for m in cl.member_ids}
        assert cl.representative_ids[0] == max(sims, key=sims.get)


def test_too_few_points_all_noise():
    ids, X, _ = _blobs(n_per=2, n_noise=0)
    res = ClusteringEngine(min_cluster_size=8).cluster(ids, X)
    assert res.clusters == [] and len(res.noise_ids) == len(ids)


def test_consolidation_merges_close_clusters_only():
    rng = np.random.default_rng(1)
    dim = 32
    base = np.eye(dim)[0]
    close = base * 0.9 + np.eye(dim)[1] * 0.3  # cos ~0.95 with base
    far = np.eye(dim)[5]
    pts = [c + rng.normal(0, 0.02, size=(20, dim)) for c in (base, close, far)]
    X = np.vstack(pts)
    X /= np.linalg.norm(X, axis=1, keepdims=True)
    ids = [f"p{i}" for i in range(len(X))]
    engine = ClusteringEngine(min_cluster_size=8, min_samples=4, merge_threshold=0.9)
    labels = np.array([0] * 20 + [1] * 20 + [2] * 20)
    merged = engine.consolidate(labels, X)
    assert merged[0] == merged[20] and merged[0] != merged[40]


def test_consolidation_never_assigns_noise():
    ids, X, _ = _blobs()
    labels = np.array([0] * 30 + [1] * 30 + [2] * 30 + [-1] * 12)
    merged = ClusteringEngine(merge_threshold=0.01).consolidate(labels, X)
    assert (merged[-12:] == -1).all()
    assert (merged[:90] != -1).all()


def test_stable_ids_survive_recluster():
    ids, X, _ = _blobs()
    engine = ClusteringEngine(min_cluster_size=8, min_samples=4, merge_threshold=0)
    first = resolve_stable_ids(engine.cluster(ids, X), [])
    assert all(r.is_new for r in first)
    previous = [{"issue_id": r.issue_id, "cluster_id": r.cluster_id, "centroid": r.cluster.centroid,
                 "members": set(r.cluster.member_ids)} for r in first]
    # add new points to the same blobs and recluster: temporary labels may differ, IDs must not
    ids2, X2, _ = _blobs(seed=7)
    ids2 = [f"new{i}" for i in range(len(ids2))]
    second = resolve_stable_ids(engine.cluster(ids + ids2, np.vstack([X, X2])), previous)
    assert {r.issue_id for r in second} == {r.issue_id for r in first}
    assert not any(r.is_new for r in second)
    # cluster IDs are not HDBSCAN integer labels
    assert all(r.cluster_id.startswith("CL-") for r in second)


def test_new_unrelated_cluster_gets_new_id():
    ids, X, _ = _blobs()
    engine = ClusteringEngine(min_cluster_size=8, min_samples=4, merge_threshold=0)
    first = resolve_stable_ids(engine.cluster(ids, X), [])
    previous = [{"issue_id": r.issue_id, "cluster_id": r.cluster_id, "centroid": r.cluster.centroid,
                 "members": set(r.cluster.member_ids)} for r in first[:1]]
    second = resolve_stable_ids(engine.cluster(ids, X), previous)
    assert sum(not r.is_new for r in second) == 1


def test_cluster_keywords_are_distinctive():
    kw = cluster_keywords({"a": ["refund not received", "where is my refund"],
                           "b": ["app crashes on launch", "the app crashes"]})
    assert "refund" in kw["a"] and any("crash" in k for k in kw["b"])


def test_representatives_skip_near_duplicates():
    from app.inference.clustering import select_representatives

    a = np.array([1.0, 0, 0])
    b = np.array([0.999, 0.0447, 0])  # near-duplicate of a
    c = np.array([0.8, 0.6, 0])
    members = np.vstack([a, b / np.linalg.norm(b), c])
    sims = np.array([0.99, 0.98, 0.9])
    assert select_representatives(members, sims, 2) == [0, 2]
    assert select_representatives(members, sims, 3) == [0, 2, 1]  # filled when diversity runs out


def test_small_data_groups_by_category():
    from app.inference.clustering import ClusteringEngine

    rng = np.random.default_rng(0)
    emb = rng.normal(size=(7, 16)).astype(np.float32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    ids = [f"c{i}" for i in range(7)]
    cats = ["Delivery", "Delivery", "Delivery", "Billing", "Billing", "Refunds", None]
    res = ClusteringEngine(min_cluster_size=8).cluster_by_category(ids, emb, cats)
    groups = sorted(sorted(c.member_ids) for c in res.clusters)
    assert groups == [["c0", "c1", "c2"], ["c3", "c4"]]
    assert sorted(res.noise_ids) == ["c5", "c6"]  # single-member category and unclassified stay unassigned
    for c in res.clusters:
        assert c.representative_ids and set(c.representative_ids) <= set(c.member_ids)
        assert abs(float(np.linalg.norm(c.centroid)) - 1.0) < 1e-5
