import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/endpoints";
import { AskAgent } from "@/components/shared/AskAgent";
import { EtaText, levelOf, RiskFigure, RiskRunway } from "@/components/shared/RiskRunway";
import { Sparkline } from "@/components/shared/Sparkline";
import { Chip } from "@/components/shared/ui";
import { Term } from "@/components/glossary/Term";
import { useProjectId } from "@/hooks/useProject";
import type { RiskRow } from "@/types";
import { cn } from "@/utils/cn";
import { timeAgo } from "@/utils/format";

function Trend({ id }: { id: string }) {
  const projectId = useProjectId();
  const q = useQuery({ queryKey: ["risk-history", projectId, id], queryFn: () => api.risk.history(projectId, id, 3), staleTime: 60_000, refetchInterval: 60_000 });
  return <Sparkline data={(q.data?.points ?? []).map((p) => p.risk)} domain={[0, 100]} height={30} />;
}

/**
 * One row per monitored service. The runway shows where its risk sits relative to the warning and critical lines;
 * the figure, trend arrow and estimated time to impact sit beside it. Selecting a row opens the full breakdown.
 */
export function RiskStrip({ rows, selected, onSelect }: { rows: RiskRow[]; selected: string | null; onSelect: (id: string) => void }) {
  return (
    <div className="rounded-panel border border-line bg-surface" role="list" aria-label="Services by failure risk">
      <div className="hidden grid-cols-[minmax(9rem,13rem)_minmax(10rem,1fr)_6rem_5rem_11rem] items-end gap-4 border-b border-line px-4 py-2 text-sm text-muted md:grid">
        <span>Service</span>
        <span><Term term="failure risk score" block>Risk against the warning and critical lines</Term></span>
        <span>Last 3 hours</span>
        <span className="text-right">Score</span>
        <span>Time to impact</span>
      </div>
      <ul className="hairlines">
        {rows.map((r) => {
          const lv = levelOf(r.risk_score, r.thresholds.warning, r.thresholds.critical);
          const active = selected === r.service_id;
          return (
            <li key={r.service_id} role="listitem">
              <div className={cn("relative grid w-full grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 px-4 py-3 text-left md:grid-cols-[minmax(9rem,13rem)_minmax(10rem,1fr)_6rem_5rem_11rem]", active && "bg-teal-soft/60")}>
                <button type="button" onClick={() => onSelect(r.service_id)} aria-pressed={active} className="absolute inset-0 focus-visible:z-10" aria-label={`Show details for ${r.service}`} />
                <div className="pointer-events-none min-w-0">
                  <div className="truncate font-medium">{r.service}</div>
                  <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
                    {r.open_alert_id && <Chip tone={lv === "critical" ? "signal" : "amber"}>alert open</Chip>}
                    {r.degraded && <Chip tone="amber" title="AI reasoning was unavailable; scored from thresholds only">degraded</Chip>}
                    <span>{r.as_of ? `updated ${timeAgo(r.as_of)}` : "waiting for first forecast"}</span>
                  </div>
                </div>
                <div className="pointer-events-none col-span-2 pb-3.5 md:col-span-1 md:pb-3"><RiskRunway score={r.risk_score} warning={r.thresholds.warning} critical={r.thresholds.critical} /></div>
                <div className="pointer-events-none hidden md:block"><Trend id={r.service_id} /></div>
                <div className="pointer-events-none order-2 text-right md:order-none"><RiskFigure score={r.risk_score} level={lv} trend={r.trend} /></div>
                <div className="pointer-events-none order-3 flex items-center justify-between gap-2 md:order-none">
                  <span className="text-sm text-muted"><EtaText low={r.eta_minutes_low} high={r.eta_minutes_high} />{lv === "ok" && !r.eta_minutes_low && <span>{r.primary_signals[0] ?? "Calm"}</span>}</span>
                  <span className="pointer-events-auto relative z-20"><AskAgent prompt={`Why is ${r.service} ${lv === "ok" ? "at " + Math.round(r.risk_score ?? 0) + "/100" : "flagged"}?`} iconOnly label="Why is this red?" /></span>
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
