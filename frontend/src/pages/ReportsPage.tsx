import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FilePlus2, FileText } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "@/api/endpoints";
import { ReportViewer } from "@/components/reports/ReportViewer";
import { Dialog, DialogClose, DialogContent } from "@/components/shared/overlays";
import { Button, Chip, EmptyState, ErrorNote, Label, PageHeader, Select, Skeleton } from "@/components/shared/ui";
import { useProjectId } from "@/hooks/useProject";
import { can, useSession } from "@/store/session";
import { cn } from "@/utils/cn";
import { timeAgo } from "@/utils/format";

const KIND = { incident: "Incident", pre_mortem: "Pre-mortem", executive_summary: "Summary" } as const;

/** Incident & Pre-Mortem Reports: draft, edit and export agent-authored reports. */
export default function ReportsPage() {
  const projectId = useProjectId();
  const user = useSession((s) => s.user);
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const open = params.get("open");
  const [kind, setKind] = useState("");
  const [dlg, setDlg] = useState(false);
  const [svc, setSvc] = useState("");
  const list = useQuery({ queryKey: ["reports", projectId, kind], queryFn: () => api.reports.list(projectId, { kind: kind || undefined }), enabled: !!projectId, refetchInterval: 30_000 });
  const services = useQuery({ queryKey: ["services", projectId], queryFn: () => api.services(projectId), enabled: dlg });
  const setOpen = (id: string | null) => { const p = new URLSearchParams(params); id ? p.set("open", id) : p.delete("open"); setParams(p, { replace: true }); };

  const draft = useMutation({
    mutationFn: () => api.reports.incident(projectId, svc ? { service: svc } : {}),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["reports", projectId] }); setDlg(false); setOpen(r.report_id); },
  });
  const exec = useMutation({ mutationFn: () => api.reports.executive(projectId, 7), onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["reports", projectId] }); setOpen(r.report_id); } });
  const rows = (list.data ?? []).filter((r) => r.status !== "discarded");

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Reports" actions={can(user, "reports.generate") && (<><Button onClick={() => exec.mutate()} loading={exec.isPending}>Weekly summary</Button><Button variant="primary" onClick={() => setDlg(true)}><FilePlus2 /> Draft an incident report</Button></>)}>
        The agent drafts incident reports, pre-mortems and summaries. You review, edit and sign off before anything is exported.
      </PageHeader>
      <ErrorNote error={exec.error} className="mb-3" />
      <div className="grid gap-5 lg:grid-cols-[19rem_minmax(0,1fr)]">
        <aside aria-label="Reports">
          <label className="mb-2 block text-sm"><span className="sr-only">Filter by type</span>
            <Select value={kind} onChange={(e) => setKind(e.target.value)}><option value="">All reports</option><option value="incident">Incident reports</option><option value="pre_mortem">Pre-mortems</option><option value="executive_summary">Summaries</option></Select>
          </label>
          {list.isLoading && <Skeleton className="h-40" />}
          {list.data && rows.length === 0 && <EmptyState icon={<FileText />} title="No reports yet">Pre-mortems are drafted automatically when a service reaches critical risk. You can also ask the agent in chat to write an incident report.</EmptyState>}
          <ul className="hairlines rounded-panel border border-line bg-surface">
            {rows.map((r) => (
              <li key={r.id}>
                <button type="button" onClick={() => setOpen(r.id)} aria-current={open === r.id} className={cn("block w-full px-3.5 py-3 text-left hover:bg-ink/[.04]", open === r.id && "bg-teal-soft/60")}>
                  <div className="line-clamp-2 text-sm font-medium">{r.title}</div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted"><Chip tone={r.kind === "pre_mortem" ? "signal" : "neutral"}>{KIND[r.kind]}</Chip><Chip tone={r.status === "approved" ? "go" : "amber"}>{r.status === "approved" ? "Signed off" : "Draft"}</Chip><span>{timeAgo(r.created_at)}</span></div>
                </button>
              </li>
            ))}
          </ul>
        </aside>
        <div className="min-w-0">
          {open ? <ReportViewer reportId={open} key={open} onGone={() => setOpen(null)} /> : <div className="rounded-panel border border-dashed border-line"><EmptyState title="Choose a report to read">Drafts are marked until someone signs them off.</EmptyState></div>}
        </div>
      </div>

      <Dialog open={dlg} onOpenChange={setDlg}>
        <DialogContent title="Draft an incident report" description="The agent finds the failure window, traces the root cause and writes the report in under two minutes. You review it before sharing.">
          <div><Label htmlFor="rep-svc">Focus on a service (optional)</Label><Select id="rep-svc" value={svc} onChange={(e) => setSvc(e.target.value)}><option value="">The most significant recent incident</option>{services.data?.map((s) => <option key={s.id} value={s.name}>{s.name}</option>)}</Select></div>
          <ErrorNote error={draft.error} />
          <div className="flex justify-end gap-2"><DialogClose asChild><Button>Cancel</Button></DialogClose><Button variant="primary" loading={draft.isPending} onClick={() => draft.mutate()}>{draft.isPending ? "Drafting" : "Draft report"}</Button></div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
