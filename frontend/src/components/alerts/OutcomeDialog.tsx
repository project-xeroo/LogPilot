import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/api/endpoints";
import { Dialog, DialogClose, DialogContent } from "@/components/shared/overlays";
import { Button, ErrorNote, Input, Label, Textarea } from "@/components/shared/ui";
import { useProjectId } from "@/hooks/useProject";
import { cn } from "@/utils/cn";

const OPTIONS = [
  { v: "prevented", t: "Prevented", d: "The alert was right and we stopped the incident." },
  { v: "occurred", t: "Incident happened", d: "The alert was right but the incident still occurred." },
  { v: "false_positive", t: "False alarm", d: "Nothing was wrong. The agent will treat this pattern more cautiously." },
] as const;

/** After every incident, prevented or not, log what happened. The agent refines its weights and signatures from it. */
export function OutcomeDialog({ alertId, service, open, onOpenChange }: { alertId: string; service: string; open: boolean; onOpenChange: (o: boolean) => void }) {
  const projectId = useProjectId();
  const qc = useQueryClient();
  const [outcome, setOutcome] = useState<string>("prevented");
  const [action, setAction] = useState("");
  const [mins, setMins] = useState("");
  const [notes, setNotes] = useState("");
  const m = useMutation({
    mutationFn: () => api.alerts.outcome(projectId, alertId, { outcome, action_taken: action || undefined, time_to_resolve_minutes: mins ? Number(mins) : undefined, notes: notes || undefined }),
    onSuccess: () => { ["alerts", "alert", "approvals", "risk"].forEach((k) => qc.invalidateQueries({ queryKey: [k, projectId] })); onOpenChange(false); },
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={`Log the outcome for ${service}`} description="This closes the alert and teaches the agent whether it was right.">
        <fieldset className="space-y-2">
          <legend className="mb-1 text-sm font-medium">What happened?</legend>
          {OPTIONS.map((o) => (
            <label key={o.v} className={cn("flex cursor-pointer items-start gap-3 rounded-control border border-line p-2.5", outcome === o.v && "border-teal bg-teal-soft/50")}>
              <input type="radio" name="outcome" value={o.v} checked={outcome === o.v} onChange={() => setOutcome(o.v)} className="mt-1 accent-[hsl(var(--teal))]" />
              <span><span className="block text-sm font-medium">{o.t}</span><span className="text-sm text-muted">{o.d}</span></span>
            </label>
          ))}
        </fieldset>
        <div><Label htmlFor="oc-action">What was done?</Label><Textarea id="oc-action" rows={2} value={action} onChange={(e) => setAction(e.target.value)} placeholder="Restarted session-worker-2 and scaled Redis to 3 replicas" /></div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div><Label htmlFor="oc-mins">Minutes to resolve</Label><Input id="oc-mins" type="number" min={0} value={mins} onChange={(e) => setMins(e.target.value)} /></div>
          <div><Label htmlFor="oc-notes">Notes</Label><Input id="oc-notes" value={notes} onChange={(e) => setNotes(e.target.value)} /></div>
        </div>
        <ErrorNote error={m.error} />
        <div className="flex justify-end gap-2"><DialogClose asChild><Button>Cancel</Button></DialogClose><Button variant="primary" loading={m.isPending} onClick={() => m.mutate()}>Save outcome</Button></div>
      </DialogContent>
    </Dialog>
  );
}
