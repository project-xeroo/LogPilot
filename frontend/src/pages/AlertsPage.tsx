import { useQuery } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { api } from "@/api/endpoints";
import { AlertCard } from "@/components/alerts/AlertCard";
import { ActionRow } from "@/components/alerts/ActionRow";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/shared/overlays";
import { Chip, EmptyState, ErrorNote, PageHeader, Skeleton } from "@/components/shared/ui";
import { useProjectId } from "@/hooks/useProject";
import { can, useSession } from "@/store/session";
import { Link } from "react-router-dom";

/** Alerts & Approvals Queue: a sequential pipeline, not three independent views.
 *
 *   flagged -> "Needs a decision" (Admin/SRE/Developer only: approve, edit or dismiss each proposal)
 *           -> "Open alerts" (everyone, including Junior Engineers: work the alert once a call has been made)
 *           -> "History" (resolved/dismissed, unchanged)
 *
 * An alert with a still-undecided recommended action never appears in "Open alerts" - it stays in the
 * decision queue until a senior role acts on it, so a junior engineer's task list is always "already
 * decided, safe to work on" rather than "here's everything, some of which nobody has approved yet". */
export default function AlertsPage() {
  const projectId = useProjectId();
  const user = useSession((s) => s.user);
  const canDecide = can(user, "actions.decide");
  const [params, setParams] = useSearchParams();
  const openId = params.get("open");
  const tab = params.get("tab") ?? (canDecide ? "decide" : "open");
  const approvals = useQuery({ queryKey: ["approvals", projectId], queryFn: () => api.alerts.approvals(projectId), refetchInterval: 20_000 });
  const open = useQuery({ queryKey: ["alerts", projectId, "open"], queryFn: () => api.alerts.list(projectId, "open"), refetchInterval: 20_000 });
  const history = useQuery({ queryKey: ["alerts", projectId, "resolved"], queryFn: () => api.alerts.list(projectId, "resolved"), enabled: tab === "history" });
  const a = approvals.data;
  const waiting = (a?.actions.length ?? 0) + (a?.pending_alerts.length ?? 0);

  // group proposed actions by alert so a person decides in context
  const grouped = new Map<string, NonNullable<typeof a>["actions"]>();
  a?.actions.forEach((x) => grouped.set(x.alert_id, [...(grouped.get(x.alert_id) ?? []), x]));

  // An alert only graduates to "Open alerts" once nothing about it is still awaiting a decision.
  const awaitingDecision = new Set<string>([...(a?.pending_alerts.map((al) => al.id) ?? []), ...grouped.keys()]);
  const openReady = open.data?.filter((al) => !awaitingDecision.has(al.id));
  // "See the full alert" from the decision queue links here even for an alert that isn't "ready" yet -
  // still show it when linked to directly, without adding it to the tab's count or default list.
  const openShown = openId && !openReady?.some((al) => al.id === openId) && open.data?.some((al) => al.id === openId)
    ? [...(openReady ?? []), ...open.data.filter((al) => al.id === openId)]
    : openReady;

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader title="Alerts and approvals">The agent raises alerts before incidents and proposes what to do. It never acts on its own: approve, edit or dismiss each proposal here.</PageHeader>
      {a?.note && <p className="mb-4 rounded-control bg-teal-soft px-3 py-2 text-sm text-teal-ink" role="note">{a.note}</p>}
      <Tabs value={tab} onValueChange={(v) => { const p = new URLSearchParams(params); p.set("tab", v); setParams(p, { replace: true }); }}>
        <TabsList>
          {canDecide && <TabsTrigger value="decide">Needs a decision {waiting > 0 && <Chip tone="signal" className="ml-1.5">{waiting}</Chip>}</TabsTrigger>}
          <TabsTrigger value="open">Open alerts {openReady?.length ? <Chip className="ml-1.5">{openReady.length}</Chip> : null}</TabsTrigger>
          <TabsTrigger value="history">History</TabsTrigger>
        </TabsList>

        <TabsContent value="decide" className="space-y-6">
          {approvals.isLoading && <Skeleton className="h-40" />}
          <ErrorNote error={approvals.error} />
          {a && waiting === 0 && <EmptyState icon={<ShieldCheck />} title="Nothing is waiting for you">When the agent proposes an action or holds an alert for review, it appears here.</EmptyState>}
          {a?.pending_alerts.map((al) => (
            <div key={al.id}><p className="mb-2 text-sm font-medium">Held for review by your autonomy policy</p><AlertCard alertId={al.id} defaultOpen={al.id === openId} /></div>
          ))}
          {[...grouped.entries()].map(([alertId, acts]) => (
            <section key={alertId} className="rounded-panel border border-line bg-surface p-4" aria-label={`Proposals for ${acts[0].service}`}>
              <div className="flex flex-wrap items-baseline gap-2"><h3 className="text-base">{acts[0].service}</h3><Chip tone={acts[0].alert.level === "critical" ? "signal" : "amber"}>{acts[0].alert.level}</Chip><Link to={`/alerts?tab=open&open=${alertId}`} className="ml-auto text-sm text-teal-ink hover:underline">See the full alert</Link></div>
              <p className="agent-voice mt-1 line-clamp-2 text-[14.5px]">{acts[0].alert.text}</p>
              <ul className="hairlines mt-2">{acts.map((x) => <ActionRow key={x.id} a={x} />)}</ul>
            </section>
          ))}
        </TabsContent>

        <TabsContent value="open" className="space-y-5">
          {open.isLoading && <Skeleton className="h-40" />}
          <ErrorNote error={open.error} />
          {open.data && openReady?.length === 0 && (
            <EmptyState icon={<ShieldCheck />} title="No open alerts">
              {awaitingDecision.size > 0
                ? canDecide
                  ? "Every open alert is still waiting on a decision - see \"Needs a decision\"."
                  : "Every open alert is still waiting on a decision from an Admin, SRE or Developer. Check back shortly."
                : "Every service is below its warning threshold. The agent re-checks each one every minute."}
            </EmptyState>
          )}
          {openShown?.map((al) => <AlertCard key={al.id} alertId={al.id} defaultOpen={al.id === openId} />)}
        </TabsContent>

        <TabsContent value="history" className="space-y-5">
          {history.isLoading && <Skeleton className="h-40" />}
          {history.data && history.data.length === 0 && <EmptyState title="No resolved alerts yet">Resolved and dismissed alerts, with the outcomes you logged, are kept here.</EmptyState>}
          {history.data?.map((al) => <AlertCard key={al.id} alertId={al.id} defaultOpen={al.id === openId} />)}
        </TabsContent>
      </Tabs>
    </div>
  );
}
