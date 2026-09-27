"""Isolation Forest (Liu et al., 2008) in pure NumPy - ML-based outlier detection for tool 13.

Anomalies are isolated in fewer random splits than normal points; score = 2^(-E[path]/c(psi)),
where ~0.5 is normal and values approaching 1 are outliers.
"""
from __future__ import annotations

import numpy as np

EULER = 0.5772156649


def _c(n: float) -> float:
    if n <= 1:
        return 0.0
    if n == 2:
        return 1.0
    return 2.0 * (np.log(n - 1.0) + EULER) - 2.0 * (n - 1.0) / n


class _Tree:
    __slots__ = ("feat", "thr", "left", "right", "size", "depth_limit")

    def __init__(self, X: np.ndarray, rng: np.random.Generator, depth: int, limit: int):
        self.size = len(X)
        self.feat = self.thr = self.left = self.right = None
        self.depth_limit = depth >= limit
        if depth >= limit or len(X) <= 1:
            return
        spans = X.max(axis=0) - X.min(axis=0)
        cand = np.flatnonzero(spans > 1e-12)
        if len(cand) == 0:
            return
        self.feat = int(rng.choice(cand))
        lo, hi = X[:, self.feat].min(), X[:, self.feat].max()
        self.thr = float(rng.uniform(lo, hi))
        mask = X[:, self.feat] < self.thr
        self.left = _Tree(X[mask], rng, depth + 1, limit)
        self.right = _Tree(X[~mask], rng, depth + 1, limit)

    def path_length(self, x: np.ndarray, depth: int = 0) -> float:
        if self.feat is None:
            return depth + _c(self.size)
        nxt = self.left if x[self.feat] < self.thr else self.right
        return nxt.path_length(x, depth + 1)


class IsolationForest:
    def __init__(self, n_estimators: int = 100, max_samples: int = 256, contamination: float = 0.01, seed: int = 42):
        self.n_estimators, self.max_samples, self.contamination, self.seed = n_estimators, max_samples, contamination, seed
        self._trees: list[_Tree] = []
        self._psi = 0
        self.threshold_ = 0.5

    def fit(self, X: np.ndarray) -> "IsolationForest":
        rng = np.random.default_rng(self.seed)
        n = len(X)
        self._psi = min(self.max_samples, n)
        limit = int(np.ceil(np.log2(max(self._psi, 2))))
        self._trees = [_Tree(X[rng.choice(n, self._psi, replace=False)], rng, 0, limit) for _ in range(self.n_estimators)]
        scores = self.score_samples(X)
        self.threshold_ = float(np.quantile(scores, 1.0 - self.contamination))
        return self

    def score_samples(self, X: np.ndarray) -> np.ndarray:
        """Anomaly score in (0, 1]; higher = more anomalous."""
        denom = _c(self._psi) or 1.0
        out = np.empty(len(X))
        for i, x in enumerate(X):
            mean_path = np.mean([t.path_length(x) for t in self._trees])
            out[i] = 2.0 ** (-mean_path / denom)
        return out

    def predict_outliers(self, X: np.ndarray) -> np.ndarray:
        return self.score_samples(X) >= self.threshold_
