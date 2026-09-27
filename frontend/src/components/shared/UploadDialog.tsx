import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, FileUp, ShieldCheck, XCircle } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { api } from "@/api/endpoints";
import { uploadLogs } from "@/api/client";
import { Dialog, DialogContent } from "@/components/shared/overlays";
import { Button, ErrorNote, Input, Label } from "@/components/shared/ui";
import { useProjectId } from "@/hooks/useProject";
import type { LogSession } from "@/types";
import { cn } from "@/utils/cn";
import { bytes, num } from "@/utils/format";

const STAGES: [string, string][] = [
  ["uploading", "Uploading"], ["redacting", "Removing sensitive data"], ["queued", "Queued"], ["parsing", "Reading log lines"],
  ["embedding", "Indexing meaning"], ["deduplication", "Grouping repeated errors"], ["clustering", "Clustering related errors"],
  ["anomaly_detection", "Looking for anomalies"], ["finalizing", "Wrapping up"], ["completed", "Done"],
];

function stageIndex(s: LogSession | null, uploading: boolean) {
  if (uploading) return 0;
  const i = STAGES.findIndex(([k]) => k === (s?.stage ?? ""));
  return i < 0 ? 2 : i;
}

/** Upload logs: validation errors are surfaced, progress is tracked, and processing starts immediately - no other step needed. */
export function UploadDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const projectId = useProjectId();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [env, setEnv] = useState("");
  const [pct, setPct] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [result, setResult] = useState<any>(null);
  const [drag, setDrag] = useState(false);

  const session = useQuery({
    queryKey: ["session", projectId, sessionId],
    queryFn: () => api.session(projectId, sessionId!),
    enabled: !!sessionId,
    refetchInterval: (q) => (q.state.data && ["completed", "failed"].includes(q.state.data.status) ? false : 1500),
  });
  const s = session.data ?? null;

  const reset = () => { setFile(null); setPct(0); setError(null); setSessionId(null); setResult(null); setUploading(false); };
  const pick = useCallback((f: File | undefined) => { if (f) { setFile(f); setError(null); } }, []);

  const start = async () => {
    if (!file) return;
    setError(null);
    setUploading(true);
    try {
      const r = await uploadLogs(projectId, file, { environment: env || undefined, onProgress: setPct });
      setResult(r);
      setSessionId(r.session_id);
    } catch (e: any) {
      setError(e);
      if (e.detail?.session_id) setSessionId(null);
    } finally {
      setUploading(false);
    }
  };

  const done = s?.status === "completed";
  const failed = s?.status === "failed";
  const idx = stageIndex(s, uploading);
  const busy = uploading || (!!sessionId && !done && !failed);
  const redactions = s?.redaction_counts ?? result?.redactions;

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o && !busy) reset(); onOpenChange(o); }}>
      <DialogContent title="Upload logs" description="Files are cleaned of emails, phone numbers, card numbers, passwords and keys before anything is stored. Processing starts as soon as the upload finishes.">
        {!sessionId && (
          <>
            <div
              onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
              onDragLeave={() => setDrag(false)}
              onDrop={(e) => { e.preventDefault(); setDrag(false); pick(e.dataTransfer.files[0]); }}
              className={cn("flex flex-col items-center gap-2 rounded-panel border border-dashed border-line px-4 py-8 text-center", drag && "border-teal bg-teal-soft")}
            >
              <FileUp className="size-7 text-muted" aria-hidden />
              {file ? (
                <p className="text-sm"><strong>{file.name}</strong> <span className="text-muted">({bytes(file.size)})</span></p>
              ) : (
                <p className="text-sm text-muted">Drop a file here, or choose one. Up to 500 MB.</p>
              )}
              <p className="text-xs text-muted">Supported: .log, .txt, .json, .csv, .zip, .gz</p>
              <input ref={input} type="file" accept=".log,.txt,.json,.csv,.zip,.gz,.ndjson,.jsonl" className="sr-only" id="log-file" onChange={(e) => pick(e.target.files?.[0])} />
              <Button variant="secondary" size="sm" onClick={() => input.current?.click()}>{file ? "Choose a different file" : "Choose file"}</Button>
            </div>
            <div>
              <Label htmlFor="upload-env">Environment (optional)</Label>
              <Input id="upload-env" value={env} onChange={(e) => setEnv(e.target.value)} placeholder="production" />
            </div>
            <ErrorNote error={error} />
            {(error as any)?.detail?.errors && <ul className="list-disc pl-5 text-sm text-signal-ink">{(error as any).detail.errors.map((m: string) => <li key={m}>{m}</li>)}</ul>}
            <Button variant="primary" onClick={start} disabled={!file} loading={uploading}>{uploading ? `Uploading ${pct.toFixed(0)}%` : "Upload and analyze"}</Button>
          </>
        )}

        {sessionId && (
          <div className="space-y-4">
            <div className="flex items-center gap-2 text-sm">
              {done ? <CheckCircle2 className="size-5 text-go" aria-hidden /> : failed ? <XCircle className="size-5 text-signal" aria-hidden /> : <span className="size-5 animate-spin rounded-full border-2 border-teal border-t-transparent" aria-hidden />}
              <span className="font-medium">{done ? "Finished" : failed ? "This file could not be processed" : STAGES[idx][1]}</span>
              {s && <span className="ml-auto tabular text-muted">{Math.round(s.progress_pct)}%</span>}
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-ink/10" role="progressbar" aria-valuenow={Math.round(s?.progress_pct ?? 0)} aria-valuemin={0} aria-valuemax={100}>
              <div className={cn("h-full rounded-full transition-all", failed ? "bg-signal" : "bg-teal")} style={{ width: `${s?.progress_pct ?? 0}%` }} />
            </div>
            {s && (
              <dl className="grid grid-cols-2 gap-3 text-sm">
                <div><dt className="text-muted">Records read</dt><dd className="tabular font-medium">{num(s.record_count)}</dd></div>
                <div><dt className="text-muted">Set aside as malformed</dt><dd className="tabular font-medium">{num(s.malformed_count)}</dd></div>
                <div><dt className="text-muted">Format</dt><dd className="font-medium">{s.format_detected ?? "detecting"}</dd></div>
                <div><dt className="text-muted">Size</dt><dd className="font-medium">{bytes(s.size_bytes)}</dd></div>
              </dl>
            )}
            {redactions && redactions.total > 0 && (
              <p className="flex items-start gap-2 rounded-control bg-go-soft px-3 py-2 text-sm text-go">
                <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden />
                <span>{num(redactions.total)} sensitive values were redacted before storage ({Object.entries(redactions.by_type).map(([k, v]) => `${v} ${k.toLowerCase().replace("_", " ")}`).join(", ")}).</span>
              </p>
            )}
            {s?.validation_errors?.length ? <ul className="list-disc pl-5 text-sm text-amber-ink">{s.validation_errors.map((m) => <li key={m}>{m}</li>)}</ul> : null}
            {failed && <ErrorNote error={s?.error_message ?? "Processing failed."} />}
            <div className="flex gap-2">
              {(done || failed) && <Button variant="secondary" onClick={reset}>Upload another</Button>}
              {done && <Button variant="primary" onClick={() => onOpenChange(false)}>Close</Button>}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
