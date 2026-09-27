import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "@/api/endpoints";
import { Button, Chip, ErrorNote, Input, Label, Select, Skeleton } from "@/components/shared/ui";
import { fmtDateTime } from "@/utils/format";

const EVENTS = [["alert.created", "New alert"], ["alert.escalated", "Alert escalated to critical"], ["report.pre_mortem", "Pre-mortem drafted"]] as const;

/** Where the agent sends alerts outside the console (signed JSON webhooks). Slack and PagerDuty delivery are on the roadmap. */
export function WebhooksPanel() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["webhooks"], queryFn: api.webhooks.list });
  const deliveries = useQuery({ queryKey: ["deliveries"], queryFn: api.webhooks.deliveries, refetchInterval: 30_000 });
  const [f, setF] = useState({ name: "", url: "", secret: "", min_level: "warning", events: EVENTS.map((e) => e[0]) as string[] });
  const create = useMutation({ mutationFn: () => api.webhooks.create({ ...f, secret: f.secret || undefined }), onSuccess: () => { setF({ ...f, name: "", url: "", secret: "" }); qc.invalidateQueries({ queryKey: ["webhooks"] }); } });
  const remove = useMutation({ mutationFn: (id: string) => api.webhooks.remove(id), onSuccess: () => qc.invalidateQueries({ queryKey: ["webhooks"] }) });
  const test = useMutation({ mutationFn: (id: string) => api.webhooks.test(id), onSuccess: () => qc.invalidateQueries({ queryKey: ["deliveries"] }) });

  return (
    <div className="space-y-5">
      <section className="rounded-panel border border-line bg-surface p-4">
        <h3 className="text-base">Send alerts to another system</h3>
        <p className="mb-3 text-sm text-muted">Each delivery is a JSON POST signed with your secret in the <code>X-LogPilot-Signature</code> header. Private and local addresses are refused.</p>
        <form onSubmit={(e) => { e.preventDefault(); create.mutate(); }} className="grid gap-3 sm:grid-cols-2">
          <div><Label htmlFor="wh-name">Name</Label><Input id="wh-name" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="On-call bridge" /></div>
          <div><Label htmlFor="wh-url">URL</Label><Input id="wh-url" required type="url" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} placeholder="https://example.com/hooks/logpilot" /></div>
          <div><Label htmlFor="wh-secret">Signing secret (optional)</Label><Input id="wh-secret" type="password" autoComplete="off" value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} /></div>
          <div><Label htmlFor="wh-level">Send from</Label><Select id="wh-level" value={f.min_level} onChange={(e) => setF({ ...f, min_level: e.target.value })}><option value="warning">Warning and above</option><option value="critical">Critical only</option></Select></div>
          <fieldset className="sm:col-span-2"><legend className="mb-1 text-sm font-medium">Events</legend><div className="flex flex-wrap gap-4 text-sm">{EVENTS.map(([v, l]) => <label key={v} className="flex items-center gap-2"><input type="checkbox" className="accent-[hsl(var(--teal))]" checked={f.events.includes(v)} onChange={() => setF({ ...f, events: f.events.includes(v) ? f.events.filter((x) => x !== v) : [...f.events, v] })} />{l}</label>)}</div></fieldset>
          <div className="sm:col-span-2"><ErrorNote error={create.error} className="mb-2" /><Button type="submit" variant="primary" loading={create.isPending}>Add webhook</Button></div>
        </form>
      </section>
      <section className="rounded-panel border border-line bg-surface p-4">
        <h3 className="mb-1 text-base">Registered webhooks</h3>
        {list.isLoading && <Skeleton className="h-16" />}
        {list.data?.length === 0 && <p className="text-sm text-muted">None yet. Alerts still appear in the console and the feed.</p>}
        <ul className="hairlines">{list.data?.map((w) => (
          <li key={w.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5 text-sm">
            <span className="font-medium">{w.name}</span><span className="min-w-0 truncate text-muted">{w.url}</span><Chip>{w.min_level}+</Chip>{w.has_secret && <Chip tone="go">signed</Chip>}
            <span className="ml-auto flex items-center gap-1.5"><Button size="sm" loading={test.isPending && test.variables === w.id} onClick={() => test.mutate(w.id)}>Send test</Button><Button size="sm" variant="ghost" aria-label={`Delete ${w.name}`} onClick={() => remove.mutate(w.id)}><Trash2 /></Button></span>
            {test.data && test.variables === w.id && <p className={`basis-full ${test.data.ok ? "text-go" : "text-signal-ink"}`} role="status">{test.data.ok ? "Delivered." : `Failed: ${test.data.error ?? "no response"}`}</p>}
          </li>))}</ul>
      </section>
      <section className="rounded-panel border border-line bg-surface p-4">
        <h3 className="mb-1 text-base">Recent deliveries</h3>
        <ul className="hairlines text-sm">{(deliveries.data ?? []).slice(0, 12).map((d) => <li key={d.id} className="flex flex-wrap gap-x-3 py-1.5"><Chip tone={d.status === "delivered" ? "go" : d.status === "failed" ? "signal" : "neutral"}>{d.status}</Chip><span>{d.channel === "in_app" ? "In-app" : "Webhook"}</span><span className="text-muted">{d.event_type}</span>{d.error && <span className="text-signal-ink">{d.error}</span>}<time className="ml-auto text-muted">{fmtDateTime(d.created_at)}</time></li>)}{!deliveries.data?.length && <li className="py-2 text-muted">Nothing delivered yet.</li>}</ul>
      </section>
    </div>
  );
}
