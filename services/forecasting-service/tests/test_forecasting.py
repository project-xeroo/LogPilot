import numpy as np

from app.baseline import BaselineResult, detect_episodes
from app.baseline.baselines import _jsd
from app.drift import DriftResult
from app.indicators import SimilarityResult
from app.indicators.feedback import W_MAX, W_MIN, _normalise
from app.indicators.signatures import _imperative
from app.scoring.risk import combine, estimate_eta, level_for, similarity_component
from app.velocity import VelocityResult
from shared.models import ForecastWeights


def W(v=0.30, s=0.40, b=0.30):
    return ForecastWeights(w_velocity=v, w_similarity=s, w_baseline=b, signature_threshold=0.8)


def test_risk_uses_the_prd_weights_30_40_30():
    vel, sim, base, drift = VelocityResult(score=100), SimilarityResult(score=0, signatures_known=1), BaselineResult(score=0), DriftResult()
    assert combine(vel, sim, base, drift, W())[0] == 30.0
    sim = SimilarityResult(score=100, signatures_known=1)
    # similarity = 70% leading-indicator match + 30% pattern drift; full drift + full match reaches the whole 40%
    assert combine(VelocityResult(score=0), sim, BaselineResult(score=0), drift, W())[0] == 28.0
    assert combine(VelocityResult(score=0), sim, BaselineResult(score=0), DriftResult(score=1.0), W())[0] == 40.0
    assert combine(VelocityResult(score=0), SimilarityResult(score=0, signatures_known=1), BaselineResult(score=100), drift, W())[0] == 30.0
    r = combine(VelocityResult(score=100), sim, BaselineResult(score=100), DriftResult(score=1.0), W())[0]
    assert r == 100.0


def test_thresholds_default_60_warning_80_critical():
    assert level_for(59.9, 60, 80) is None and level_for(60, 60, 80) == "warning" and level_for(80, 60, 80) == "critical"


def test_similarity_falls_back_to_drift_without_history():
    assert similarity_component(SimilarityResult(signatures_known=0), DriftResult(score=0.5)) == 50.0
    assert similarity_component(SimilarityResult(score=100, signatures_known=2), DriftResult(score=0.0)) == 70.0


def test_eta_extrapolates_exponential_growth_and_bounds_the_range():
    v = VelocityResult(errors_per_min=4.0, growth_rate=0.2, velocity=1.0)
    lo, hi = estimate_eta(v, failure_level=20.0, risk=70, warning=60)
    assert 0 < lo < hi and 4 < (lo + hi) / 2 < 12
    assert estimate_eta(v, 20.0, risk=10, warning=60) is None  # no ETA while risk is low
    assert estimate_eta(VelocityResult(errors_per_min=25.0), 20.0, 90, 60) == (0.0, 3.0)  # already past the line


def test_episode_detection_ignores_isolated_spikes():
    e = np.zeros(300)
    e[100:112] = 30  # a real incident
    e[200], e[203] = 12, 12  # two blips close together
    eps = detect_episodes(e)
    assert eps == [(100, 111)]


def test_weights_stay_bounded_and_normalised():
    w = _normalise(np.array([0.05, 0.9, 0.05]))
    assert abs(w.sum() - 1) < 1e-9 and w.min() >= W_MIN - 1e-9 and w.max() <= W_MAX + 1e-9


def test_pattern_divergence_is_zero_for_identical_and_high_for_disjoint():
    assert _jsd({"a": 1.0}, {"a": 1.0}) == 0.0
    assert _jsd({"a": 1.0}, {"b": 1.0}) > 0.99


def test_actions_are_phrased_as_steps():
    assert _imperative("Restarted session-worker-2") == "Restart session-worker-2"
    assert _imperative("scaling redis from 2 to 3 replicas") == "Scale redis from 2 to 3 replicas"
