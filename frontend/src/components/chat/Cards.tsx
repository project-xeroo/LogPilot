import { ArrowRightLeft, FileText, GitBranch, Lightbulb, ListChecks } from "lucide-react";
import { Link } from "react-router-dom";
import { Area, AreaChart, ResponsiveContainer, Tooltip as RTooltip, XAxis } from "recharts";
import { Chip } from "@/components/shared/ui";
import { levelOf, RiskFigure, RiskRunway, EtaText, levelTone } from "@/components/shared/RiskRunway";
import type { Card } from "@/types";
import { fmtTime, pct } from "@/utils/format";
import { cn } from "@/utils/cn";

/** Structured tool output shown inside the conversation. Every card links to the screen that holds the full view. */

function Shell({ icon, title, to, linkLabel, children }: { icon: React.ReactNode; title: string; to?: string; linkLabel?: string; children: React.ReactNode }) {
  return (
    <div className="rounded-panel border border-line bg-raised">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2 text-sm font-medium [&_svg]:size-4 [&_svg]:text-muted">
        {icon}
        <span>{title}</span>
        {to && <Link to={to} className="ml-auto text-teal-ink underline-offset-2 hover:underline">{linkLabel ?? "Open"}</Link>}
      </div>
      <div className="px-3 py-2.5">{children}</div>
    </div>
  );
}

function RiskCard({ data }: { data: any }) {
  const rows = ([...(data.services ?? [])] as any[]).sort((a, b) => (b.risk_score ?? -1) - (a.risk_score ?? -1)).slice(0, 5);
  if (!rows.length) return <Shell icon={<ListChecks />} title="Failure risk"><p className="text-sm text-muted">{data.message ?? "No risk scores yet."}</p></Shell>;
  return (
    <Shell icon={<ListChecks />} title="Failure risk right now" to="/risk" linkLabel="Open risk board">
      <ul className="hairlines">
        {rows.map((r) => {
          const lv = levelOf(r.risk_score, r.thresholds?.warning, r.thresholds?.critical);
          return (
            <li key={r.service_id} className="grid grid-cols-[8.5rem_1fr_auto] items-center gap-3 py-2 first:pt-0 last:pb-0">
              <span className="truncate text-sm font-medium">{r.service}</span>
              <RiskRunway score={r.risk_score} warning={r.thresholds?.warning} critical={r.thresholds?.critical} compact />
              <span className="flex items-center gap-2"><RiskFigure score={r.risk_score} level={lv} trend={r.trend} /></span>
            </li>
          );
        })}
      </ul>
      {rows[0]?.eta_minutes_low != null && <p className="mt-2"><EtaText low={rows[0].eta_minutes_low} high={rows[0].eta_minutes_high} /> <span className="text-sm text-muted">for {rows[0].service}</span></p>}
    </Shell>
  );
}

const statusTone: Record<string, "go" | "amber" | "signal" | "neutral"> = { healthy: "go", degraded: "amber", unhealthy: "signal", silent: "neutral" };

