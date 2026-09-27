import { useQuery } from "@tanstack/react-query";
import { GitCompareArrows } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "@/api/endpoints";
import { Term } from "@/components/glossary/Term";
import { AskAgent } from "@/components/shared/AskAgent";
import { Markdown } from "@/components/shared/Markdown";
import { Button, Chip, EmptyState, ErrorNote, Label, PageHeader, Select, Skeleton } from "@/components/shared/ui";
import { useProjectId } from "@/hooks/useProject";
import type { Comparison, VersionStats } from "@/types";
import { fmtDateTime, num, pct } from "@/utils/format";

function Side({ v, tone }: { v: VersionStats; tone: "before" | "after" }) {
  return (
    <div className="rounded-panel border border-line bg-surface p-4">
      <div className="flex items-baseline gap-2"><h3 className="text-lg">{v.version}</h3><span className="text-sm text-muted">{tone === "before" ? "before" : "after"}</span></div>
      <p className="text-xs text-muted">{fmtDateTime(v.first_seen)} to {fmtDateTime(v.last_seen)}</p>
      <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
        <div><dt className="text-muted"><Term term="error rate">Error rate</Term></dt><dd className="tabular text-lg font-semibold">{pct(v.error_rate, 2)}</dd></div>
        <div><dt className="text-muted">Errors per minute</dt><dd className="tabular text-lg font-semibold">{v.errors_per_min.toFixed(2)}</dd></div>
        <div><dt className="text-muted"><Term term="p95 latency">p95 latency</Term></dt><dd className="tabular text-lg font-semibold">{v.latency_p95_ms ? `${Math.round(v.latency_p95_ms)} ms` : "n/a"}</dd></div>
        <div><dt className="text-muted">Log records</dt><dd className="tabular text-lg font-semibold">{num(v.records)}</dd></div>
      </dl>
    </div>
  );
}

