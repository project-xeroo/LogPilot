import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "@/api/endpoints";
import { Switch } from "@/components/shared/overlays";
import { Button, ErrorNote, Input, Label, Skeleton, Textarea } from "@/components/shared/ui";
import { Term } from "@/components/glossary/Term";
import { useProjectId } from "@/hooks/useProject";
import { can, useSession } from "@/store/session";
import type { MonitoredService } from "@/types";

function Thresholds() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["forecast-settings"], queryFn: api.policy.forecast });
  const [d, setD] = useState({ warning_threshold: 60, critical_threshold: 80, interval_seconds: 60, window_seconds: 60 });
  useEffect(() => { if (q.data) setD(q.data); }, [q.data]);
  const save = useMutation({ mutationFn: () => api.policy.setForecast(d), onSuccess: () => qc.invalidateQueries({ queryKey: ["forecast-settings"] }) });
  if (q.isLoading) return <Skeleton className="h-40" />;
  const bad = d.critical_threshold <= d.warning_threshold;
  return (
    <form onSubmit={(e) => { e.preventDefault(); if (!bad) save.mutate(); }} className="rounded-panel border border-line bg-surface p-4">
      <h3 className="text-base">Alert thresholds and cadence</h3>
      <p className="mb-3 text-sm text-muted">Defaults for every service. A risk score at or above <Term term="failure risk score">the warning line</Term> raises an alert; at or above the critical line the agent also drafts a pre-mortem.</p>
      <div className="grid gap-3 sm:grid-cols-4">
        {([["warning_threshold", "Warning at", 1, 99], ["critical_threshold", "Critical at", 2, 100], ["interval_seconds", "Score every (seconds)", 10, 3600], ["window_seconds", "Sampling window (seconds)", 10, 600]] as const).map(([k, l, min, max]) => (
          <div key={k}><Label htmlFor={k}>{l}</Label><Input id={k} type="number" min={min} max={max} value={d[k]} onChange={(e) => setD({ ...d, [k]: Number(e.target.value) })} /></div>
        ))}
      </div>
      {bad && <p role="alert" className="mt-2 text-sm text-signal-ink">The critical threshold must be higher than the warning threshold.</p>}
      <ErrorNote error={save.error} className="mt-2" />
      <div className="mt-3 flex items-center gap-3"><Button type="submit" variant="primary" disabled={bad} loading={save.isPending}>Save thresholds</Button>{save.isSuccess && <span className="text-sm text-go" role="status">Saved.</span>}</div>
    </form>
  );
}

function ServiceRow({ s, projectId }: { s: MonitoredService; projectId: string }) {
  const qc = useQueryClient();
  const [d, setD] = useState({ enabled: s.enabled, interval: s.forecast_interval_seconds, warn: s.warning_threshold ?? "", crit: s.critical_threshold ?? "", playbook: (s.playbook?.steps ?? []).join("\n") });
  const save = useMutation({
    mutationFn: () => api.configureService(projectId, s.id, {
      enabled: d.enabled, forecast_interval_seconds: d.interval, playbook: d.playbook.split("\n").map((x) => x.trim()).filter(Boolean),
      ...(d.warn === "" && d.crit === "" ? { clear_thresholds: true } : { warning_threshold: d.warn === "" ? undefined : Number(d.warn), critical_threshold: d.crit === "" ? undefined : Number(d.crit) }),
    } as any),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["services", projectId] }),
  });
  return (
    <li className="py-3">
      <div className="grid items-end gap-x-4 gap-y-2 sm:grid-cols-[minmax(9rem,1fr)_6rem_6rem_6rem_6rem_auto]">
        <div><div className="font-medium">{s.name}</div><label className="mt-1 flex items-center gap-2 text-sm text-muted"><Switch checked={d.enabled} onCheckedChange={(v) => setD({ ...d, enabled: v })} aria-label={`Forecast ${s.name}`} />Forecast this service</label></div>
        <div><Label htmlFor={`i-${s.id}`}>Every (s)</Label><Input id={`i-${s.id}`} type="number" min={10} value={d.interval} onChange={(e) => setD({ ...d, interval: Number(e.target.value) })} /></div>
        <div><Label htmlFor={`w-${s.id}`}>Warning</Label><Input id={`w-${s.id}`} type="number" placeholder="default" value={d.warn} onChange={(e) => setD({ ...d, warn: e.target.value as any })} /></div>
        <div><Label htmlFor={`c-${s.id}`}>Critical</Label><Input id={`c-${s.id}`} type="number" placeholder="default" value={d.crit} onChange={(e) => setD({ ...d, crit: e.target.value as any })} /></div>
        <span />
        <Button size="sm" variant="primary" loading={save.isPending} onClick={() => save.mutate()}>Save</Button>
      </div>
      <div className="mt-2"><Label htmlFor={`p-${s.id}`}>Playbook steps (one per line, optional)</Label><Textarea id={`p-${s.id}`} rows={2} value={d.playbook} onChange={(e) => setD({ ...d, playbook: e.target.value })} placeholder="Restart session-worker-2&#10;Scale Redis to 3 replicas" /></div>
      <ErrorNote error={save.error} className="mt-2" />
    </li>
  );
}

/** Alert thresholds plus per-service cadence, thresholds and playbooks (Admin and SRE). */
export function ForecastSettings() {
  const projectId = useProjectId();
  const user = useSession((s) => s.user);
  const q = useQuery({ queryKey: ["services", projectId], queryFn: () => api.services(projectId), enabled: !!projectId });
  return (
    <div className="space-y-5">
      {can(user, "settings.thresholds") && <Thresholds />}
      <section className="rounded-panel border border-line bg-surface p-4">
        <h3 className="text-base">Monitored services</h3>
        <p className="mb-1 text-sm text-muted">The agent discovers services from your logs. Override the cadence or thresholds per service, and give it a playbook to draw recommended actions from.</p>
        {q.isLoading && <Skeleton className="h-24" />}
        {q.data?.length === 0 && <p className="py-4 text-sm text-muted">No services yet. Upload logs and they appear here.</p>}
        <ul className="hairlines">{q.data?.map((s) => <ServiceRow key={s.id + s.forecast_interval_seconds + s.enabled} s={s} projectId={projectId} />)}</ul>
      </section>
    </div>
  );
}
