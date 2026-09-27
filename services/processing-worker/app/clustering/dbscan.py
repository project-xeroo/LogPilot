"""Weighted DBSCAN (cosine distance) and silhouette score in pure NumPy.

Density-based clustering per PRD tool 07. Implemented directly (rather than via scikit-learn) so the
worker image stays small and runs on locked-down hosts; behaviour matches sklearn's DBSCAN with
`sample_weight` and `metric="cosine"`.

Inputs are L2-normalised rows, so cosine distance = 1 - dot product. Neighbourhoods are found in
chunks so memory stays O(n) instead of O(n^2).
"""
from __future__ import annotations

import numpy as np

CHUNK = 1024


def radius_neighbors(X: np.ndarray, eps: float) -> list[np.ndarray]:
    """Indices j with cosine distance(i, j) <= eps, for every row i (includes i itself)."""
    n = X.shape[0]
    out: list[np.ndarray] = []
    thr = 1.0 - eps
    for s in range(0, n, CHUNK):
        sims = X[s : s + CHUNK] @ X.T
        for row in sims:
            out.append(np.flatnonzero(row >= thr - 1e-9))
    return out


def dbscan(X: np.ndarray, eps: float, min_samples: float, sample_weight: np.ndarray | None = None) -> np.ndarray:
    """Returns labels, -1 for noise. A point is 'core' when the summed weight of its eps-neighbourhood
    (including itself) is >= min_samples."""
    n = X.shape[0]
    w = np.ones(n) if sample_weight is None else np.asarray(sample_weight, dtype=float)
    nbrs = radius_neighbors(X, eps)
    core = np.array([w[nb].sum() >= min_samples for nb in nbrs])
    labels = np.full(n, -1, dtype=int)
    cid = 0
    for i in range(n):
        if labels[i] != -1 or not core[i]:
            continue
        labels[i] = cid
        stack = [i]
        while stack:
            p = stack.pop()
            if not core[p]:
                continue  # border points join a cluster but do not expand it
            for q in nbrs[p]:
                if labels[q] == -1:
                    labels[q] = cid
                    stack.append(q)
        cid += 1
    return labels


def silhouette_cosine(X: np.ndarray, labels: np.ndarray, max_points: int = 2000, seed: int = 0) -> float | None:
    """Mean silhouette over (a sample of) clustered points using cosine distance. Needs >= 2 clusters."""
    uniq = np.unique(labels)
    if len(uniq) < 2 or len(labels) < 3:
        return None
    idx = np.arange(len(labels))
    if len(idx) > max_points:
        idx = np.random.default_rng(seed).choice(idx, max_points, replace=False)
    Xs, Ls = X[idx], labels[idx]
    D = 1.0 - Xs @ Xs.T
    np.clip(D, 0.0, 2.0, out=D)
    scores = []
    for k in range(len(idx)):
        same = Ls == Ls[k]
        n_same = same.sum()
        if n_same <= 1:
            continue  # sklearn convention: singleton clusters score 0 and are skipped in the mean of valid points
        a = D[k, same].sum() / (n_same - 1)
        b = min(D[k, Ls == c].mean() for c in uniq if c != Ls[k] and (Ls == c).any())
        scores.append((b - a) / max(a, b, 1e-12))
    return float(np.mean(scores)) if scores else None