function Delta({ c }: { c: Comparison }) {
  const rows = [
    { m: "Error rate", before: c.from.error_rate * 100, after: c.to.error_rate * 100 },
    { m: "Errors / min", before: c.from.errors_per_min, after: c.to.errors_per_min },
  ];
  return (
    <div className="h-44" role="img" aria-label={`Error rate went from ${pct(c.from.error_rate, 2)} to ${pct(c.to.error_rate, 2)}`}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 6, right: 8, left: -12, bottom: 0 }}>
          <CartesianGrid stroke="hsl(var(--line))" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="m" tick={{ fontSize: 12, fill: "hsl(var(--muted))" }} />
          <YAxis tick={{ fontSize: 11, fill: "hsl(var(--muted))" }} />
          <Tooltip contentStyle={{ fontSize: 12, borderRadius: 6, background: "hsl(var(--raised))", border: "1px solid hsl(var(--line))" }} />
          <Bar dataKey="before" name={c.from.version} fill="hsl(var(--chart) / .55)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
          <Bar dataKey="after" name={c.to.version} fill={c.regression ? "hsl(var(--signal))" : "hsl(var(--teal))"} radius={[3, 3, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Deployment Comparison: side-by-side regression view between two versions of a service. */
export default function DeploymentsPage() {
  const projectId = useProjectId();
  const versions = useQuery({ queryKey: ["deployments", projectId], queryFn: () => api.deployments.versions(projectId), enabled: !!projectId });
  const auto = useQuery({ queryKey: ["deployments", projectId, "auto"], queryFn: () => api.deployments.auto(projectId), enabled: !!projectId, refetchInterval: 30_000 });
  const byService = useMemo(() => {
    const m = new Map<string, string[]>();
    versions.data?.forEach((v) => m.set(v.service, [...(m.get(v.service) ?? []), v.version]));
    return m;
  }, [versions.data]);
  const [service, setService] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [req, setReq] = useState<{ service: string; from_version: string; to_version: string } | null>(null);
  const multi = [...byService.entries()].filter(([, v]) => v.length > 1);

  useEffect(() => { if (!service && multi.length) setService(multi[0][0]); }, [multi, service]);
  useEffect(() => {
    const vs = byService.get(service);
    if (vs && vs.length > 1) { setTo(vs[0]); setFrom(vs[1]); }
  }, [service, byService]);

  const cmp = useQuery({ queryKey: ["compare", projectId, req], queryFn: () => api.deployments.compare(projectId, req!), enabled: !!req });
  const c = cmp.data;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <PageHeader title="Deployments">Compare two versions of a service to see what a release changed. When the agent spots a new version in your logs it compares it with the previous one on its own and flags regressions in the feed.</PageHeader>
      {versions.isLoading && <Skeleton className="h-24" />}
      {versions.data && multi.length === 0 && <EmptyState icon={<GitCompareArrows />} title="Nothing to compare yet">The agent needs logs from at least two versions of a service. Logs that include a version field (or version=... text) are picked up automatically.</EmptyState>}
      {multi.length > 0 && (
        <form onSubmit={(e) => { e.preventDefault(); setReq({ service, from_version: from, to_version: to }); }} className="flex flex-wrap items-end gap-3 rounded-panel border border-line bg-surface p-4">
          <div><Label htmlFor="d-svc">Service</Label><Select id="d-svc" value={service} onChange={(e) => setService(e.target.value)}>{multi.map(([s]) => <option key={s}>{s}</option>)}</Select></div>
          <div><Label htmlFor="d-from">Before</Label><Select id="d-from" value={from} onChange={(e) => setFrom(e.target.value)}>{byService.get(service)?.map((v) => <option key={v}>{v}</option>)}</Select></div>
          <div><Label htmlFor="d-to">After</Label><Select id="d-to" value={to} onChange={(e) => setTo(e.target.value)}>{byService.get(service)?.map((v) => <option key={v}>{v}</option>)}</Select></div>
          <Button type="submit" variant="primary" disabled={!from || !to || from === to} loading={cmp.isFetching}>Compare versions</Button>
        </form>
      )}
      <ErrorNote error={cmp.error} />
      {c && (
        <section aria-label="Comparison result" className="space-y-4">
          <div className={c.regression ? "rounded-panel border border-signal bg-signal-soft/50 p-4" : "rounded-panel border border-line bg-go-soft/60 p-4"}>
            <div className="flex flex-wrap items-center gap-2"><Chip tone={c.regression ? "signal" : "go"}>{c.regression ? "Regression detected" : "No regression"}</Chip><span className="font-medium">{c.service}</span><AskAgent prompt={`Did the latest deployment of ${c.service} cause any regressions?`} label="Ask the agent" className="ml-auto" /></div>
            <p className="agent-voice mt-1.5">{c.regression ? `${c.to.version} is worse than ${c.from.version}: ${c.regression_reasons.join("; ")}.` : `${c.to.version} behaves like ${c.from.version}. Error rate moved from ${pct(c.from.error_rate, 2)} to ${pct(c.to.error_rate, 2)}.`}</p>
          </div>
          <div className="grid gap-4 md:grid-cols-2"><Side v={c.from} tone="before" /><Side v={c.to} tone="after" /></div>
          <div className="rounded-panel border border-line bg-surface p-4"><h3 className="mb-2 text-base">Change at a glance</h3><Delta c={c} /></div>
          <div className="grid gap-4 md:grid-cols-2">
            <section className="rounded-panel border border-line bg-surface p-4"><h3 className="mb-1 text-base">New error types in {c.to.version}</h3>
              {c.new_error_types.length ? <ul className="hairlines text-sm">{c.new_error_types.map((n) => <li key={n.sample} className="py-2"><span className="tabular mr-2 font-semibold">{n.count}</span><span className="break-words font-mono text-[12.5px] text-muted">{n.sample.slice(0, 200)}</span></li>)}</ul> : <p className="text-sm text-muted">None. No error appears that {c.from.version} didn't already have.</p>}</section>
            <section className="rounded-panel border border-line bg-surface p-4"><h3 className="mb-1 text-base">Errors that got worse or went away</h3>
              {c.increased_errors.length || c.resolved_error_types.length ? <ul className="hairlines text-sm">
                {c.increased_errors.map((n) => <li key={n.sample} className="py-2"><Chip tone="amber">more frequent</Chip> <span className="tabular text-muted">{n.before_per_min}/min to {n.after_per_min}/min</span><p className="break-words font-mono text-[12.5px] text-muted">{n.sample.slice(0, 160)}</p></li>)}
                {c.resolved_error_types.map((n) => <li key={n.sample} className="py-2"><Chip tone="go">gone</Chip><p className="break-words font-mono text-[12.5px] text-muted">{n.sample.slice(0, 160)}</p></li>)}</ul> : <p className="text-sm text-muted">No notable changes.</p>}</section>
          </div>
        </section>
      )}

      <section aria-label="Comparisons the agent ran">
        <h2 className="mb-2 text-xl">Flagged automatically</h2>
        {auto.data?.length ? (
          <ul className="hairlines rounded-panel border border-line bg-surface px-4">{auto.data.map((a) => (
            <li key={a.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-3">
              <span className="font-medium">{a.service}</span><span className="text-sm text-muted">{a.from_version} to {a.to_version}</span><Chip tone={a.regression ? "signal" : "go"}>{a.regression ? "Regression" : "Clean"}</Chip><Chip>{a.trigger === "deploy_event" ? "on deploy" : "on request"}</Chip>
              <time className="ml-auto text-xs text-muted" dateTime={a.created_at}>{fmtDateTime(a.created_at)}</time>
              {a.result.narrative && <Markdown className="basis-full text-[14.5px]">{a.result.narrative}</Markdown>}
            </li>))}</ul>
        ) : <p className="text-sm text-muted">When a new version shows up in your logs, the agent's comparison appears here.</p>}
      </section>
    </div>
  );
}
