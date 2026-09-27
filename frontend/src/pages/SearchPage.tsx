import { useMutation, useQuery } from "@tanstack/react-query";
import { Search as SearchIcon, SlidersHorizontal } from "lucide-react";
import { useState } from "react";
import { api } from "@/api/endpoints";
import { ResultRow } from "@/components/search/SearchResults";
import { Term } from "@/components/glossary/Term";
import { Button, EmptyState, ErrorNote, Input, Label, PageHeader, Select, Spinner } from "@/components/shared/ui";
import { useProjectId } from "@/hooks/useProject";
import { cn } from "@/utils/cn";
import { num } from "@/utils/format";

const RANGES: [string, string, number | null][] = [["any", "Any time", null], ["15m", "Last 15 minutes", 15], ["1h", "Last hour", 60], ["6h", "Last 6 hours", 360], ["24h", "Last 24 hours", 1440], ["7d", "Last 7 days", 10080]];
const SEVS = ["DEBUG", "INFO", "WARN", "ERROR", "FATAL"];

/** Search: fallback keyword / semantic search for when a person wants to look themselves rather than ask. */
export default function SearchPage() {
  const projectId = useProjectId();
  const services = useQuery({ queryKey: ["services", projectId], queryFn: () => api.services(projectId), enabled: !!projectId });
  const [mode, setMode] = useState<"keyword" | "semantic">("keyword");
  const [q, setQ] = useState("");
  const [regex, setRegex] = useState(false);
  const [exact, setExact] = useState(false);
  const [range, setRange] = useState("any");
  const [sev, setSev] = useState<string[]>([]);
  const [svc, setSvc] = useState("");
  const [env, setEnv] = useState("");
  const [ver, setVer] = useState("");
  const [trace, setTrace] = useState("");
  const [more, setMore] = useState(false);

  const run = useMutation({
    mutationFn: () => {
      const mins = RANGES.find((r) => r[0] === range)?.[2];
      return api.search(projectId, {
        query: q, mode, regex: mode === "keyword" && regex, exact: mode === "keyword" && exact, severity: sev, services: svc ? [svc] : [],
        environment: env || undefined, deployment_version: ver || undefined, trace_id: trace || undefined,
        ...(mins ? { start: new Date(Date.now() - mins * 60000).toISOString() } : {}), limit: 50, context_window: 2,
      });
    },
  });
  const r = run.data;
  const canSubmit = mode === "semantic" ? q.trim().length > 1 : q.trim().length > 0 || sev.length > 0 || !!svc || !!trace || !!ver;

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Search">Find log lines yourself. Keyword search matches text exactly (regular expressions welcome); search by meaning finds related errors even when the wording differs.</PageHeader>
      <form onSubmit={(e) => { e.preventDefault(); if (canSubmit) run.mutate(); }} className="space-y-3 rounded-panel border border-line bg-surface p-4">
        <div role="radiogroup" aria-label="Search type" className="inline-flex rounded-control bg-ink/[.06] p-1">
          {([["keyword", "Keyword"], ["semantic", "By meaning"]] as const).map(([v, l]) => (
            <button key={v} type="button" role="radio" aria-checked={mode === v} onClick={() => setMode(v)} className={cn("rounded-[5px] px-3.5 py-1.5 text-sm font-medium", mode === v ? "bg-raised shadow-sm" : "text-muted")}>{l}</button>
          ))}
        </div>
        <div className="flex gap-2">
          <div className="relative flex-1">
            <SearchIcon className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-muted" aria-hidden />
            <Label htmlFor="q" className="sr-only">Search query</Label>
            <Input id="q" value={q} onChange={(e) => setQ(e.target.value)} className="pl-8" placeholder={mode === "keyword" ? (regex ? "timed out after \\d+ms" : "connection pool exhausted") : "can't get a connection to the cache"} autoFocus />
          </div>
          <Button type="submit" variant="primary" disabled={!canSubmit} loading={run.isPending}>Search</Button>
        </div>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
          {mode === "keyword" && (
            <>
              <label className="flex items-center gap-2"><input type="checkbox" checked={regex} onChange={(e) => setRegex(e.target.checked)} className="accent-[hsl(var(--teal))]" /> Regular expression</label>
              <label className="flex items-center gap-2"><input type="checkbox" checked={exact} disabled={regex} onChange={(e) => setExact(e.target.checked)} className="accent-[hsl(var(--teal))]" /> Exact phrase</label>
            </>
          )}
          <label className="flex items-center gap-2"><span className="text-muted">Time</span><Select value={range} onChange={(e) => setRange(e.target.value)} className="w-auto">{RANGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></label>
          <label className="flex items-center gap-2"><span className="text-muted">Service</span><Select value={svc} onChange={(e) => setSvc(e.target.value)} className="w-auto"><option value="">All services</option>{services.data?.map((s) => <option key={s.id} value={s.name}>{s.name}</option>)}</Select></label>
          <button type="button" onClick={() => setMore(!more)} aria-expanded={more} className="ml-auto inline-flex items-center gap-1.5 text-muted hover:text-ink"><SlidersHorizontal className="size-4" aria-hidden /> More filters</button>
        </div>
        <fieldset className="flex flex-wrap items-center gap-2 text-sm"><legend className="sr-only">Severity</legend><span className="text-muted"><Term term="severity">Severity</Term></span>
          {SEVS.map((s) => <label key={s} className={cn("cursor-pointer rounded-full border px-2.5 py-0.5", sev.includes(s) ? "border-teal bg-teal-soft text-teal-ink" : "border-line text-muted hover:text-ink")}><input type="checkbox" className="sr-only" checked={sev.includes(s)} onChange={() => setSev(sev.includes(s) ? sev.filter((x) => x !== s) : [...sev, s])} />{s}</label>)}
        </fieldset>
        {more && (
          <div className="grid gap-3 sm:grid-cols-3">
            <div><Label htmlFor="env">Environment</Label><Input id="env" value={env} onChange={(e) => setEnv(e.target.value)} placeholder="production" /></div>
            <div><Label htmlFor="ver"><Term term="deployment version">Deployment version</Term></Label><Input id="ver" value={ver} onChange={(e) => setVer(e.target.value)} placeholder="v2.3.1" /></div>
            <div><Label htmlFor="trace"><Term term="trace ID">Trace ID</Term></Label><Input id="trace" value={trace} onChange={(e) => setTrace(e.target.value)} /></div>
          </div>
        )}
      </form>

      <div className="mt-5" aria-live="polite">
        {run.isPending && <Spinner label="Searching" />}
        <ErrorNote error={run.error} />
        {r && (
          <>
            <p className="mb-1 text-sm text-muted">{r.count === 0 ? "No matches." : `${num(r.count)} shown${r.total_capped ? " of more than 10,000" : r.total > r.count ? ` of ${num(r.total)}` : ""}, found in ${r.took_ms} ms.`}</p>
            {r.count === 0 ? (
              <EmptyState icon={<SearchIcon />} title="Nothing matched">{mode === "keyword" ? "Try fewer words, widen the time range, or switch to By meaning to catch different wording." : "Try describing the problem differently, or lower the severity filter."}</EmptyState>
            ) : (
              <ul className="hairlines rounded-panel border border-line bg-surface px-4">{r.results.map((x) => <ResultRow key={x.id} r={x} semantic={r.mode === "semantic"} />)}</ul>
            )}
          </>
        )}
        {!r && !run.isPending && !run.error && <EmptyState icon={<SearchIcon />} title="Search your logs">Not sure what to look for? Ask the agent in chat instead. It searches for you and cites what it found.</EmptyState>}
      </div>
    </div>
  );
}
