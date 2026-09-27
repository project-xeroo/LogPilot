import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/endpoints";
import { Chip, ErrorNote, Skeleton } from "@/components/shared/ui";
import { useProjectId } from "@/hooks/useProject";
import type { MetricRow } from "@/types";

const fmt = (m: MetricRow) => {
  if (m.value == null) return "No data yet";
  if (m.unit === "ratio") return `${(m.value * 100).toFixed(1)}%`;
  if (m.unit === "score") return m.value.toFixed(2);
  if (m.unit === "records/min") return Math.round(m.value).toLocaleString();
  if (m.unit === "tokens/1k events") return Math.round(m.value).toLocaleString();
  return `${m.value.toFixed(m.value < 10 ? 2 : 1)} ${m.unit}`;
};

function Group({ title, rows }: { title: string; rows: MetricRow[] }) {
  return (
    <section className="rounded-panel border border-line bg-surface p-4">
      <h3 className="mb-1 text-base">{title}</h3>
      <ul className="hairlines">{rows.map((m) => (
        <li key={m.metric} className="grid items-baseline gap-x-4 gap-y-0.5 py-2.5 sm:grid-cols-[1.4fr_9rem_1fr_7rem]">
          <div><div className="text-sm font-medium">{m.metric}</div>{m.detail && <div className="text-xs text-muted">{m.detail}</div>}</div>
          <div className="tabular text-sm font-semibold">{fmt(m)}</div>
          <div className="text-sm text-muted">Target {m.target}</div>
          <div><Chip tone={m.status === "meets" ? "go" : m.status === "misses" ? "amber" : "neutral"}>{m.status === "meets" ? "Meets target" : m.status === "misses" ? "Below target" : "No data"}</Chip></div>
        </li>))}</ul>
    </section>
  );
}

/** Success metrics: user-facing, technical, and cloud cost. */
export function MetricsPanel() {
  const projectId = useProjectId();
  const q = useQuery({ queryKey: ["metrics", projectId], queryFn: () => api.metrics(projectId), enabled: !!projectId, refetchInterval: 60_000 });
  if (q.isLoading) return <Skeleton className="h-96" />;
  if (q.error) return <ErrorNote error={q.error} />;
  const m = q.data!;
  return (
    <div className="space-y-5">
      <p className="text-sm text-muted">Measured over the last {m.window_days} days. Metrics that depend on your feedback (alert accuracy, prevention rate, chat ratings) fill in as your team logs outcomes and rates answers.</p>
      <Group title="What your team experiences" rows={m.user_facing} />
      <Group title="How the system performs" rows={m.technical} />
      <Group title="Cloud AI cost" rows={m.cloud_cost} />
    </div>
  );
}
