import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "@/api/endpoints";
import { Term } from "@/components/glossary/Term";
import { AskAgent } from "@/components/shared/AskAgent";
import { EtaText, levelOf, RiskFigure, RiskRunway } from "@/components/shared/RiskRunway";
import { Button, ErrorNote, Skeleton } from "@/components/shared/ui";
import { useProjectId } from "@/hooks/useProject";
import { can, useSession } from "@/store/session";
import { cn } from "@/utils/cn";
import { fmtTime, timeAgo } from "@/utils/format";

function SignalBar({ label, term, score, weight, note }: { label: string; term: string; score: number; weight: number; note: string }) {
  return (
    <div className="grid grid-cols-[1fr_auto] items-baseline gap-x-3 gap-y-1 py-2.5">
      <div className="text-sm font-medium"><Term term={term}>{label}</Term> <span className="font-normal text-muted">weighted {Math.round(weight * 100)}%</span></div>
      <div className="tabular text-sm font-semibold">{Math.round(score)}<span className="font-normal text-muted"> / 100</span></div>
      <div className="col-span-2 h-1.5 overflow-hidden rounded-full bg-ink/[.08]"><div className="h-full rounded-full bg-chart" style={{ width: `${Math.min(100, score)}%` }} /></div>
      <p className="col-span-2 text-sm text-muted">{note}</p>
    </div>
  );
}

