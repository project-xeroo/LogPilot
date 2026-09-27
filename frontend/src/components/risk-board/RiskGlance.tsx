import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "@/api/endpoints";
import { AskAgent } from "@/components/shared/AskAgent";
import { EtaText, levelOf, RiskFigure, RiskRunway } from "@/components/shared/RiskRunway";
import { Skeleton } from "@/components/shared/ui";
import { useProjectId } from "@/hooks/useProject";
import { can, useSession } from "@/store/session";

/** The agent's standing judgment, in one sentence and three runways. Shown at the top of the home feed. */
export function RiskGlance() {
  const projectId = useProjectId();
  const user = useSession((s) => s.user);
  const q = useQuery({ queryKey: ["risk", projectId], queryFn: () => api.risk.board(projectId), enabled: !!projectId && can(user, "risk.view"), refetchInterval: 30_000 });
  if (!can(user, "risk.view")) return null;
  if (q.isLoading) return <Skeleton className="h-36" />;
  const rows = (q.data?.services ?? []).filter((s) => s.risk_score != null);
  if (!rows.length) return null;
  const hot = rows.filter((r) => r.level === "warning" || r.level === "critical");
  const crit = rows.filter((r) => r.level === "critical");
  const top = rows.slice(0, 3);
  const sentence = crit.length
    ? `${crit.length} service${crit.length > 1 ? "s are" : " is"} past the critical line. ${crit[0].service} needs attention first.`
    : hot.length
      ? `${hot.length} service${hot.length > 1 ? "s are" : " is"} above the warning line, led by ${hot[0].service}.`
      : "Every monitored service is below the warning line. I'm still watching.";
  return (
    <section aria-label="Failure risk at a glance" className="rounded-panel border border-line bg-surface p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="agent-voice text-[16px]">{sentence}</p>
        <Link to="/risk" className="text-sm font-medium text-teal-ink hover:underline">Open the risk board</Link>
      </div>
      <ul className="mt-3 hairlines">
        {top.map((r) => {
          const lv = levelOf(r.risk_score, r.thresholds.warning, r.thresholds.critical);
          return (
            <li key={r.service_id} className="grid grid-cols-[minmax(6rem,9rem)_1fr_auto] items-center gap-3 py-2 sm:grid-cols-[minmax(7rem,10rem)_1fr_4.5rem_auto]">
              <span className="truncate text-sm font-medium">{r.service}</span>
              <RiskRunway score={r.risk_score} warning={r.thresholds.warning} critical={r.thresholds.critical} compact />
              <span className="hidden text-right sm:block"><EtaText low={r.eta_minutes_low} high={r.eta_minutes_high} className="text-xs" /></span>
              <span className="flex items-center gap-1"><RiskFigure score={r.risk_score} level={lv} trend={r.trend} />
                <AskAgent prompt={`Why is ${r.service} ${lv === "ok" ? "at this risk level" : "flagged"}?`} label="Why" iconOnly /></span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
