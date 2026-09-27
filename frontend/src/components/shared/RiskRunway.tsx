import { ArrowDownRight, ArrowRight, ArrowUpRight } from "lucide-react";
import { cn } from "@/utils/cn";
import { eta } from "@/utils/format";
import type { Level } from "@/types";

/**
 * The risk runway: one service's failure risk laid along a 0-100 track with tick marks at the warning and
 * critical thresholds, so "how close is it to the line" is readable at a glance - even from across a room
 * at 3am. Colour appears only when risk does: teal-grey while calm, amber past warning, vermilion past critical.
 */
export function levelOf(score: number | null | undefined, warning = 60, critical = 80): Level {
  if (score == null) return "ok";
  return score >= critical ? "critical" : score >= warning ? "warning" : "ok";
}

const fill: Record<Level, string> = { ok: "bg-teal/55", warning: "bg-amber", critical: "bg-signal" };
const text: Record<Level, string> = { ok: "text-ink", warning: "text-amber-ink", critical: "text-signal-ink" };

export function TrendIcon({ trend, className }: { trend?: string | null; className?: string }) {
  const c = cn("size-4", className);
  if (trend === "rising") return <ArrowUpRight className={cn(c, "text-signal-ink")} aria-label="rising" />;
  if (trend === "falling") return <ArrowDownRight className={cn(c, "text-go")} aria-label="falling" />;
  return <ArrowRight className={cn(c, "text-muted")} aria-label="steady" />;
}

export function RiskRunway({ score, warning = 60, critical = 80, className, compact }: { score: number | null; warning?: number; critical?: number; className?: string; compact?: boolean }) {
  const level = levelOf(score, warning, critical);
  const v = Math.max(0, Math.min(100, score ?? 0));
  return (
    <div className={cn("relative", compact ? "h-5" : "h-7", className)} role="img" aria-label={score == null ? "No risk score yet" : `Risk ${v.toFixed(0)} of 100, ${level === "ok" ? "below the warning threshold" : level}`}>
      <div className="absolute inset-x-0 top-1/2 h-2 -translate-y-1/2 overflow-hidden rounded-full bg-ink/[.08]">
        <div className={cn("h-full rounded-full transition-[width] duration-500", fill[level])} style={{ width: `${v}%` }} />
      </div>
      {[warning, critical].map((t, i) => (
        <div key={t} className="absolute top-0 h-full" style={{ left: `${t}%` }}>
          <div className={cn("absolute left-0 top-[3px] h-[calc(100%-6px)] w-px", i === 0 ? "bg-amber" : "bg-signal")} />
          {!compact && <span className="absolute -bottom-3.5 -translate-x-1/2 text-[10.5px] text-muted tabular">{t}</span>}
        </div>
      ))}
    </div>
  );
}

export function RiskFigure({ score, level, trend }: { score: number | null; level: Level; trend?: string | null }) {
  return (
    <span className={cn("tabular inline-flex items-baseline gap-1.5 text-2xl font-semibold", text[level])}>
      {score == null ? "–" : Math.round(score)}
      <TrendIcon trend={trend} className="self-center" />
    </span>
  );
}

export function EtaText({ low, high, className }: { low?: number | null; high?: number | null; className?: string }) {
  const t = eta(low, high);
  if (!t) return null;
  return <span className={cn("text-sm text-muted", className)}>{t === "impact imminent" ? "Impact imminent" : `Est. impact in ${t.replace("about ", "")}`}</span>;
}

export const levelTone = { ok: "neutral", warning: "amber", critical: "signal" } as const;