/** Full signal breakdown and supporting evidence for one service: what the agent sees, and why it scores it this way. */
export function RiskDetail({ serviceId }: { serviceId: string }) {
  const projectId = useProjectId();
  const user = useSession((s) => s.user);
  const qc = useQueryClient();
  const detail = useQuery({ queryKey: ["risk-detail", projectId, serviceId], queryFn: () => api.risk.detail(projectId, serviceId), refetchInterval: 30_000 });
  const hist = useQuery({ queryKey: ["risk-history-6h", projectId, serviceId], queryFn: () => api.risk.history(projectId, serviceId, 6), refetchInterval: 60_000 });
  const run = useMutation({ mutationFn: () => api.risk.run(projectId, serviceId), onSuccess: () => qc.invalidateQueries({ queryKey: ["risk-detail", projectId, serviceId] }) });

  if (detail.isLoading) return <Skeleton className="h-96" />;
  if (detail.error) return <ErrorNote error={detail.error} />;
  const d = detail.data!;
  const s = d.signals;
  const th = s.thresholds ?? { warning: 60, critical: 80 };
  const lv = levelOf(d.risk_score, th.warning, th.critical);
  const pts = (hist.data?.points ?? []).map((p) => ({ t: fmtTime(p.t), risk: p.risk }));
  const v = s.velocity, b = s.baseline, sim = s.similarity;

  return (
    <article className="rounded-panel border border-line bg-surface p-5" aria-label={`${d.service} risk details`}>
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-xl">{d.service}</h2>
          <p className="text-sm text-muted">Scored {timeAgo(d.as_of)}{d.cycle_ms != null ? `, cycle took ${d.cycle_ms} ms` : ""}. <EtaText low={d.eta_minutes_low} high={d.eta_minutes_high} /></p>
        </div>
        <div className="flex items-center gap-3">
          <RiskFigure score={d.risk_score} level={lv} trend={d.trend} />
          {can(user, "services.configure") && <Button size="sm" variant="secondary" onClick={() => run.mutate()} loading={run.isPending}><RefreshCw /> Re-score now</Button>}
        </div>
      </header>
      <RiskRunway score={d.risk_score} warning={th.warning} critical={th.critical} className="mt-3 mb-4" />

      {d.alert && (
        <div className="mb-4 rounded-control bg-amber-soft/60 px-3 py-2.5">
          <p className="agent-voice text-[14.5px]">{d.alert.text}</p>
          <Link to={`/alerts?open=${d.alert.id}`} className="mt-1 inline-block text-sm font-medium text-teal-ink hover:underline">Review the alert and its proposed actions</Link>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <div>
          <h3 className="mb-1 text-base">How this score is made</h3>
          <div className="hairlines">
            <SignalBar label="Error velocity" term="error velocity" score={d.velocity_score} weight={d.weights.velocity}
              note={`${v.errors_per_min.toFixed(1)} errors/min now, ${v.ratio.toFixed(1)}x its usual ${v.baseline_per_min.toFixed(1)}/min. ${v.acceleration > 0.05 ? "Errors are accelerating." : v.velocity > 0.1 ? "Still rising." : "Not accelerating."}`} />
            <SignalBar label="Pattern similarity" term="leading indicator" score={d.similarity_score} weight={d.weights.similarity}
              note={sim.signatures_known ? `Closest past pre-failure pattern matches at ${sim.best_match.toFixed(2)}${sim.best_label ? `: "${sim.best_label.slice(0, 80)}"` : ""}.` : "No past incidents recorded yet, so only pattern drift counts here."} />
            <SignalBar label="Baseline deviation" term="baseline deviation" score={d.baseline_score} weight={d.weights.baseline}
              note={b.available ? `${b.z.toFixed(1)} standard deviations above normal for this time of week (${b.ratio.toFixed(1)}x).` : "No baseline yet."} />
          </div>
          <p className="mt-2 text-xs text-muted">
            Weights start at 30/40/30 and adapt to this service from your outcome feedback ({d.weights.feedback_samples} labelled so far: {d.weights.true_positives} accurate, {d.weights.false_positives} false alarms).
          </p>
        </div>

        <div>
          <h3 className="mb-1 text-base">Risk over the last 6 hours</h3>
          <div className="h-44" role="img" aria-label="Line chart of risk score over the last six hours">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={pts} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid stroke="hsl(var(--line))" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="t" tick={{ fontSize: 11, fill: "hsl(var(--muted))" }} interval="preserveStartEnd" minTickGap={40} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 11, fill: "hsl(var(--muted))" }} ticks={[0, 25, th.warning, th.critical, 100]} />
                <ReferenceLine y={th.warning} stroke="hsl(var(--amber))" strokeDasharray="4 3" />
                <ReferenceLine y={th.critical} stroke="hsl(var(--signal))" strokeDasharray="4 3" />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 6, background: "hsl(var(--raised))", border: "1px solid hsl(var(--line))" }} />
                <Line type="monotone" dataKey="risk" stroke="hsl(var(--chart))" strokeWidth={2} dot={false} isAnimationActive={false} name="Risk" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      <div className="mt-5 grid gap-6 lg:grid-cols-2">
        <section>
          <h3 className="mb-1 text-base">What is failing</h3>
          {s.top_errors?.length ? (
            <ul className="hairlines text-sm">{s.top_errors.map((e) => (<li key={e.message} className="flex gap-3 py-2"><span className="tabular w-12 shrink-0 text-right font-semibold">{e.count}</span><span className="break-words font-mono text-[12.5px] text-muted">{e.message}</span></li>))}</ul>
          ) : <p className="text-sm text-muted">No errors in the last 10 minutes.</p>}
        </section>
        <section>
          <h3 className="mb-1 text-base">Similar past incidents</h3>
          {sim.matches?.length ? (
            <ul className="hairlines text-sm">{sim.matches.map((m) => (
              <li key={m.incident_start} className="py-2"><div className="font-medium">{new Date(m.incident_start).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}<span className="ml-2 font-normal text-muted">similarity {m.similarity.toFixed(2)}</span></div>
                {m.resolved_actions.length > 0 && <p className="text-muted">Resolved by: {m.resolved_actions.join("; ")}</p>}</li>))}</ul>
          ) : <p className="text-sm text-muted">Nothing comparable on record for this service yet. Log outcomes on alerts and the agent will build this memory.</p>}
        </section>
      </div>

      {s.drift.drifting_variants?.length > 0 && (
        <p className="mt-4 rounded-control bg-amber-soft/60 px-3 py-2 text-sm"><Term term="pattern drift">Pattern drift</Term>: new error variants related to earlier ones are appearing, e.g. "{s.drift.drifting_variants[0].template.slice(0, 90)}".</p>
      )}

      <footer className={cn("mt-5 flex flex-wrap items-center gap-2 border-t border-line pt-3")}>
        <AskAgent prompt={`Why is ${d.service} ${lv === "ok" ? "at " + Math.round(d.risk_score) + "/100" : "flagged"}?`} label="Ask the agent why" />
        <AskAgent prompt={`What is the root cause of the errors in ${d.service}?`} label="Trace the root cause" />
        <AskAgent prompt={`What should we do about ${d.service}?`} label="What should we do?" />
      </footer>
    </article>
  );
}
