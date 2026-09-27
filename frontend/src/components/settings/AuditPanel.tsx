import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ShieldCheck, Undo2 } from "lucide-react";
import { useState } from "react";
import { download } from "@/api/client";
import { api } from "@/api/endpoints";
import { Dialog, DialogClose, DialogContent, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/shared/overlays";
import { Button, Chip, ErrorNote, Label, Skeleton, Textarea } from "@/components/shared/ui";
import { fmtDateTime } from "@/utils/format";

const TIER: Record<string, string> = { autonomous_background: "Background", autonomous_policy_bounded: "Policy-bounded", propose_only: "Propose-only", read_only: "Read-only" };

function RevertDialog({ id, summary, open, onOpenChange }: { id: string; summary: string; open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const m = useMutation({ mutationFn: () => api.audit.revert(id, note), onSuccess: () => { qc.invalidateQueries({ queryKey: ["audit"] }); onOpenChange(false); } });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Reverse this action" description={summary}>
        <div><Label htmlFor="rv-note">Why are you reversing it?</Label><Textarea id="rv-note" rows={3} value={note} onChange={(e) => setNote(e.target.value)} /></div>
        <p className="text-sm text-muted">The original action stays in the audit trail, marked as reversed.</p>
        <ErrorNote error={m.error} />
        <div className="flex justify-end gap-2"><DialogClose asChild><Button>Cancel</Button></DialogClose><Button variant="danger" loading={m.isPending} onClick={() => m.mutate()}>Reverse action</Button></div>
      </DialogContent>
    </Dialog>
  );
}

/** Audit trail for everything people and the agent have done. Agent actions record tool, trigger, autonomy level, confidence and approver. */
export function AuditPanel() {
  const actions = useQuery({ queryKey: ["audit", "actions"], queryFn: () => api.audit.actions({ limit: 100 }), refetchInterval: 30_000 });
  const events = useQuery({ queryKey: ["audit", "events"], queryFn: () => api.audit.events({ limit: 100 }), refetchInterval: 30_000 });
  const verify = useMutation({ mutationFn: api.audit.verify });
  const [rev, setRev] = useState<any>(null);
  const [expErr, setExpErr] = useState<unknown>(null);
  const exp = async (kind: string) => { setExpErr(null); try { await download("/audit/export", { kind, format: "csv" }, `${kind}.csv`); } catch (e) { setExpErr(e); } };

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => verify.mutate()} loading={verify.isPending}><ShieldCheck /> Verify the log hasn't been altered</Button>
        {verify.data && <Chip tone={verify.data.valid ? "go" : "signal"}>{verify.data.valid ? `Intact: ${verify.data.checked.toLocaleString()} entries checked` : `Broken at entry ${verify.data.first_break_id}`}</Chip>}
        <span className="ml-auto flex gap-2"><Button size="sm" onClick={() => exp("agent_actions")}><Download /> Agent actions (CSV)</Button><Button size="sm" onClick={() => exp("events")}><Download /> User actions (CSV)</Button></span>
      </div>
      <ErrorNote error={expErr} className="mb-3" />
      <Tabs defaultValue="agent">
        <TabsList><TabsTrigger value="agent">Agent actions</TabsTrigger><TabsTrigger value="user">User actions</TabsTrigger></TabsList>
        <TabsContent value="agent">
          {actions.isLoading && <Skeleton className="h-40" />}
          <ErrorNote error={actions.error} />
          <ul className="hairlines rounded-panel border border-line bg-surface px-4">
            {actions.data?.map((a) => (
              <li key={a.id} className="py-2.5 text-sm">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="font-medium">{a.tool_name.replace(/_/g, " ")}</span>
                  <Chip>{a.trigger.replace("_", " ")}</Chip><Chip tone="teal">{TIER[a.autonomy_level] ?? a.autonomy_level}</Chip>
                  {a.status !== "executed" && <Chip tone={a.status === "reverted" || a.status === "rejected" ? "signal" : "amber"}>{a.status}</Chip>}
                  {a.confidence != null && <span className="text-muted">confidence {Math.round(a.confidence * 100)}%</span>}
                  {a.high_impact && <Chip tone="amber">high impact</Chip>}
                  <time className="ml-auto text-xs text-muted" dateTime={a.created_at}>{fmtDateTime(a.created_at)}</time>
                </div>
                <p className="text-muted">{a.summary}</p>
                {a.reversible && !a.reverted_at && <button type="button" onClick={() => setRev(a)} className="mt-1 inline-flex items-center gap-1 text-sm text-teal-ink hover:underline"><Undo2 className="size-3.5" aria-hidden /> Reverse</button>}
              </li>
            ))}
            {actions.data?.length === 0 && <li className="py-6 text-center text-sm text-muted">No agent actions yet.</li>}
          </ul>
        </TabsContent>
        <TabsContent value="user">
          {events.isLoading && <Skeleton className="h-40" />}
          <ErrorNote error={events.error} />
          <ul className="hairlines rounded-panel border border-line bg-surface px-4">
            {events.data?.map((e) => (
              <li key={e.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-2 text-sm">
                <span className="font-medium">{e.action}</span><span className="text-muted">{e.actor_label ?? e.actor_type}</span>{e.resource_type && <span className="text-muted">{e.resource_type}</span>}
                <time className="ml-auto text-xs text-muted" dateTime={e.ts}>{fmtDateTime(e.ts)}</time>
              </li>
            ))}
          </ul>
        </TabsContent>
      </Tabs>
      {rev && <RevertDialog id={rev.id} summary={rev.summary} open onOpenChange={(o) => !o && setRev(null)} />}
    </div>
  );
}
