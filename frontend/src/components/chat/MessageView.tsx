import { ChevronDown, ThumbsDown, ThumbsUp, Wrench } from "lucide-react";
import { useState } from "react";
import { CardView } from "@/components/chat/Cards";
import { TermsInText } from "@/components/glossary/Term";
import { Markdown } from "@/components/shared/Markdown";
import { Chip } from "@/components/shared/ui";
import type { ChatMsg } from "@/types";
import { cn } from "@/utils/cn";
import { fmtDateTime, pct } from "@/utils/format";

const TOOL_LABEL: Record<string, string> = {
  log_search: "Searched logs", health_state: "Read service health", root_cause_analysis: "Traced the root cause", incident_report_generation: "Drafted a report",
  deployment_comparison: "Compared deployments", proactive_failure_forecasting: "Checked failure risk", "conversational_chat.explain": "Explained the message",
};

const confTone = { high: "go", medium: "amber", low: "signal" } as const;

function Confidence({ m }: { m: ChatMsg }) {
  if (m.confidence == null || !m.confidence_label) return null;
  return (
    <Chip tone={confTone[m.confidence_label]} title="How well the evidence supports this answer">
      {m.confidence_label[0].toUpperCase() + m.confidence_label.slice(1)} confidence, {pct(m.confidence)}
    </Chip>
  );
}

function Evidence({ m }: { m: ChatMsg }) {
  const [open, setOpen] = useState(false);
  const src = m.sources ?? [];
  if (!src.length) return null;
  return (
    <div className="mt-3">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="flex items-center gap-1 text-sm font-medium text-teal-ink">
        <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} aria-hidden /> Evidence from the logs ({src.length})
      </button>
      {open && (
        <ol className="mt-2 space-y-2">
          {src.map((s) => (
            <li key={s.id} className="rounded-control border border-line bg-raised px-2.5 py-2 text-sm">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className="tabular font-semibold">[{s.n}]</span><span className="font-medium">{s.service}</span><span className="text-muted">{fmtDateTime(s.timestamp)}</span>
                {s.count ? <span className="text-muted">seen {s.count.toLocaleString()} times</span> : null}
                <span className="ml-auto text-xs text-muted">{s.source?.filename ? `${s.source.filename}${s.source.line_no ? `:${s.source.line_no}` : ""}` : ""}</span>
              </div>
              <p className="mt-0.5 break-words font-mono text-[12.5px] text-muted">{s.message}</p>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

export function MessageView({ m, onFollowup, onRate }: { m: ChatMsg; onFollowup: (q: string) => void; onRate: (id: string, r: 1 | -1) => void }) {
  if (m.role === "user") {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-panel rounded-br-sm bg-ink px-3.5 py-2 text-[15px] text-paper">{m.content}</p>
      </div>
    );
  }
  const canRate = !m.pending && !m.error && !m.id.startsWith("a-");
  return (
    <article className="max-w-full" aria-label="Agent answer" aria-busy={m.pending}>
      {!!m.tools?.length && (
        <ul className="mb-2 flex flex-wrap gap-1.5" aria-label="Tools the agent used">
          {m.tools.map((t, i) => (
            <li key={i}><Chip tone={t.ok ? "teal" : "amber"}><Wrench className="size-3" aria-hidden />{TOOL_LABEL[t.tool] ?? t.tool}{t.ms != null && t.ok ? `, ${t.ms} ms` : t.ok ? "" : ", unavailable"}</Chip></li>
          ))}
        </ul>
      )}
      {m.cards && m.cards.length > 0 && <div className="mb-3 space-y-2">{m.cards.map((c, i) => <CardView key={i} card={c} />)}</div>}
      {m.error ? (
        <p role="alert" className="rounded-control bg-signal-soft px-3 py-2 text-sm text-signal-ink">{m.content}</p>
      ) : m.content ? (
        <>
          <Markdown>{m.content}</Markdown>
          {m.pending && <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse rounded-sm bg-teal align-middle" aria-hidden />}
        </>
      ) : (
        <p className="flex items-center gap-1.5 text-sm text-muted" role="status">
          <span className="relative flex size-1.5 shrink-0">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-teal opacity-60" aria-hidden />
            <span className="relative inline-flex size-1.5 rounded-full bg-teal" aria-hidden />
          </span>
          <span className="animate-breathe">{m.steps?.length ? m.steps[m.steps.length - 1] : "Thinking..."}</span>
        </p>
      )}
      {!m.pending && !m.error && (
        <>
          <div className="mt-2 flex flex-wrap items-center gap-2"><Confidence m={m} />
            {canRate && (
              <span className="ml-auto flex items-center gap-1" role="group" aria-label="Was this helpful?">
                <button type="button" onClick={() => onRate(m.id, 1)} aria-pressed={m.rating === 1} aria-label="Helpful" className={cn("rounded-control p-1.5 text-muted hover:bg-ink/[.06]", m.rating === 1 && "bg-go-soft text-go")}><ThumbsUp className="size-4" /></button>
                <button type="button" onClick={() => onRate(m.id, -1)} aria-pressed={m.rating === -1} aria-label="Not helpful" className={cn("rounded-control p-1.5 text-muted hover:bg-ink/[.06]", m.rating === -1 && "bg-signal-soft text-signal")}><ThumbsDown className="size-4" /></button>
              </span>
            )}
          </div>
          <TermsInText text={m.content} />
          <Evidence m={m} />
          {!!m.followups?.length && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {m.followups.map((f) => <button key={f} type="button" onClick={() => onFollowup(f)} className="rounded-full border border-line bg-raised px-3 py-1 text-sm hover:border-teal hover:text-teal-ink">{f}</button>)}
            </div>
          )}
        </>
      )}
    </article>
  );
}
