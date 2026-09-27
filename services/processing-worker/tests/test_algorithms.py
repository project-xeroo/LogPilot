from datetime import UTC, datetime, timedelta

import numpy as np

from app.anomaly.detectors import Detected, explain, outlier_anomalies, statistical_anomalies
from app.anomaly.iforest import IsolationForest
from app.clustering.dbscan import dbscan, silhouette_cosine
from shared.utils.analytics import MinuteSeries


def _unit(v):
    v = np.asarray(v, dtype=float)
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def test_dbscan_finds_dense_groups_and_noise():
    rng = np.random.default_rng(0)
    a = _unit(np.array([1, 0, 0]) + rng.normal(0, 0.03, (12, 3)))
    b = _unit(np.array([0, 1, 0]) + rng.normal(0, 0.03, (10, 3)))
    lone = _unit(np.array([[0, 0, 1.0]]))
    X = np.vstack([a, b, lone])
    labels = dbscan(X, eps=0.1, min_samples=3)
    assert len(set(labels[:12])) == 1 and len(set(labels[12:22])) == 1
    assert labels[0] != labels[12] and labels[-1] == -1  # the outlier is noise
    s = silhouette_cosine(X[labels != -1], labels[labels != -1])
    assert s is not None and s > 0.8


def test_dbscan_min_samples_two_groups_pairs_only():
    X = _unit(np.array([[1, 0], [0.99, 0.05], [0, 1.0]]))
    labels = dbscan(X, eps=0.05, min_samples=2)
    assert labels[0] == labels[1] != -1 and labels[2] == -1


def test_isolation_forest_scores_outliers_higher():
    rng = np.random.default_rng(1)
    normal = rng.normal(0, 1, (300, 2))
    X = np.vstack([normal, [[9, 9], [-8, 9]]])
    f = IsolationForest(n_estimators=80, contamination=0.02, seed=3).fit(X)
    s = f.score_samples(X)
    assert s[-1] > np.percentile(s[:-2], 95) and s[-2] > np.percentile(s[:-2], 95)


def _series(errors, total=None, lat=None):
    n = len(errors)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    return MinuteSeries(
        minutes=[t0 + timedelta(minutes=i) for i in range(n)],
        total=np.array(total if total is not None else [10.0] * n),
        errors=np.array(errors, dtype=float), warns=np.zeros(n),
        latency=np.array(lat if lat is not None else [np.nan] * n, dtype=float),
    )


def test_error_spike_detected_with_explanation():
    err = [0.0, 1.0] * 60 + [30.0] * 5 + [0.0] * 20
    found = statistical_anomalies("checkout", _series(err))
    spike = next(d for d in found if d.kind == "error_spike")
    assert spike.observed == 30.0 and spike.severity == "high"
    assert "checkout" in explain(spike) and "errors per minute" in explain(spike)


def test_tiny_blips_are_not_anomalies():
    err = [0.0] * 200
    err[100] = 3.0
    assert not [d for d in statistical_anomalies("x", _series(err)) if d.kind == "error_spike"]


def test_service_silence_and_latency_spike():
    total = [10.0] * 90 + [0.0] * 15
    silence = next(d for d in statistical_anomalies("auth", _series([0.0] * 105, total)) if d.kind == "service_silence")
    assert silence.evidence["silent_minutes"] == 15
    lat = [90.0] * 100 + [2500.0] * 5 + [90.0] * 5
    kinds = {d.kind for d in statistical_anomalies("auth", _series([0.0] * 110, lat=lat))}
    assert "latency_spike" in kinds


def test_outlier_detector_ignores_short_windows_and_flat_series():
    assert outlier_anomalies("x", _series([0.0] * 50), []) == []
    flat = _series([1.0] * 200)
    assert all(isinstance(d, Detected) for d in outlier_anomalies("x", flat, []))
