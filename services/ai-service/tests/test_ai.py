import json

import numpy as np
import pytest

from app.chat.service import confidence_of, followups
from app.providers import composers
from app.providers.base import AIUnavailable, EgressBlocked, Message, ModelRole
from app.providers.guarded import GuardedProvider
from app.providers.heuristic import HeuristicProvider, embed_text
from app.providers.openai_compatible import OpenAICompatibleProvider, assert_egress_allowed
from app.reports.export import to_markdown, to_pdf
from app.search.engine import Filters, _where
from shared.config import settings
from shared.utils.logtemplate import normalize_message


class Recorder(HeuristicProvider):
    """Records exactly what would have been sent to a cloud provider."""

    name = "recorder"

    def __init__(self):
        self.embedded, self.contexts, self.messages = [], [], []

    def embed(self, texts):
        self.embedded += texts
        return super().embed(texts)

    def complete(self, *, task, messages, role, context=None, **kw):
        self.contexts.append(context)
        self.messages.append(messages)
        return "ok"


def test_embeddings_can_point_at_a_different_provider_than_chat():
    """AI_EMBEDDING_PROVIDER lets embeddings use a different backend than chat/RCA/reports - needed
    when a provider's chat models are good but its embedding model isn't (or isn't provisioned)."""
    chat, embed = Recorder(), Recorder()
    chat.name, embed.name = "cloud-chat", "offline-embed"
    g = GuardedProvider(chat, embed)
    g.complete(task="chat_answer", role=ModelRole.FAST, messages=[Message("user", "hi")])
    g.embed(["hello"])
    assert chat.messages and not chat.embedded  # chat backend never sees an embed call
    assert embed.embedded and not embed.contexts  # embed backend never sees a chat call
    assert g.name == "cloud-chat" and g.embedding_name == "offline-embed"


def test_guarded_provider_defaults_embeddings_to_the_chat_backend():
    single = Recorder()
    g = GuardedProvider(single)
    assert g.embedding_inner is single and g.embedding_name == g.name


def test_pii_never_reaches_the_provider():
    inner = Recorder()
    g = GuardedProvider(inner)
    secret_msg = "user bob@example.com card 4111 1111 1111 1111 password=hunter2"
    g.embed([secret_msg])
    g.complete(task="chat_answer", role=ModelRole.FAST, messages=[Message("user", secret_msg)],
               context={"sources": [{"message": "Authorization: Bearer abcdEFGH1234567890xyz"}]})
    blob = json.dumps([inner.embedded, inner.contexts, [[m.content for m in ms] for ms in inner.messages]])
    for leak in ("bob@example.com", "4111 1111 1111 1111", "hunter2", "abcdEFGH1234567890xyz"):
        assert leak not in blob
    assert g.usage.redactions["EMAIL"] >= 2 and g.usage.redactions["PASSWORD"] >= 1


def test_egress_is_limited_to_the_allowlist(monkeypatch):
    monkeypatch.setattr(settings, "ai_base_url", "https://api.provider.example/v1")
    monkeypatch.setattr(settings, "ai_egress_allowlist", "")
    assert_egress_allowed("https://api.provider.example/v1/chat/completions")
    with pytest.raises(EgressBlocked):
        assert_egress_allowed("https://evil.example.net/steal")
    assert issubclass(EgressBlocked, AIUnavailable)  # a blocked call degrades gracefully like any AI outage
    monkeypatch.setattr(settings, "ai_egress_allowlist", "a.example.com,b.example.com")
    with pytest.raises(EgressBlocked):
        assert_egress_allowed("https://api.provider.example/v1/x")


def test_mock_embeddings_group_paraphrases_and_separate_topics():
    e = lambda s: np.array(embed_text(normalize_message(s)))
    a = e("Redis connection timed out after 5000ms (pool exhausted)")
    b = e("Timeout connecting to redis: connection pool exhausted, waited 3000ms")
    c = e("Database deadlock detected in transaction 4471")
    assert float(a @ b) > 0.85 and float(a @ c) < 0.4
    assert abs(np.linalg.norm(a) - 1) < 1e-5


def test_alert_text_has_the_prd_shape():
    ctx = {"service": "checkout-service", "risk_score": 78, "level": "warning", "velocity_score": 60, "similarity_score": 90, "baseline_score": 80,
           "signals": {"velocity": {"errors_per_min": 12.0, "acceleration": 0.4}, "baseline": {"ratio": 4.2, "z": 6}, "similarity": {"best_match": 0.97}},
           "eta": {"low": 10, "high": 15}, "top_errors": [{"message": "Redis connection timed out", "count": 40}],
           "similar_incidents": [{"label": "redis pool", "incident_start": "2025-11-14T10:00:00+00:00", "similarity": 0.97, "resolved_actions": ["Restart session-worker-2"]},
                                 {"label": "redis pool", "incident_start": "2025-12-03T09:00:00+00:00", "similarity": 0.95, "resolved_actions": ["Scale Redis to 3 replicas"]}]}
    out = json.loads(composers.risk_explanation(ctx))
    t = out["alert_text"]
    assert "78% failure probability" in t and "November 14th and December 3rd" in t and "10–15 minutes" in t and "Recommended:" in t
    assert out["recommended_actions"][0]["source"] == "historical" and out["recommended_actions"][0]["text"] == "Restart session-worker-2"


