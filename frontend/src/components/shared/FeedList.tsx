import { useInfiniteQuery } from "@tanstack/react-query";
import { Activity, BellRing, FileText, GitCompareArrows, Inbox, MessageSquareText, Radar, ScrollText, Waypoints } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/api/endpoints";
import { AskAgent } from "@/components/shared/AskAgent";
import { Markdown } from "@/components/shared/Markdown";
import { Button, EmptyState, ErrorNote, Skeleton } from "@/components/shared/ui";
import { useProjectId } from "@/hooks/useProject";
import type { FeedItem } from "@/types";
import { cn } from "@/utils/cn";
import { dayLabel, fmtTime } from "@/utils/format";

const ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  alert: BellRing, report: FileText, anomaly: Activity, answer: MessageSquareText, deployment: GitCompareArrows, ingestion: ScrollText, rca: Waypoints, action: Radar,
};
const RAIL = { info: "bg-line", warning: "bg-amber", critical: "bg-signal" } as const;

function link(i: FeedItem): { to: string; label: string } | null {
  if (i.ref_type === "alert" && i.ref_id) return { to: `/alerts?open=${i.ref_id}`, label: "Review alert" };
  if (i.ref_type === "report" && i.ref_id) return { to: `/reports?open=${i.ref_id}`, label: "Open report" };
  if (i.ref_type === "deployment_comparison") return { to: "/deployments", label: "See comparison" };
  if (i.kind === "anomaly" || i.kind === "ingestion") return { to: "/risk", label: "Open health state" };
  return null;
}

function askFor(i: FeedItem): string | null {
  if (i.kind === "alert" && i.service) return `Why is ${i.service} flagged?`;
  if (i.kind === "anomaly" && i.service) return `What caused the ${i.title.toLowerCase().replace(/ in .*/, "")} in ${i.service}?`;
  if (i.kind === "deployment" && i.service) return `Did the latest deployment of ${i.service} cause any regressions?`;
  return null;
}

function Item({ i }: { i: FeedItem }) {
  const [open, setOpen] = useState(false);
  const Icon = ICON[i.kind] ?? Inbox;
  const l = link(i);
  const q = askFor(i);
  const long = (i.body?.length ?? 0) > 220;
  return (
    <li className="relative flex gap-3 py-3.5 pl-4">
      <span className={cn("absolute bottom-3.5 left-0 top-3.5 w-[3px] rounded-full", RAIL[i.severity])} aria-hidden />
      <Icon className={cn("mt-0.5 size-[18px] shrink-0", i.severity === "critical" ? "text-signal" : i.severity === "warning" ? "text-amber-ink" : "text-muted")} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <h3 className="text-[15px] font-semibold leading-snug">{i.title}</h3>
          <time dateTime={i.created_at} className="ml-auto shrink-0 text-xs tabular text-muted">{fmtTime(i.created_at)}</time>
        </div>
        {i.body && <Markdown className={cn("mt-1 text-[14.5px] leading-[1.6]", !open && long && "line-clamp-3")}>{i.body}</Markdown>}
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          {long && <button type="button" onClick={() => setOpen(!open)} className="rounded-control px-2 py-1 text-sm text-muted hover:bg-ink/[.06]">{open ? "Show less" : "Show more"}</button>}
          {l && <Link to={l.to} className="rounded-control px-2 py-1 text-sm font-medium text-teal-ink hover:bg-teal-soft">{l.label}</Link>}
          {q && <AskAgent prompt={q} label="Ask why" />}
        </div>
      </div>
    </li>
  );
}

/** Agent Feed: a chronological stream of everything the agent has noticed, said or drafted. */
export function FeedList() {
  const projectId = useProjectId();
  const q = useInfiniteQuery({
    queryKey: ["feed", projectId],
    queryFn: ({ pageParam }) => api.feed(projectId, { before: pageParam || undefined, limit: 30 }),
    initialPageParam: "" as string,
    getNextPageParam: (last) => last.next_before ?? undefined,
    enabled: !!projectId,
    refetchInterval: 60_000,
  });
  const groups = useMemo(() => {
    const items = q.data?.pages.flatMap((p) => p.items) ?? [];
    const out: { day: string; items: FeedItem[] }[] = [];
    for (const i of items) {
      const d = dayLabel(i.created_at);
      const g = out[out.length - 1];
      if (g?.day === d) g.items.push(i); else out.push({ day: d, items: [i] });
    }
    return out;
  }, [q.data]);

  if (q.isLoading) return <div className="space-y-4">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16" />)}</div>;
  if (q.error) return <ErrorNote error={q.error} />;
  if (!groups.length) {
    return <EmptyState icon={<Inbox />} title="Nothing to report yet">Upload logs, or ask the agent a question. Alerts, anomalies, drafts and answers will collect here as they happen.</EmptyState>;
  }
  return (
    <div>
      {groups.map((g) => (
        <section key={g.day} aria-label={g.day}>
          <h2 className="sticky top-[3.4rem] z-10 -mx-1 bg-paper/95 px-1 py-1.5 text-sm font-medium text-muted backdrop-blur">{g.day}</h2>
          <ul className="hairlines">{g.items.map((i) => <Item key={i.id} i={i} />)}</ul>
        </section>
      ))}
      {q.hasNextPage && <div className="py-4 text-center"><Button variant="secondary" onClick={() => q.fetchNextPage()} loading={q.isFetchingNextPage}>Show older activity</Button></div>}
    </div>
  );
}
