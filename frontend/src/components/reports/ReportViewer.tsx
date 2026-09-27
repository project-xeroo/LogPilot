import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Download, Pencil, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/api/endpoints";
import { download } from "@/api/client";
import { Markdown } from "@/components/shared/Markdown";
import { Button, Chip, ErrorNote, Input, Skeleton, Textarea } from "@/components/shared/ui";
import { useProjectId } from "@/hooks/useProject";
import { can, useSession } from "@/store/session";
import type { ReportSection } from "@/types";
import { fmtDateTime } from "@/utils/format";

const KIND = { incident: "Incident report", pre_mortem: "Pre-mortem", executive_summary: "Executive summary" } as const;

/** Read, edit in place, sign off and export an agent-drafted report. Editing an approved report voids the sign-off. */
export function ReportViewer({ reportId, onGone }: { reportId: string; onGone: () => void }) {
  const projectId = useProjectId();
  const user = useSession((s) => s.user);
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["report", projectId, reportId], queryFn: () => api.reports.get(projectId, reportId) });
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");
  const [secs, setSecs] = useState<ReportSection[]>([]);
  const [exportErr, setExportErr] = useState<unknown>(null);
  useEffect(() => { if (q.data) { setTitle(q.data.title); setSecs(q.data.sections ?? []); } }, [q.data]);
  useEffect(() => setEditing(false), [reportId]);

  const inval = () => { qc.invalidateQueries({ queryKey: ["report", projectId, reportId] }); qc.invalidateQueries({ queryKey: ["reports", projectId] }); };
  const save = useMutation({ mutationFn: () => api.reports.edit(projectId, reportId, { title, sections: secs }), onSuccess: () => { setEditing(false); inval(); } });
  const approve = useMutation({ mutationFn: () => api.reports.approve(projectId, reportId), onSuccess: inval });
  const discard = useMutation({ mutationFn: () => api.reports.discard(projectId, reportId), onSuccess: () => { inval(); onGone(); } });

  if (q.isLoading) return <Skeleton className="h-96" />;
  if (q.error) return <ErrorNote error={q.error} />;
  const r = q.data!;
  const canEdit = can(user, "reports.edit");
  const exp = async (f: "pdf" | "markdown") => {
    setExportErr(null);
    try { await download(`/projects/${projectId}/reports/${reportId}/export`, { format: f }, `report.${f === "pdf" ? "pdf" : "md"}`); } catch (e) { setExportErr(e); }
  };

  return (
    <article className="rounded-panel border border-line bg-surface" aria-label={r.title}>
      {r.status !== "approved" && <p role="note" className="rounded-t-panel bg-amber-soft px-4 py-2 text-sm text-amber-ink">Draft. The agent wrote this and nobody has signed it off yet. Exports carry a draft mark until you approve it.</p>}
      <header className="flex flex-wrap items-start gap-3 border-b border-line px-5 py-4">
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex flex-wrap items-center gap-2"><Chip tone="teal">{KIND[r.kind]}</Chip><Chip tone={r.status === "approved" ? "go" : r.status === "discarded" ? "signal" : "amber"}>{r.status === "approved" ? "Signed off" : r.status === "discarded" ? "Discarded" : "Draft"}</Chip></div>
          {editing ? <Input value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Report title" className="text-lg font-semibold" /> : <h2 className="text-xl">{r.title}</h2>}
          <p className="mt-1 text-sm text-muted">Drafted {fmtDateTime(r.created_at)}{r.generation_ms ? ` in ${(r.generation_ms / 1000).toFixed(1)} s` : ""}{r.ai_narrated === false ? ", composed directly from log data" : ""}{r.approved_at ? `, signed off ${fmtDateTime(r.approved_at)}` : ""}.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {canEdit && !editing && r.status !== "discarded" && <Button size="sm" onClick={() => setEditing(true)}><Pencil /> Edit</Button>}
          {editing && (<><Button size="sm" onClick={() => { setEditing(false); setTitle(r.title); setSecs(r.sections ?? []); }}>Cancel</Button><Button size="sm" variant="primary" loading={save.isPending} onClick={() => save.mutate()}>Save changes</Button></>)}
          {canEdit && !editing && r.status === "draft" && <Button size="sm" variant="primary" loading={approve.isPending} onClick={() => approve.mutate()}><CheckCircle2 /> Approve</Button>}
          <Button size="sm" onClick={() => exp("pdf")}><Download /> PDF</Button>
          <Button size="sm" onClick={() => exp("markdown")}><Download /> Markdown</Button>
          {canEdit && r.status !== "discarded" && <Button size="sm" variant="ghost" aria-label="Discard report" onClick={() => discard.mutate()} loading={discard.isPending}><Trash2 /></Button>}
        </div>
      </header>
      <ErrorNote error={save.error || approve.error || discard.error || exportErr} className="mx-5 mt-3" />
      <div className="space-y-6 px-5 py-5">
        {(editing ? secs : r.sections ?? []).map((s, i) => (
          <section key={s.key} aria-labelledby={`sec-${s.key}`}>
            <h3 id={`sec-${s.key}`} className="mb-1.5 text-lg">{s.title}</h3>
            {editing ? <Textarea rows={Math.min(14, Math.max(4, s.body.split("\n").length + 1))} value={s.body} onChange={(e) => setSecs(secs.map((x, j) => (j === i ? { ...x, body: e.target.value } : x)))} aria-label={s.title} className="font-mono text-[13px]" /> : <Markdown>{s.body}</Markdown>}
          </section>
        ))}
      </div>
    </article>
  );
}