function HealthCard({ data }: { data: any }) {
  const rows = (data.services ?? []).slice(0, 6);
  const tl = (data.error_timeline ?? []).map((p: any) => ({ t: fmtTime(p.t), errors: p.errors }));
  return (
    <Shell icon={<ListChecks />} title={`Service health, last ${data.window_minutes} min`} to="/risk" linkLabel="Open health state">
      {tl.length > 1 && (
        <div className="mb-2 h-16" aria-hidden>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={tl} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
              <XAxis dataKey="t" hide />
              <RTooltip contentStyle={{ fontSize: 12, borderRadius: 6 }} />
              <Area type="monotone" dataKey="errors" stroke="hsl(var(--signal))" fill="hsl(var(--signal))" fillOpacity={0.12} strokeWidth={1.5} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
      <ul className="hairlines text-sm">
        {rows.map((s: any) => (
          <li key={s.service} className="flex items-center gap-2 py-1.5 first:pt-0 last:pb-0">
            <span className="font-medium">{s.service}</span>
            <Chip tone={statusTone[s.status] ?? "neutral"}>{s.status}</Chip>
            <span className="ml-auto tabular text-muted">{s.errors} errors, {pct(s.error_rate, 1)}</span>
          </li>
        ))}
      </ul>
    </Shell>
  );
}

const ROLE_LABEL: Record<string, string> = { root_cause: "Root cause", contributing: "Contributing", symptom: "Symptom" };

export function RcaChain({ data }: { data: any }) {
  const chain = data.causal_chain ?? [];
  return (
    <ol className="space-y-0">
      {chain.map((c: any, i: number) => (
        <li key={c.service + i} className="relative flex gap-3 pb-3 last:pb-0">
          <span className="flex flex-col items-center">
            <span className={cn("mt-1 size-2.5 rounded-full", c.role === "root_cause" ? "bg-signal" : c.role === "contributing" ? "bg-amber" : "bg-muted")} aria-hidden />
            {i < chain.length - 1 && <span className="mt-1 w-px flex-1 bg-line" aria-hidden />}
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{c.service}</span><Chip tone={c.role === "root_cause" ? "signal" : c.role === "contributing" ? "amber" : "neutral"}>{ROLE_LABEL[c.role] ?? c.role}</Chip></div>
            {c.event && <p className="text-sm text-muted">{c.event}</p>}
          </div>
        </li>
      ))}
    </ol>
  );
}

function RcaCard({ data }: { data: any }) {
  return (
    <Shell icon={<GitBranch />} title="Root cause analysis">
      {(data.causal_chain ?? []).length ? <RcaChain data={data} /> : <p className="text-sm text-muted">{data.explanation}</p>}
      <p className="mt-2 text-sm text-muted">Confidence <strong className="text-ink">{pct(data.confidence)}</strong>. This is a draft for a person to review; nothing has been changed.</p>
    </Shell>
  );
}

function SearchCard({ data }: { data: any }) {
  const rows = (data.results ?? []).slice(0, 5);
  return (
    <Shell icon={<ListChecks />} title={`${data.count ?? rows.length} matching log records`} to="/search" linkLabel="Open search">
      <ul className="hairlines text-sm">
        {rows.map((r: any) => (
          <li key={r.id} className="py-1.5 first:pt-0 last:pb-0">
            <div className="flex flex-wrap items-center gap-x-2"><span className="tabular text-muted">{fmtTime(r.timestamp)}</span><span className="font-medium">{r.service}</span><Chip tone={r.severity === "ERROR" || r.severity === "FATAL" ? "signal" : r.severity === "WARN" ? "amber" : "neutral"}>{r.severity}</Chip></div>
            <p className="line-clamp-2 break-words font-mono text-[12.5px] text-muted">{r.message}</p>
          </li>
        ))}
      </ul>
    </Shell>
  );
}

function DeploymentCard({ data }: { data: any }) {
  const a = data.from, b = data.to;
  return (
    <Shell icon={<ArrowRightLeft />} title={`${data.service}: ${a.version} vs ${b.version}`} to="/deployments" linkLabel="Open comparison">
      {data.regression && <p className="mb-2 rounded-control bg-signal-soft px-2.5 py-1.5 text-sm text-signal-ink">Regression: {(data.regression_reasons ?? []).join("; ")}.</p>}
      <div className="grid grid-cols-2 gap-3 text-sm">
        {[a, b].map((v: any) => (
          <div key={v.version}><div className="font-medium">{v.version}</div><div className="text-muted tabular">Error rate {pct(v.error_rate, 2)}</div><div className="text-muted tabular">{v.latency_p95_ms ? `p95 ${Math.round(v.latency_p95_ms)} ms` : "no latency data"}</div></div>
        ))}
      </div>
    </Shell>
  );
}

function ReportCard({ data }: { data: any }) {
  return (
    <Shell icon={<FileText />} title={data.existing ? "Existing report" : "Draft report ready"} to={`/reports?open=${data.report_id}`} linkLabel="Review and edit">
      <p className="text-sm">{data.title}</p>
      <p className="text-sm text-muted">Drafted by the agent. A person reviews and signs off before it is exported.</p>
    </Shell>
  );
}

function ExplainCard({ data }: { data: any }) {
  return (
    <Shell icon={<Lightbulb />} title="What this message means">
      <blockquote className="mb-2 break-words border-l-2 border-line pl-2 font-mono text-[12.5px] text-muted">{String(data.message).slice(0, 240)}</blockquote>
      <p className="text-sm"><Chip tone={data.is_normal ? "go" : "amber"}>{data.is_normal ? "Routine" : "Worth attention"}</Chip> {data.normality_note}</p>
    </Shell>
  );
}

export function CardView({ card }: { card: Card }) {
  switch (card.type) {
    case "risk": return <RiskCard data={card.data} />;
    case "health": return <HealthCard data={card.data} />;
    case "rca": return <RcaCard data={card.data} />;
    case "search": return <SearchCard data={card.data} />;
    case "deployment": return <DeploymentCard data={card.data} />;
    case "report": return <ReportCard data={card.data} />;
    case "explain": return <ExplainCard data={card.data} />;
    default: return null;
  }
}

void levelTone;
