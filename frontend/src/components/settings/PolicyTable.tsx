import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "@/api/endpoints";
import { Switch } from "@/components/shared/overlays";
import { Button, Chip, ErrorNote, Input, Select, Skeleton } from "@/components/shared/ui";
import type { PolicyResponse, PolicyRow, ToolPolicy } from "@/types";

function Row({ tool, row, tiers, scopes, onSaved }: { tool: ToolPolicy; row: PolicyRow; tiers: PolicyResponse["tiers"]; scopes: string[]; onSaved: () => void }) {
  const [d, setD] = useState(row);
  const dirty = JSON.stringify(d) !== JSON.stringify(row);
  const save = useMutation({ mutationFn: () => api.policy.set(tool.name, d), onSuccess: onSaved });
  const del = useMutation({ mutationFn: () => api.policy.removeOverride(tool.name, row.scope), onSuccess: onSaved });
  const locked = !tool.editable;
  const id = `${tool.name}-${row.scope}`;
  return (
    <div className="grid grid-cols-2 items-end gap-x-4 gap-y-2 py-2.5 sm:grid-cols-[6.5rem_minmax(11rem,1fr)_6.5rem_7rem_5rem_auto]">
      <div className="text-sm"><span className="mb-1 block text-muted">Applies to</span>{row.scope === "*" ? <Chip>All environments</Chip> : <Chip tone="teal">{row.scope} only</Chip>}</div>
      <div><label htmlFor={`${id}-tier`} className="mb-1 block text-sm text-muted">Autonomy tier</label>
        <Select id={`${id}-tier`} disabled={locked} value={d.tier} onChange={(e) => setD({ ...d, tier: e.target.value })}>
          {tiers.map((t) => <option key={t.tier} value={t.tier} disabled={!t.selectable}>{t.label}{t.selectable ? "" : " (future)"}</option>)}
        </Select></div>
      <div><label htmlFor={`${id}-conf`} className="mb-1 block text-sm text-muted">Min confidence</label><Input id={`${id}-conf`} disabled={locked} type="number" min={0} max={1} step={0.05} value={d.min_confidence} onChange={(e) => setD({ ...d, min_confidence: Number(e.target.value) })} /></div>
      <label className="flex items-center gap-2 text-sm"><Switch disabled={locked} checked={d.requires_approval} onCheckedChange={(v) => setD({ ...d, requires_approval: v })} aria-label="Requires approval" />Needs approval</label>
      <label className="flex items-center gap-2 text-sm"><Switch disabled={locked} checked={d.enabled} onCheckedChange={(v) => setD({ ...d, enabled: v })} aria-label="Enabled" />Enabled</label>
      <div className="flex items-center gap-1.5">
        <Button size="sm" variant="primary" disabled={!dirty || locked} loading={save.isPending} onClick={() => save.mutate()}>Save</Button>
        {row.scope !== "*" && <Button size="sm" variant="ghost" aria-label={`Remove the ${row.scope} override`} onClick={() => del.mutate()} disabled={locked}><Trash2 /></Button>}
      </div>
      <div className="col-span-full"><ErrorNote error={save.error || del.error} /></div>
      <span className="hidden" aria-hidden>{scopes.length}</span>
    </div>
  );
}

/** Per-tool autonomy tier configuration (PRD 8.2), per environment: stricter in production, looser in staging. */
export function PolicyTable() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["policy"], queryFn: api.policy.tools });
  const [adding, setAdding] = useState<string | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["policy"] });
  const add = useMutation({ mutationFn: (v: { tool: ToolPolicy; scope: string }) => api.policy.set(v.tool.name, { scope: v.scope, tier: v.tool.policies[0]?.tier ?? v.tool.default_tier, min_confidence: 0, requires_approval: v.tool.policies[0]?.requires_approval ?? false, enabled: true }), onSuccess: () => { setAdding(null); refresh(); } });
  if (q.isLoading) return <Skeleton className="h-96" />;
  if (q.error) return <ErrorNote error={q.error} />;
  const p = q.data!;
  return (
    <div className="space-y-5">
      <section className="rounded-panel border border-line bg-surface p-4">
        <h3 className="mb-2 text-base">What the tiers mean</h3>
        <dl className="grid gap-x-8 gap-y-2 text-sm md:grid-cols-2">{p.tiers.map((t) => <div key={t.tier}><dt className="font-medium">{t.label}</dt><dd className="text-muted">{t.definition}</dd></div>)}</dl>
        <p className="mt-3 text-sm text-muted">An action outside the granted tier is never silently blocked: it's downgraded to propose-only and sent to the approval queue. Every autonomous action is logged and can be reversed.</p>
      </section>
      <ErrorNote error={add.error} />
      <ul className="hairlines rounded-panel border border-line bg-surface px-4">
        {p.tools.map((t) => (
          <li key={t.name} className="py-3">
            <div className="flex flex-wrap items-baseline gap-2">
              <h3 className="text-base">{t.title}</h3>
              {t.flagship && <Chip tone="teal">Flagship</Chip>}
              {t.human_review && <Chip tone="amber">{t.human_review}</Chip>}
              {t.mandatory && <Chip tone="go"><Lock className="size-3" aria-hidden />Always on</Chip>}
              {!t.editable && !t.mandatory && <Chip>Admin only</Chip>}
            </div>
            <p className="text-sm text-muted">{t.description}</p>
            {t.mandatory ? (
              <p className="mt-2 text-sm">PII redaction runs before any data is stored or sent to an AI provider. It can't be dialled down, turned off or gated on approval.</p>
            ) : (
              <>
                <div className="hairlines">{t.policies.map((r) => <Row key={r.scope + r.tier + r.enabled + r.min_confidence + r.requires_approval} tool={t} row={r} tiers={p.tiers} scopes={p.scopes} onSaved={refresh} />)}</div>
                {t.editable && (adding === t.name ? (
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-sm"><span>Add an override for</span>{p.scopes.filter((s) => s !== "*" && !t.policies.some((r) => r.scope === s)).map((s) => <Button key={s} size="sm" onClick={() => add.mutate({ tool: t, scope: s })}>{s}</Button>)}<Button size="sm" variant="ghost" onClick={() => setAdding(null)}>Cancel</Button></div>
                ) : (
                  <Button size="sm" variant="ghost" className="mt-1" onClick={() => setAdding(t.name)}><Plus /> Add a per-environment override</Button>
                ))}
              </>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
