import { useMutation } from "@tanstack/react-query";
import { ChevronDown, Lightbulb } from "lucide-react";
import { useState } from "react";
import { api } from "@/api/endpoints";
import { AskAgent } from "@/components/shared/AskAgent";
import { Chip } from "@/components/shared/ui";
import { useProjectId } from "@/hooks/useProject";
import type { SearchResult } from "@/types";
import { cn } from "@/utils/cn";
import { fmtDateTime } from "@/utils/format";

const sevTone = (s: string) => (s === "ERROR" || s === "FATAL" ? "signal" : s === "WARN" ? "amber" : "neutral") as "signal" | "amber" | "neutral";

function Highlighted({ text, ranges }: { text: string; ranges?: number[][] }) {
  if (!ranges?.length) return <>{text}</>;
  const out: React.ReactNode[] = [];
  let at = 0;
  [...ranges].sort((a, b) => a[0] - b[0]).forEach(([s, e], i) => {
    if (s < at) return;
    out.push(text.slice(at, s), <mark key={i} className="rounded-sm bg-amber/40 px-0.5 text-inherit">{text.slice(s, e)}</mark>);
    at = e;
  });
  out.push(text.slice(at));
  return <>{out}</>;
}

function ContextLine({ r, muted }: { r: SearchResult; muted?: boolean }) {
  return <li className={cn("flex gap-2 break-words", muted && "text-muted")}><span className="w-12 shrink-0 tabular text-muted">{new Date(r.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span><span className="w-11 shrink-0 text-muted">{r.severity}</span><span>{r.message}</span></li>;
}

export function ResultRow({ r, semantic }: { r: SearchResult; semantic: boolean }) {
  const projectId = useProjectId();
  const [ctx, setCtx] = useState(false);
  const explain = useMutation({ mutationFn: () => api.chat.explain(projectId, r.message, r.service) });
  const hasCtx = !!(r.context && (r.context.before.length || r.context.after.length));
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-sm">
        <time dateTime={r.timestamp} className="tabular text-muted">{fmtDateTime(r.timestamp)}</time>
        <span className="font-medium">{r.service}</span>
        <Chip tone={sevTone(r.severity)}>{r.severity}</Chip>
        {r.deployment_version && <span className="text-muted">{r.deployment_version}</span>}
        {semantic && r.score != null && <Chip tone="teal" title="How close this message is in meaning to your question">{Math.round(r.score * 100)}% match</Chip>}
        {semantic && r.occurrences ? <span className="text-muted">seen {r.occurrences.toLocaleString()} times</span> : null}
        <span className="ml-auto text-xs text-muted">{r.source.filename ? `${r.source.filename}${r.source.line_no ? `:${r.source.line_no}` : ""}` : ""}</span>
      </div>
      <p className="mt-1 whitespace-pre-wrap break-words font-mono text-[12.5px] leading-5"><Highlighted text={r.message} ranges={r.highlights} /></p>
      {(r.trace_id || r.request_id) && <p className="mt-0.5 text-xs text-muted">{r.trace_id && <>trace {r.trace_id.slice(0, 16)} </>}{r.request_id && <>request {r.request_id}</>}</p>}
      <div className="mt-1.5 flex flex-wrap items-center gap-1">
        {hasCtx && <button type="button" onClick={() => setCtx(!ctx)} aria-expanded={ctx} className="inline-flex items-center gap-1 rounded-control px-2 py-1 text-sm text-muted hover:bg-ink/[.06]"><ChevronDown className={cn("size-3.5 transition-transform", ctx && "rotate-180")} aria-hidden />Surrounding lines</button>}
        <button type="button" onClick={() => explain.mutate()} className="inline-flex items-center gap-1.5 rounded-control px-2 py-1 text-sm text-teal-ink hover:bg-teal-soft"><Lightbulb className="size-3.5" aria-hidden />What does this mean?</button>
        <AskAgent prompt={`Explain: ${r.message.slice(0, 120)}`} label="Ask the agent" />
      </div>
      {ctx && r.context && (
        <ul className="mt-1 space-y-0.5 rounded-control bg-paper px-3 py-2 font-mono text-[12px] leading-5">
          {r.context.before.map((c) => <ContextLine key={c.id} r={c} muted />)}
          <li className="flex gap-2 font-semibold"><span className="w-12 shrink-0" /><span className="w-11 shrink-0">{r.severity}</span><span className="break-words">this line</span></li>
          {r.context.after.map((c) => <ContextLine key={c.id} r={c} muted />)}
        </ul>
      )}
      {explain.data && (
        <div className="mt-2 rounded-control border border-line bg-raised p-3 text-sm">
          <p className="agent-voice text-[14.5px]">{explain.data.meaning}</p>
          <p className="mt-1.5"><Chip tone={explain.data.is_normal ? "go" : "amber"}>{explain.data.is_normal ? "Routine" : "Worth attention"}</Chip> <span className="text-muted">{explain.data.normality_note}</span></p>
          <p className="mt-1.5 text-muted">{explain.data.why_it_matters}</p>
          <ul className="mt-1.5 list-disc pl-5 text-muted">{explain.data.what_to_check.map((c) => <li key={c}>{c}</li>)}</ul>
        </div>
      )}
      {explain.error && <p className="mt-1 text-sm text-signal-ink" role="alert">{(explain.error as Error).message}</p>}
    </li>
  );
}
