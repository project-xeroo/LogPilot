export type Role = "admin" | "developer" | "junior_engineer" | "sre" | "viewer";
export type Level = "ok" | "warning" | "critical";

export interface ProjectRef { id: string; name: string; environment: string }
export interface User {
  id: string; email: string; name: string; role: Role; guided_mode: boolean; org_id: string;
  permissions: string[]; cross_project: boolean; projects: ProjectRef[];
}

export interface FeedItem {
  id: string; kind: string; title: string; body: string | null; severity: "info" | "warning" | "critical";
  ref_type: string | null; ref_id: string | null; service: string | null; created_at: string; meta?: Record<string, unknown> | null;
}

export interface RiskRow {
  service_id: string; service: string; environment: string; enabled: boolean; interval_seconds: number;
  risk_score: number | null; velocity_score: number | null; similarity_score: number | null; baseline_score: number | null;
  trend: "rising" | "falling" | "steady" | null; eta_minutes_low: number | null; eta_minutes_high: number | null;
  as_of: string | null; degraded: boolean; level: Level | null; primary_signals: string[]; open_alert_id: string | null;
  thresholds: { warning: number; critical: number };
}

export interface RiskDetail {
  service_id: string; service: string; risk_score: number; velocity_score: number; similarity_score: number; baseline_score: number;
  trend: string; eta_minutes_low: number | null; eta_minutes_high: number | null; as_of: string; degraded: boolean; cycle_ms: number | null;
  signals: {
    velocity: { errors_per_min: number; baseline_per_min: number; ratio: number; velocity: number; acceleration: number; growth_rate: number; series: { t: string; errors_per_min: number; smoothed: number }[] };
    baseline: { z: number; ratio: number; expected_per_min: number; pattern_divergence: number; window: string; available: boolean };
    similarity: { best_match: number; best_label: string | null; matches: SimilarIncident[]; signatures_known: number };
    drift: { score: number; cluster_shift: number; drifting_variants: { template: string; similarity_to_known: number }[] };
    top_errors: { message: string; count: number }[];
    thresholds: { warning: number; critical: number };
  };
  weights: { velocity: number; similarity: number; baseline: number; signature_threshold: number; feedback_samples: number; true_positives: number; false_positives: number };
  alert: { id: string; level: string; status: string; text: string } | null;
}
export interface SimilarIncident { label: string; incident_start: string; similarity: number; resolved_actions: string[] }
export interface RiskPoint { t: string; risk: number; velocity: number; similarity: number; baseline: number }

export interface Action {
  id: string; alert_id: string; service: string; rank: number; text: string; original_text: string; rationale: string | null;
  source: "historical" | "llm" | "playbook"; risk_level: "low" | "high"; autonomy_tier: string;
  status: "proposed" | "approved" | "edited" | "dismissed" | "executed"; decided_at: string | null; dismiss_reason: string | null;
}
export interface Alert {
  id: string; service_id: string; service: string; level: "warning" | "critical"; status: string; risk_score: number; peak_risk_score: number;
  failure_probability: number | null; text: string; eta_minutes_low: number | null; eta_minutes_high: number | null; degraded: boolean;
  created_at: string; updated_at: string; resolved_at: string | null; pre_mortem_report_id: string | null; pattern_matches: SimilarIncident[] | null;
  evidence_chain?: { top_errors?: { message: string; count: number }[]; similar_incidents?: SimilarIncident[]; drivers?: { signal: string; score: number; weight: number; contribution: number }[]; explanation?: string; rca?: { chain: string[]; confidence: number; explanation: string } | null };
  actions?: Action[];
  outcomes?: { id: string; outcome: string; alert_accurate: boolean; action_taken: string | null; time_to_resolve: number | null; notes: string | null; learned: boolean; created_at: string }[];
}
export interface Approvals {
  pending_alerts: Alert[];
  actions: (Action & { alert: { id: string; level: string; risk_score: number; text: string } })[];
  viewer_can_decide: boolean; viewer_guided: boolean; note: string | null;
}

export interface ServiceHealth {
  service: string; status: "healthy" | "degraded" | "unhealthy" | "silent"; records: number; errors: number; warnings: number; fatal: number;
  error_rate: number; errors_per_min: number; previous_error_rate: number; trend: "rising" | "falling" | "steady"; p95_latency_ms: number | null;
  last_seen: string | null; risk: { risk_score: number; trend: string } | null;
}
export interface HealthState {
  as_of: string; window_minutes: number; services: ServiceHealth[]; top_problematic_services: string[];
  severity_distribution: Record<string, number>;
  error_timeline: { t: string; total: number; errors: number; warnings: number }[];
  trending_issues: { message: string; count: number; previous_count: number; growth: number; cluster: string | null }[];
}
export interface Cluster { id: string; label: string; confidence: number; member_count: number; occurrences: number; services: string[]; last_seen: string | null; drift_score: number }
export interface Anomaly { id: string; kind: string; service: string; severity: string; window_start: string; window_end: string; explanation: string }

