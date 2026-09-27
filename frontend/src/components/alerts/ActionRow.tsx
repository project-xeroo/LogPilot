import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Lock, Pencil, X } from "lucide-react";
import { useState } from "react";
import { api } from "@/api/endpoints";
import { Dialog, DialogClose, DialogContent, Tip } from "@/components/shared/overlays";
import { Button, Chip, ErrorNote, Label, Textarea } from "@/components/shared/ui";
import { useProjectId } from "@/hooks/useProject";
import { useSession } from "@/store/session";
import type { Action } from "@/types";
import { cn } from "@/utils/cn";

const SOURCE = { historical: "Worked before", llm: "Agent's reasoning", playbook: "Your playbook" } as const;

/** Why a person can't decide on this action (server enforces the same rules). null = they can. */
export function blockReason(role: string | undefined, guided: boolean, a: Pick<Action, "risk_level">): string | null {
  if (role === "junior_engineer") return "Approvals are withheld for Junior Engineer accounts until an Admin or SRE promotes the account. You can still discuss this action in chat.";
  if (role === "viewer") return "Viewers can see proposed actions but can't decide on them.";
  if (guided && a.risk_level === "high") return "This is a high-risk action. In guided mode it goes to a Developer, SRE or Admin, so you aren't the last line of defence on a production call.";
  return null;
}

/**
 * A propose-only recommended action. The agent never executes it; a person approves, edits or dismisses it
 * (dismissing requires a reason, which the agent records and learns from).
 */
export function ActionRow({ a, onDone }: { a: Action; onDone?: () => void }) {
  const projectId = useProjectId();
  const role = useSession((s) => s.user?.role);
  const guided = useSession((s) => s.guided);
  const qc = useQueryClient();
  const [dlg, setDlg] = useState<null | "edit" | "dismiss">(null);
  const [text, setText] = useState(a.text);
  const [reason, setReason] = useState("");
  const blocked = blockReason(role, guided, a);
  const decided = a.status !== "proposed";

  const refresh = () => { ["alerts", "alert", "approvals"].forEach((k) => qc.invalidateQueries({ queryKey: [k, projectId] })); onDone?.(); setDlg(null); };
  const approve = useMutation({ mutationFn: () => api.alerts.approveAction(projectId, a.id), onSuccess: refresh });
  const edit = useMutation({ mutationFn: () => api.alerts.editAction(projectId, a.id, text), onSuccess: refresh });
  const dismiss = useMutation({ mutationFn: () => api.alerts.dismissAction(projectId, a.id, reason), onSuccess: refresh });
  const err = approve.error || edit.error || dismiss.error;

  const lockTip = (btn: React.ReactElement) => (blocked ? <Tip label={blocked}><span tabIndex={0} className="inline-flex">{btn}</span></Tip> : btn);

  return (
    <li className="py-3">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
        <span className="tabular mt-0.5 w-5 shrink-0 text-sm font-semibold text-muted">{a.rank}.</span>
        <div className="min-w-0 flex-1">
          <p className={cn("agent-voice text-[15px]", a.status === "dismissed" && "text-muted line-through")}>{a.text}</p>
          {a.rationale && <p className="text-sm text-muted">{a.rationale}</p>}
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <Chip tone="neutral">{SOURCE[a.source]}</Chip>
            <Chip tone={a.risk_level === "high" ? "amber" : "neutral"}>{a.risk_level === "high" ? "High risk" : "Low risk"}</Chip>
            <Chip tone="teal">Propose-only</Chip>
            {decided && <Chip tone={a.status === "dismissed" ? "signal" : "go"}>{a.status === "dismissed" ? "Dismissed" : "Approved"}</Chip>}
          </div>
          {a.dismiss_reason && <p className="mt-1 text-sm text-muted">Reason: {a.dismiss_reason}</p>}
        </div>
        {!decided && (
          <div className="flex items-center gap-1.5">
            {lockTip(<Button size="sm" variant="primary" disabled={!!blocked} loading={approve.isPending} onClick={() => approve.mutate()}>{blocked ? <Lock /> : <Check />} Approve</Button>)}
            {lockTip(<Button size="sm" disabled={!!blocked} onClick={() => { setText(a.text); setDlg("edit"); }}><Pencil /> Edit</Button>)}
            {lockTip(<Button size="sm" variant="ghost" disabled={!!blocked} onClick={() => setDlg("dismiss")}><X /> Dismiss</Button>)}
          </div>
        )}
      </div>
      {blocked && !decided && <p className="mt-1.5 pl-8 text-sm text-muted"><Lock className="mr-1 inline size-3.5" aria-hidden />{blocked}</p>}
      <ErrorNote error={err} className="mt-2" />
      {approve.data?.note && <p className="mt-1.5 pl-8 text-sm text-go">{approve.data.note}</p>}

      <Dialog open={dlg === "edit"} onOpenChange={(o) => !o && setDlg(null)}>
        <DialogContent title="Edit and approve this action" description="Adjust the wording so it matches what your team will actually do. It's recorded as approved.">
          <div><Label htmlFor={`edit-${a.id}`}>Action</Label><Textarea id={`edit-${a.id}`} rows={4} value={text} onChange={(e) => setText(e.target.value)} /></div>
          <ErrorNote error={edit.error} />
          <div className="flex justify-end gap-2"><DialogClose asChild><Button>Cancel</Button></DialogClose><Button variant="primary" disabled={text.trim().length < 3} loading={edit.isPending} onClick={() => edit.mutate()}>Save and approve</Button></div>
        </DialogContent>
      </Dialog>
      <Dialog open={dlg === "dismiss"} onOpenChange={(o) => !o && setDlg(null)}>
        <DialogContent title="Dismiss this action" description="Say why. The agent records your reason and uses it to make better recommendations.">
          <div><Label htmlFor={`why-${a.id}`}>Reason</Label><Textarea id={`why-${a.id}`} rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="For example: already scaled manually, or not applicable to this environment" /></div>
          <ErrorNote error={dismiss.error} />
          <div className="flex justify-end gap-2"><DialogClose asChild><Button>Cancel</Button></DialogClose><Button variant="danger" disabled={reason.trim().length < 3} loading={dismiss.isPending} onClick={() => dismiss.mutate()}>Dismiss action</Button></div>
        </DialogContent>
      </Dialog>
    </li>
  );
}