def test_threshold_fallback_alert_still_explains_itself():
    out = json.loads(composers.risk_explanation({"service": "s", "risk_score": 65, "level": "warning", "degraded": True, "signals": {}}))
    assert "threshold-based" in out["alert_text"]


def test_reports_have_all_sections_and_export_formats():
    ctx = {"window": {"start": "2026-01-01T10:00:00+00:00", "end": "2026-01-01T10:30:00+00:00", "duration_min": 30},
           "services": [{"service": "checkout", "errors": 100, "peak_per_min": 20, "first_error": "2026-01-01T10:05:00+00:00", "top_errors": [{"message": "boom", "count": 9}]}],
           "impact": {"total_errors": 100, "services_affected": 1, "peak_error_rate": 0.4}, "rca": {"causal_chain": [{"service": "redis"}], "confidence": 0.8, "explanation": "redis first"}}
    secs = json.loads(composers.incident_report(ctx))["sections"]
    assert [s["key"] for s in secs] == ["summary", "timeline", "affected_services", "impact_analysis", "root_cause", "resolution", "preventive_actions"]
    rep = {"title": "Incident report: checkout", "kind": "incident", "status": "draft", "created_at": "2026-01-01T10:40:00", "sections": secs}
    assert "## Root Cause" in to_markdown(rep) and "DRAFT" in to_markdown(rep)
    assert to_pdf(rep)[:4] == b"%PDF"
    pm = json.loads(composers.pre_mortem({"service": "checkout", "alert": {"risk_score": 85, "level": "critical"}, "eta": {"low": 8, "high": 14}}))["sections"]
    assert [s["key"] for s in pm][:2] == ["summary", "what_is_about_to_happen"] and len(pm) == 7


def test_confidence_reflects_evidence():
    strong = [{"score": 0.9}, {"score": 0.8}, {"score": 0.7}]
    assert confidence_of(strong, {}, [])[1] == "high" or confidence_of(strong, {}, [])[0] >= 0.7
    assert confidence_of([], {}, [])[1] == "low"
    assert confidence_of([], {"risk": {"services": []}}, [])[0] >= 0.7
    assert followups("x", {"rca": {"causal_chain": []}}, [])[0].startswith("Generate")


def test_embed_retries_with_input_type_for_asymmetric_nim_models(monkeypatch):
    """NVIDIA's nv-embedqa-*/e5-* NIMs reject a plain OpenAI embeddings payload and demand an
    `input_type` field; the client should retry once with it added rather than fail outright."""
    monkeypatch.setattr(settings, "ai_base_url", "https://integrate.api.nvidia.com/v1")
    monkeypatch.setattr(settings, "ai_egress_allowlist", "integrate.api.nvidia.com")
    monkeypatch.setattr(settings, "ai_embedding_model", "nvidia/nv-embedqa-e5-v5")
    monkeypatch.setattr(settings, "embedding_dim", 3)
    provider = OpenAICompatibleProvider()

    calls = []

    class FakeResp:
        def __init__(self, status, body):
            self.status_code = status
            self._body = body
            self.text = json.dumps(body)
            self.request = None

        def raise_for_status(self):
            if self.status_code >= 400:
                import httpx

                raise httpx.HTTPStatusError("err", request=None, response=self)

        def json(self):
            return self._body

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        if "input_type" not in json:
            return FakeResp(422, {"detail": [{"loc": ["body", "input_type"], "msg": "field required"}]})
        return FakeResp(200, {"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]})

    monkeypatch.setattr("app.providers.openai_compatible.httpx.post", fake_post)
    vecs = provider.embed(["hello"])
    assert vecs == [[0.1, 0.2, 0.3]]
    assert len(calls) == 2 and "input_type" not in calls[0] and calls[1]["input_type"] == "passage"


def test_symmetric_embedding_models_never_need_a_retry(monkeypatch):
    """bge-m3 (the default NIM embedding choice) answers a plain OpenAI payload directly."""
    monkeypatch.setattr(settings, "ai_base_url", "https://integrate.api.nvidia.com/v1")
    monkeypatch.setattr(settings, "ai_egress_allowlist", "integrate.api.nvidia.com")
    monkeypatch.setattr(settings, "ai_embedding_model", "baai/bge-m3")
    monkeypatch.setattr(settings, "embedding_dim", 3)
    provider = OpenAICompatibleProvider()
    calls = []

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"index": 0, "embedding": [0.4, 0.5, 0.6]}]}

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        return FakeResp()

    monkeypatch.setattr("app.providers.openai_compatible.httpx.post", fake_post)
    assert provider.embed(["hello"]) == [[0.4, 0.5, 0.6]]
    assert len(calls) == 1


def test_search_filters_build_parameterised_sql():
    params: dict = {}
    sql = _where(Filters(services=["a"], severity=["ERROR"], trace_id="t'; DROP TABLE x;--"), params)
    assert "DROP" not in sql and params["f_trace"].startswith("t'") and "service = ANY(:f_svc)" in sql