export interface SourceRef { n: number; id: string; service: string; severity: string; timestamp: string; message: string; count: number | null; score: number; source?: { filename: string | null; line_no: number | null } }
export interface Card { type: "risk" | "health" | "rca" | "search" | "deployment" | "report" | "explain"; data: any }
export interface ChatMsg {
  id: string; role: "user" | "agent"; content: string; sources?: SourceRef[] | null; cards?: Card[] | null; confidence?: number | null;
  confidence_label?: "high" | "medium" | "low" | null; rating?: number | null; followups?: string[] | null; tools?: { tool: string; ms?: number; ok: boolean }[];
  pending?: boolean; created_at?: string; error?: boolean;
  status?: "thinking" | "done" | "error"; steps?: string[]; // live progress while the agent is still working
}

export interface ChatThreadSummary { id: string; title: string; updated_at: string; created_at?: string }

export interface ApiKeyRow {
  id: string; name: string; key_prefix: string; scopes: string[]; created_at: string; last_used_at: string | null; revoked_at: string | null;
}
export interface CreatedApiKey extends ApiKeyRow { key: string }

export interface SearchResult {
  id: string; timestamp: string; service: string; severity: string; message: string; request_id: string | null; trace_id: string | null;
  environment: string | null; deployment_version: string | null; source: { session_id: string | null; line_no: number | null; filename: string | null };
  highlights?: number[][]; score?: number; occurrences?: number | null; template?: string;
  context?: { before: SearchResult[]; after: SearchResult[] };
}
export interface SearchResponse { mode: string; query: string; count: number; total: number; total_capped?: boolean; results: SearchResult[]; took_ms: number }

export interface ReportSection { key: string; title: string; body: string }
export interface Report {
  id: string; title: string; kind: "incident" | "pre_mortem" | "executive_summary"; status: "draft" | "approved" | "discarded";
  created_at: string; updated_at: string; generation_ms: number | null; model: string | null; approved_at: string | null; alert_id: string | null;
  sections?: ReportSection[]; ai_narrated?: boolean;
}

export interface DeploymentVersion { service: string; version: string; environment: string; deployed_at: string }
export interface VersionStats { version: string; records: number; errors: number; error_rate: number; errors_per_min: number; latency_p95_ms: number | null; latency_mean_ms: number | null; first_seen: string; last_seen: string }
export interface Comparison {
  service: string; from: VersionStats; to: VersionStats;
  deltas: { error_rate: number; error_rate_ratio: number | null; errors_per_min: number; latency_p95_ms: number | null; latency_p95_ratio: number | null };
  new_error_types: { count: number; sample: string }[]; resolved_error_types: { count: number; sample: string }[]; increased_errors: { sample: string; before_per_min: number; after_per_min: number }[];
  health: { from: string; to: string }; regression: boolean; regression_reasons: string[]; narrative?: string;
}

export interface LogSession {
  id: string; filename: string; status: string; stage: string | null; progress_pct: number; record_count: number; malformed_count: number;
  size_bytes: number; source: string; format_detected: string | null; upload_time: string; validation_errors: string[] | null; error_message: string | null;
  redaction_counts: { by_type: Record<string, number>; total: number } | null;
}

export interface PolicyRow { scope: string; tier: string; min_confidence: number; requires_approval: boolean; enabled: boolean }
export interface ToolPolicy {
  number: string; name: string; title: string; description: string; default_tier: string; human_review: string; flagship: boolean; mandatory: boolean; editable: boolean; policies: PolicyRow[];
}
export interface PolicyResponse { tiers: { tier: string; label: string; definition: string; selectable: boolean }[]; scopes: string[]; tools: ToolPolicy[] }

export interface ManagedUser {
  id: string; email: string; name: string; role: Role; guided_mode: boolean; is_active: boolean; created_at: string; last_login_at: string | null; promoted_at: string | null; project_ids: string[];
}
export interface GlossaryTerm { id: string; term: string; definition: string; category: string }
export interface MonitoredService { id: string; name: string; environment: string; enabled: boolean; forecast_interval_seconds: number; warning_threshold: number | null; critical_threshold: number | null; playbook: { steps: string[] } | null }
export interface MetricRow { metric: string; value: number | null; unit: string; target: string; status: "meets" | "misses" | "no_data"; detail: string | null }
export interface LiveEvent { type: string; project_id: string | null; interrupt?: boolean; ts?: string; payload?: any; [k: string]: any }
