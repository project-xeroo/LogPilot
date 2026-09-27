import { BellRing, FileWarning, X } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/shared/ui";
import { AskAgent } from "@/components/shared/AskAgent";
import { Markdown } from "@/components/shared/Markdown";
import { useLive } from "@/store/live";
import { cn } from "@/utils/cn";

/**
 * Nothing the agent says autonomously hides behind a screen the person has to think to open: proactive alerts and
 * pre-mortems interrupt the console here, on whatever page they are on. Announced politely to screen readers.
 */
export function AlertInterrupts() {
  const items = useLive((s) => s.interrupts);
  const dismiss = useLive((s) => s.dismiss);
  const nav = useNavigate();
  if (!items.length) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 top-0 z-[70] flex flex-col items-center gap-2 p-3" role="region" aria-label="Agent alerts" aria-live="assertive">
      {items.slice(0, 3).map((i) => {
        const critical = i.level === "critical" || i.kind === "escalated" || i.kind === "pre_mortem";
        return (
          <div key={i.key} className={cn("pointer-events-auto flex w-full max-w-md animate-drop items-start gap-2 rounded-control border bg-raised p-2.5 shadow-lg", critical ? "border-signal" : "border-amber")}>
            <span className={cn("mt-0.5 grid size-6 shrink-0 place-items-center rounded-full", critical ? "bg-signal-soft text-signal-ink animate-ring" : "bg-amber-soft text-amber-ink")}>
              {i.kind === "pre_mortem" ? <FileWarning className="size-3.5" aria-hidden /> : <BellRing className="size-3.5" aria-hidden />}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold leading-snug">{i.title}</p>
              {i.body && <Markdown className="mt-0.5 line-clamp-2 text-[13px] leading-snug">{i.body}</Markdown>}
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                {/* tab=open: a plain autonomous alert only renders under "Open alerts", not "Needs a decision" */}
                {i.alertId && <Button size="sm" variant="primary" onClick={() => { nav(`/alerts?tab=open&open=${i.alertId}`); dismiss(i.key); }}>Review alert</Button>}
                {i.reportId && <Button size="sm" variant="primary" onClick={() => { nav(`/reports?open=${i.reportId}`); dismiss(i.key); }}>Open pre-mortem</Button>}
                {i.service && <AskAgent prompt={`Why is ${i.service} flagged?`} label="Why is this flagged?" />}
              </div>
            </div>
            <button type="button" onClick={() => dismiss(i.key)} className="rounded-control p-1 text-muted hover:bg-ink/[.06]" aria-label="Dismiss alert"><X className="size-3.5" /></button>
          </div>
        );
      })}
    </div>
  );
}
