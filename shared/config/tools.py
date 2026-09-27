"""The agent's tool set (PRD Section 5) with default autonomy tiers (Section 8.2).

This registry is the single source of truth for: the tool router, the seeded
`autonomy_policy` rows, the Settings - Autonomy & Policy screen, and audit records.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum


class Tier(StrEnum):
    READ_ONLY = "read_only"
    AUTONOMOUS_BACKGROUND = "autonomous_background"
    AUTONOMOUS_POLICY_BOUNDED = "autonomous_policy_bounded"
    PROPOSE_ONLY = "propose_only"
    AUTONOMOUS_EXECUTION = "autonomous_execution"  # opt-in, future scope (PRD 14)


# Ordering used when the router downgrades an action outside the granted tier.
TIER_ORDER = [
    Tier.READ_ONLY,
    Tier.PROPOSE_ONLY,
    Tier.AUTONOMOUS_POLICY_BOUNDED,
    Tier.AUTONOMOUS_BACKGROUND,
    Tier.AUTONOMOUS_EXECUTION,
]


@dataclass(frozen=True)
class ToolSpec:
    number: str
    name: str  # stable id used in agent_actions.tool_name / autonomy_policy.tool_name
    title: str
    default_tier: Tier
    description: str
    human_review: str = ""  # "human sign-off" / "human-reviewed" qualifiers from the PRD
    flagship: bool = False


TOOLS: list[ToolSpec] = [
    ToolSpec("01", "log_ingestion", "Log Ingestion Tool", Tier.AUTONOMOUS_BACKGROUND,
             "Multi-format ingestion (.log .txt .json .csv .zip .gz up to 500MB), API ingestion and real-time streaming."),
    ToolSpec("02", "log_parsing", "Log Parsing Tool", Tier.AUTONOMOUS_BACKGROUND,
             "Normalizes unstructured logs into structured records; malformed records stored separately."),
    ToolSpec("03", "pii_redaction", "PII Redaction Tool", Tier.AUTONOMOUS_BACKGROUND,
             "Mandatory redaction before storage or any cloud AI call. Not policy-configurable."),
    ToolSpec("04", "structured_storage", "Structured Storage & Indexing Tool", Tier.AUTONOMOUS_BACKGROUND,
             "PostgreSQL/time-series storage, full-text index, date partitioning, vector index."),
    ToolSpec("05", "log_search", "Log Search Tool", Tier.READ_ONLY,
             "Keyword (regex) and semantic search with filters, sources and context windows."),
    ToolSpec("06", "error_deduplication", "Error Deduplication Tool", Tier.AUTONOMOUS_BACKGROUND,
             "Semantic-similarity grouping of repeated errors with counts and first/last seen."),
    ToolSpec("07", "error_clustering", "Error Clustering Tool", Tier.AUTONOMOUS_BACKGROUND,
             "Density-based clustering of error embeddings; auto-labelled, confidence-scored, tracked over time."),
    ToolSpec("08", "health_state", "Analytics / Health-State Tool", Tier.READ_ONLY,
             "Continuously updated per-service health state, queryable on demand."),
    ToolSpec("09", "conversational_chat", "Conversational Chat Tool", Tier.READ_ONLY,
             "Primary human interface: retrieval-grounded answers with sources and confidence."),
    ToolSpec("10", "root_cause_analysis", "Root Cause Analysis Tool", Tier.AUTONOMOUS_POLICY_BOUNDED,
             "Causal-chain analysis across services with confidence and evidence.", "human-reviewed output"),
    ToolSpec("11", "incident_report_generation", "Incident Report Generation Tool", Tier.AUTONOMOUS_POLICY_BOUNDED,
             "Drafts structured incident reports; editable, exportable as PDF/Markdown.", "human sign-off"),
    ToolSpec("12", "deployment_comparison", "Deployment Comparison Tool", Tier.AUTONOMOUS_POLICY_BOUNDED,
             "Side-by-side comparison of deployment versions; auto-flags regressions on deploy events."),
    ToolSpec("13", "anomaly_detection", "Anomaly Detection Tool", Tier.AUTONOMOUS_BACKGROUND,
             "Statistical baseline + ML outlier detection with natural-language explanations."),
    ToolSpec("14", "proactive_failure_forecasting", "Proactive Failure Forecasting Tool (FLAGSHIP)",
             Tier.AUTONOMOUS_POLICY_BOUNDED,
             "Continuous loop computing failure risk scores, pre-incident alerts and pre-mortems.", flagship=True),
    # Sub-capabilities of the flagship (PRD 4.3) that carry their own autonomy level.
    ToolSpec("14a", "pre_incident_alerts", "Pre-Incident Alerts", Tier.AUTONOMOUS_POLICY_BOUNDED,
             "Threshold-triggered alerts with natural-language explanation."),
    ToolSpec("14b", "recommended_actions", "Recommended Actions", Tier.PROPOSE_ONLY,
             "Ranked remediation steps; autonomous execution requires an explicit policy grant."),
    ToolSpec("14c", "pre_mortem_reports", "Pre-Mortem Reports", Tier.AUTONOMOUS_POLICY_BOUNDED,
             "Drafted automatically at the critical threshold; human sign-off before export.", "human sign-off"),
    ToolSpec("14d", "feedback_learning", "Feedback Loop & Self-Improvement", Tier.AUTONOMOUS_BACKGROUND,
             "Refines pattern-matching weights and leading-indicator signatures from human-labelled outcomes."),
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}

ENVIRONMENT_SCOPES = ["*", "production", "staging", "dev"]


def tool_dicts() -> list[dict]:
    return [{**asdict(t), "default_tier": t.default_tier.value} for t in TOOLS]
