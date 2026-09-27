import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, FileWarning } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/api/endpoints";
import { ActionRow, blockReason } from "@/components/alerts/ActionRow";
import { OutcomeDialog } from "@/components/alerts/OutcomeDialog";
import { Dialog, DialogClose, DialogContent } from "@/components/shared/overlays";
import { Term } from "@/components/glossary/Term";
import { AskAgent } from "@/components/shared/AskAgent";
import { EtaText, RiskFigure } from "@/components/shared/RiskRunway";
import { Button, Chip, ErrorNote, Label, Skeleton, Textarea } from "@/components/shared/ui";
import { useProjectId } from "@/hooks/useProject";
import { can, useSession } from "@/store/session";
import type { Alert } from "@/types";
import { cn } from "@/utils/cn";
import { fmtDateTime, pct, timeAgo } from "@/utils/format";

/**
 * A pre-incident alert: the agent's headline in its own words, then the evidence chain behind it (open by default in
 * guided mode so a new engineer learns the vocabulary from real examples), then the propose-only actions.
 */
export function AlertCard({ alertId, defaultOpen }: { alertId: string; defaultOpen?: boolean }) {
  const projectId = useProjectId();
  const user = useSession((s) => s.user);
  const guided = useSession((s) => s.guided);
  const qc = useQueryClient();
  const ref = useRef<HTMLElement>(null);
  const [open, setOpen] = useState(!!defaultOpen || guided);
  const [outcomeOpen, setOutcomeOpen] = useState(false);
  const [dismissOpen, setDismissOpen] = useState(false);
  const [reason, setReason] = useState("");
  const q = useQuery({ queryKey: ["alert", projectId, alertId], queryFn: () => api.alerts.detail(projectId, alertId), refetchInterval: 20_000 });
  useEffect(() => { if (guided) setOpen(true); }, [guided]);
  useEffect(() => { if (defaultOpen) ref.current?.scrollIntoView({ block: "center", behavior: "smooth" }); }, [defaultOpen]);

  const refresh = () => ["alerts", "alert", "approvals", "risk"].forEach((k) => qc.invalidateQueries({ queryKey: [k, projectId] }));
  const ack = useMutation({ mutationFn: () => api.alerts.acknowledge(projectId, alertId), onSuccess: refresh });
  const release = useMutation({ mutationFn: () => api.alerts.release(projectId, alertId), onSuccess: refresh });
  const dismiss = useMutation({ mutationFn: () => api.alerts.dismiss(projectId, alertId, reason), onSuccess: () => { refresh(); setDismissOpen(false); } });
  const pm = useMutation({ mutationFn: () => api.alerts.preMortem(projectId, alertId), onSuccess: refresh });

  if (q.isLoading) return <Skeleton className="h-40" />;
  if (q.error) return <ErrorNote error={q.error} />;
  const a = q.data as Alert;
  const ev = a.evidence_chain ?? {};
  const closed = !!a.resolved_at;
  const blocked = blockReason(user?.role, guided, { risk_level: a.level === "critical" ? "high" : "low" });
  const canOutcome = can(user, "alerts.outcome");

  return (
    <article ref={ref} className={cn("rounded-panel border bg-surface", a.level === "critical" && !closed ? "border-signal/70" : "border-line")} aria-label={`${a.level} alert for ${a.service}`}>
      <header className="flex flex-wrap items-start gap-3 px-4 pt-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-lg">{a.service}</h3>
            <Chip tone={a.level === "critical" ? "signal" : "amber"}>{a.level === "critical" ? "Critical" : "Warning"}</Chip>
            {a.status === "pending_review" && <Chip tone="teal">Held for review by policy</Chip>}
            {a.degraded && <Chip tone="amber" title="AI reasoning was unavailable, so this alert used threshold rules only">Threshold-based</Chip>}
            {closed && <Chip tone="neutral">{a.status === "dismissed" ? "Dismissed" : "Resolved"} {timeAgo(a.resolved_at)}</Chip>}
          </div>
          <p className="mt-0.5 text-sm text-muted">Raised {fmtDateTime(a.created_at)}{a.peak_risk_score > a.risk_score ? `, peaked at ${Math.round(a.peak_risk_score)}` : ""}. <EtaText low={a.eta_minutes_low} high={a.eta_minutes_high} /></p>
        </div>
        <RiskFigure score={a.risk_score} level={a.level} trend={undefined} />
      </header>

      <p className="agent-voice px-4 pb-3 pt-3">{a.text}</p>

      <div className="border-t border-line px-4">
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full items-center gap-2 py-2.5 text-sm font-medium text-teal-ink">
          <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} aria-hidden /> Why the agent is saying this
        </button>
        {open && (
          <div className="grid gap-5 pb-4 md:grid-cols-2">
            <div>
              <h4 className="mb-1 text-sm font-semibold">What drove the score</h4>
              <ul className="space-y-1.5 text-sm">
                {(ev.drivers ?? []).map((d) => (
                  <li key={d.signal}><div className="flex justify-between"><span><Term term={d.signal === "Error velocity" ? "error velocity" : d.signal === "Baseline deviation" ? "baseline deviation" : "leading indicator"}>{d.signal}</Term></span><span className="tabular text-muted">{Math.round(d.score)} x {Math.round(d.weight * 100)}%</span></div>
                    <div className="mt-0.5 h-1.5 overflow-hidden rounded-full bg-ink/[.08]"><div className="h-full bg-chart" style={{ width: `${Math.min(100, d.contribution * 2.5)}%` }} /></div></li>
                ))}
              </ul>
              {ev.top_errors?.length ? (<><h4 className="mb-1 mt-3 text-sm font-semibold">What is failing</h4><ul className="text-sm">{ev.top_errors.map((e) => <li key={e.message} className="flex gap-2 py-0.5"><span className="tabular w-10 shrink-0 text-right font-semibold">{e.count}</span><span className="break-words font-mono text-[12.5px] text-muted">{e.message}</span></li>)}</ul></>) : null}
            </div>
            <div>
              <h4 className="mb-1 text-sm font-semibold">Similar past incidents</h4>
              {(ev.similar_incidents ?? a.pattern_matches ?? []).length ? (
                <ul className="hairlines text-sm">{(ev.similar_incidents ?? a.pattern_matches ?? []).map((m) => (
                  <li key={m.incident_start} className="py-1.5"><span className="font-medium">{fmtDateTime(m.incident_start)}</span> <span className="text-muted">similarity {m.similarity.toFixed(2)}</span>{m.resolved_actions?.length ? <p className="text-muted">Resolved by: {m.resolved_actions.join("; ")}</p> : null}</li>))}</ul>
              ) : <p className="text-sm text-muted">No comparable incident on record yet.</p>}
              {ev.rca && (<><h4 className="mb-1 mt-3 text-sm font-semibold">Root cause analysis (automatic)</h4><p className="text-sm">{ev.rca.chain.join(" → ")} <span className="text-muted">confidence {pct(ev.rca.confidence)}</span></p></>)}
            </div>
          </div>
        )}
      </div>

      <div className="border-t border-line px-4">
        <h4 className="pt-3 text-sm font-semibold">Proposed actions <span className="font-normal text-muted">The agent never runs these; a person decides.</span></h4>
        {a.actions?.length ? <ul className="hairlines">{a.actions.map((x) => <ActionRow key={x.id} a={x} />)}</ul> : <p className="py-3 text-sm text-muted">No actions were proposed.</p>}
      </div>

      <footer className="flex flex-wrap items-center gap-2 border-t border-line px-4 py-3">
        {a.status === "pending_review" && !closed && <Button variant="primary" size="sm" loading={release.isPending} disabled={!!blocked} onClick={() => release.mutate()}>Release this alert</Button>}
        {!closed && a.status === "open" && canOutcome && <Button size="sm" loading={ack.isPending} onClick={() => ack.mutate()}>Acknowledge</Button>}
        {!closed && canOutcome && <Button size="sm" onClick={() => setOutcomeOpen(true)}>Log the outcome</Button>}
        {!closed && <Button size="sm" variant="ghost" disabled={!!blocked} onClick={() => setDismissOpen(true)}>Dismiss alert</Button>}
        {a.pre_mortem_report_id
          ? <Button size="sm" asChild><Link to={`/reports?open=${a.pre_mortem_report_id}`}><FileWarning /> Open pre-mortem</Link></Button>
          : can(user, "reports.generate") && <Button size="sm" loading={pm.isPending} onClick={() => pm.mutate()}><FileWarning /> Draft a pre-mortem</Button>}
        <AskAgent prompt={`Why is ${a.service} flagged?`} label="Ask why" className="ml-auto" />
      </footer>
      <ErrorNote error={ack.error || release.error || pm.error} className="mx-4 mb-3" />
      {a.outcomes?.length ? <p className="border-t border-line px-4 py-2.5 text-sm text-muted">Outcome logged: {a.outcomes[0].outcome.replace("_", " ")}{a.outcomes[0].action_taken ? `, ${a.outcomes[0].action_taken}` : ""}. {a.outcomes[0].learned ? "The agent has learned from it." : "The agent is learning from it."}</p> : null}

      <OutcomeDialog alertId={alertId} service={a.service} open={outcomeOpen} onOpenChange={setOutcomeOpen} />
      <Dialog open={dismissOpen} onOpenChange={setDismissOpen}>
        <DialogContent title={`Dismiss the alert for ${a.service}`} description="Say why. It's recorded, and any undecided proposals on it are dismissed with the same reason.">
          <div><Label htmlFor="dismiss-alert">Reason</Label><Textarea id="dismiss-alert" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} /></div>
          <ErrorNote error={dismiss.error} />
          <div className="flex justify-end gap-2"><DialogClose asChild><Button>Cancel</Button></DialogClose><Button variant="danger" disabled={reason.trim().length < 3} loading={dismiss.isPending} onClick={() => dismiss.mutate()}>Dismiss alert</Button></div>
        </DialogContent>
      </Dialog>
    </article>
  );
}
