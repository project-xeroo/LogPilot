import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "@/api/endpoints";
import { Term } from "@/components/glossary/Term";
import { AskAgent } from "@/components/shared/AskAgent";
import { Chip, ErrorNote, Select, Skeleton } from "@/components/shared/ui";
import { useProjectId } from "@/hooks/useProject";
import { fmtTime, num, pct, timeAgo, titleCase } from "@/utils/format";

const tone = { healthy: "go", degraded: "amber", unhealthy: "signal", silent: "neutral" } as const;
const SEV_COLOR: Record<string, string> = { FATAL: "hsl(var(--signal))", ERROR: "hsl(var(--signal) / .7)", WARN: "hsl(var(--amber))", INFO: "hsl(var(--chart) / .5)", DEBUG: "hsl(var(--line))" };
const ANOMALY_WINDOWS: [number, string][] = [[1, "Last hour"], [6, "Last 6 hours"], [24, "Last 24 hours"], [72, "Last 3 days"], [168, "Last 7 days"]];
const anomalyTone = { high: "signal", medium: "amber", low: "neutral" } as const;

/** The agent's health state per service, queried on demand rather than rendered as a fixed dashboard. */
export function HealthPanel() {
  const projectId = useProjectId();
  const [win, setWin] = useState(60);
  const health = useQuery({ queryKey: ["health", projectId, win], queryFn: () => api.health(projectId, { window_minutes: win }), refetchInterval: 20_000, enabled: !!projectId });
  const clusters = useQuery({ queryKey: ["clusters", projectId], queryFn: () => api.clusters(projectId), enabled: !!projectId, refetchInterval: 60_000 });

  const [anomWindow, setAnomWindow] = useState(24);
  const [anomKind, setAnomKind] = useState("");
  const [anomSeverity, setAnomSeverity] = useState("");
  const [anomService, setAnomService] = useState("");
  const anomalies = useQuery({ queryKey: ["anomalies", projectId, anomWindow], queryFn: () => api.anomalies(projectId, anomWindow), enabled: !!projectId, refetchInterval: 60_000 });
  const anomKinds = useMemo(() => [...new Set((anomalies.data ?? []).map((a) => a.kind))].sort(), [anomalies.data]);
  const anomServices = useMemo(() => [...new Set((anomalies.data ?? []).map((a) => a.service))].sort(), [anomalies.data]);
  const filteredAnomalies = useMemo(
    () => (anomalies.data ?? []).filter((a) => (!anomKind || a.kind === anomKind) && (!anomSeverity || a.severity === anomSeverity) && (!anomService || a.service === anomService)),
    [anomalies.data, anomKind, anomSeverity, anomService],
  );

  if (health.isLoading) return <Skeleton className="h-80" />;
  if (health.error) return <ErrorNote error={health.error} />;
  const h = health.data!;
  const timeline = h.error_timeline.map((p) => ({ t: fmtTime(p.t), errors: p.errors, warnings: p.warnings }));
  const sevTotal = Object.values(h.severity_distribution).reduce((a, b) => a + b, 0) || 1;
  const sev = ["FATAL", "ERROR", "WARN", "INFO", "DEBUG"].filter((k) => h.severity_distribution[k]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h2 className="text-xl">Health state</h2><p className="text-sm text-muted">Updated {timeAgo(h.as_of)}. Everything here can be asked about in chat.</p></div>
        <label className="text-sm"><span className="mr-2 text-muted">Window</span>
          <Select value={win} onChange={(e) => setWin(Number(e.target.value))} className="inline-block w-auto" aria-label="Time window">
            {[[15, "Last 15 minutes"], [60, "Last hour"], [360, "Last 6 hours"], [1440, "Last 24 hours"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </Select>
        </label>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1.6fr_1fr]">
        <section className="rounded-panel border border-line bg-surface p-4">
          <div className="mb-2 flex items-center justify-between"><h3 className="text-base"><Term term="error rate">Error timeline</Term></h3><AskAgent prompt={`Why did errors change in the last ${win >= 1440 ? "24 hours" : win + " minutes"}?`} label="Why?" /></div>
          <div className="h-48" role="img" aria-label="Errors and warnings over time">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={timeline} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid stroke="hsl(var(--line))" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="t" tick={{ fontSize: 11, fill: "hsl(var(--muted))" }} minTickGap={36} />
                <YAxis tick={{ fontSize: 11, fill: "hsl(var(--muted))" }} allowDecimals={false} />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 6, background: "hsl(var(--raised))", border: "1px solid hsl(var(--line))" }} />
                <Area type="monotone" dataKey="warnings" stackId="1" stroke="hsl(var(--amber))" fill="hsl(var(--amber))" fillOpacity={0.2} name="Warnings" isAnimationActive={false} />
                <Area type="monotone" dataKey="errors" stackId="1" stroke="hsl(var(--signal))" fill="hsl(var(--signal))" fillOpacity={0.25} name="Errors" isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </section>
        <section className="rounded-panel border border-line bg-surface p-4">
          <h3 className="mb-3 text-base"><Term term="severity">Severity mix</Term></h3>
          <div className="flex h-3 overflow-hidden rounded-full" role="img" aria-label={`Severity distribution: ${sev.map((k) => `${k} ${pct(h.severity_distribution[k] / sevTotal, 1)}`).join(", ")}`}>
            {sev.map((k) => <div key={k} style={{ width: `${(h.severity_distribution[k] / sevTotal) * 100}%`, background: SEV_COLOR[k] }} />)}
          </div>
          <ul className="mt-3 space-y-1 text-sm">{sev.map((k) => <li key={k} className="flex items-center gap-2"><span className="size-2.5 rounded-sm" style={{ background: SEV_COLOR[k] }} aria-hidden /><span className="w-14">{k}</span><span className="tabular ml-auto text-muted">{num(h.severity_distribution[k])}</span></li>)}</ul>
        </section>
      </div>

      <section className="rounded-panel border border-line bg-surface">
        <div className="flex items-center justify-between px-4 pt-3"><h3 className="text-base">Services</h3><span className="text-sm text-muted">Most errors first</span></div>
        <div className="overflow-x-auto px-4 pb-2">
          <table className="w-full min-w-[34rem] text-sm">
            <thead><tr className="text-left text-muted"><th className="py-2 pr-3 font-medium">Service</th><th className="px-3 font-medium">Status</th><th className="px-3 text-right font-medium">Errors</th><th className="px-3 text-right font-medium"><Term term="error rate">Error rate</Term></th><th className="px-3 text-right font-medium"><Term term="p95 latency">p95 latency</Term></th><th className="w-10" /></tr></thead>
            <tbody className="hairlines">
              {h.services.map((s) => (
                <tr key={s.service} className="[&>td]:py-2">
                  <td className="pr-3 font-medium">{s.service}</td>
                  <td className="px-3"><Chip tone={tone[s.status]}>{s.status}</Chip></td>
                  <td className="tabular px-3 text-right">{num(s.errors)}</td>
                  <td className="tabular px-3 text-right">{pct(s.error_rate, 1)}</td>
                  <td className="tabular px-3 text-right">{s.p95_latency_ms ? `${Math.round(s.p95_latency_ms)} ms` : "n/a"}</td>
                  <td><AskAgent prompt={`Why is ${s.service} ${s.status === "healthy" ? "healthy" : s.status}?`} label="Ask why" iconOnly /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded-panel border border-line bg-surface p-4">
          <h3 className="mb-1 text-base">Trending issues</h3>
          {h.trending_issues.length ? (
            <ul className="hairlines text-sm">{h.trending_issues.slice(0, 5).map((t) => (
              <li key={t.message} className="py-2"><div className="flex items-baseline gap-2"><span className="tabular font-semibold">{t.count}</span><span className="text-muted">was {t.previous_count}</span>{t.cluster && <Chip tone="teal" className="ml-auto">{t.cluster}</Chip>}</div><p className="break-words font-mono text-[12.5px] text-muted">{t.message}</p></li>))}</ul>
          ) : <p className="text-sm text-muted">No error type is growing in this window.</p>}
        </section>
        <section className="rounded-panel border border-line bg-surface p-4">
          <h3 className="mb-1 text-base"><Term term="error cluster">Error clusters</Term></h3>
          {clusters.data?.length ? (
            <ul className="hairlines text-sm">{clusters.data.slice(0, 6).map((c) => (
              <li key={c.id} className="flex items-baseline gap-2 py-2"><span className="font-medium">{c.label}</span><span className="text-muted">{c.services.join(", ")}</span><span className="tabular ml-auto text-muted" title={`${c.member_count} message variants, confidence ${pct(c.confidence)}`}>{num(c.occurrences)}</span></li>))}</ul>
          ) : <p className="text-sm text-muted">Clusters appear once logs have been processed.</p>}
        </section>
      </div>

      <section className="rounded-panel border border-line bg-surface p-4">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <h3 className="text-base"><Term term="anomaly">Anomalies</Term></h3>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Select value={anomWindow} onChange={(e) => setAnomWindow(Number(e.target.value))} className="w-auto" aria-label="Time range">
              {ANOMALY_WINDOWS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
            <Select value={anomKind} onChange={(e) => setAnomKind(e.target.value)} className="w-auto" aria-label="Kind">
              <option value="">All kinds</option>
              {anomKinds.map((k) => <option key={k} value={k}>{titleCase(k)}</option>)}
            </Select>
            <Select value={anomSeverity} onChange={(e) => setAnomSeverity(e.target.value)} className="w-auto" aria-label="Severity">
              <option value="">All severities</option>
              {["high", "medium", "low"].map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
            </Select>
            <Select value={anomService} onChange={(e) => setAnomService(e.target.value)} className="w-auto" aria-label="Service">
              <option value="">All services</option>
              {anomServices.map((s) => <option key={s} value={s}>{s}</option>)}
            </Select>
          </div>
        </div>
        {anomalies.isLoading && <Skeleton className="h-16" />}
        <ErrorNote error={anomalies.error} />
        {!anomalies.isLoading && filteredAnomalies.length === 0 && (
          <p className="text-sm text-muted">{anomalies.data?.length ? "No anomalies match these filters." : "Nothing unusual against the baseline in this window."}</p>
        )}
        {filteredAnomalies.length > 0 && (
          <>
            <p className="mb-2 text-sm text-muted">{filteredAnomalies.length} of {anomalies.data!.length} shown</p>
            <ul className="hairlines text-sm">{filteredAnomalies.slice(0, 30).map((a) => (
              <li key={a.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-2">
                <Chip tone={anomalyTone[a.severity as keyof typeof anomalyTone] ?? "neutral"}>{a.kind.replace(/_/g, " ")}</Chip>
                <span className="font-medium">{a.service}</span>
                <span className="min-w-0 flex-1 basis-64 agent-voice text-[14px]">{a.explanation}</span>
                <time className="text-xs text-muted tabular" dateTime={a.window_start}>{timeAgo(a.window_start)}</time>
                <AskAgent prompt={`What caused the ${a.kind.replace(/_/g, " ")} in ${a.service}?`} label="Ask why" iconOnly />
              </li>))}</ul>
          </>
        )}
      </section>
    </div>
  );
}
