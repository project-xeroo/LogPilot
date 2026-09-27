import { useQuery } from "@tanstack/react-query";
import { Gauge } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/api/endpoints";
import { HealthPanel } from "@/components/risk-board/HealthPanel";
import { RiskDetail } from "@/components/risk-board/RiskDetail";
import { RiskStrip } from "@/components/risk-board/RiskStrip";
import { EmptyState, ErrorNote, PageHeader, Skeleton } from "@/components/shared/ui";
import { useProjectId } from "@/hooks/useProject";

/** Failure Risk Board: the queryable state behind the Failure Risk view, live as the agent re-scores every service. */
export default function RiskBoardPage() {
  const projectId = useProjectId();
  const q = useQuery({ queryKey: ["risk", projectId], queryFn: () => api.risk.board(projectId), enabled: !!projectId, refetchInterval: 20_000 });
  const [sel, setSel] = useState<string | null>(null);
  const rows = q.data?.services ?? [];
  useEffect(() => { if (!sel && rows.length) setSel(rows[0].service_id); }, [rows, sel]);

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <PageHeader title="Failure risk">The agent re-scores every service each minute. Amber and vermilion lines mark the warning (60) and critical (80) thresholds; the bar shows how close each service is.</PageHeader>
      {q.isLoading && <Skeleton className="h-72" />}
      <ErrorNote error={q.error} />
      {!q.isLoading && !q.error && rows.length === 0 && (
        <EmptyState icon={<Gauge />} title="No services are being forecast yet">Upload logs and the agent will discover your services, learn what normal looks like and start scoring them within a minute.</EmptyState>
      )}
      {rows.length > 0 && (
        <>
          <RiskStrip rows={rows} selected={sel} onSelect={setSel} />
          {sel && <RiskDetail serviceId={sel} key={sel} />}
        </>
      )}
      <HealthPanel />
    </div>
  );
}
