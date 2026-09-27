import { useQuery } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { api } from "@/api/endpoints";
import { Chip, ErrorNote, Skeleton } from "@/components/shared/ui";
import { num } from "@/utils/format";

/** Model/provider settings: read-only here because credentials live in the deployment's secret store. */
export function ProviderPanel() {
  const q = useQuery({ queryKey: ["provider"], queryFn: api.policy.provider, refetchInterval: 30_000 });
  const sys = useQuery({ queryKey: ["system"], queryFn: api.system, refetchInterval: 30_000, retry: false });
  if (q.isLoading) return <Skeleton className="h-64" />;
  if (q.error) return <ErrorNote error={q.error} />;
  const p = q.data;
  const usage = p.usage ?? {};
  return (
    <div className="space-y-5">
      <section className="rounded-panel border border-line bg-surface p-4">
        <div className="flex flex-wrap items-center gap-2"><h3 className="text-base">AI provider</h3><Chip tone="teal">{p.provider === "mock" ? "Offline (no cloud calls)" : p.provider}</Chip>{p.endpoint_host && <span className="text-sm text-muted">{p.endpoint_host}</span>}{p.api_key_configured && <Chip tone="go">Key configured</Chip>}</div>
        <p className="mt-1 text-sm text-muted">Provider, endpoint and credentials are set in your deployment configuration so secrets never pass through the browser. LogPilot talks to any OpenAI-compatible endpoint.</p>
        <dl className="hairlines mt-3">
          {Object.entries(p.models as Record<string, { model: string; used_for: string }>).map(([role, m]) => (
            <div key={role} className="grid gap-x-4 py-2 text-sm sm:grid-cols-[9rem_1fr_2fr]"><dt className="font-medium capitalize">{role} model</dt><dd className="tabular">{m.model}</dd><dd className="text-muted">{m.used_for}</dd></div>
          ))}
        </dl>
      </section>
      <section className="rounded-panel border border-line bg-surface p-4">
        <h3 className="flex items-center gap-2 text-base"><ShieldCheck className="size-4 text-go" aria-hidden /> Guardrails on every AI call</h3>
        <ul className="mt-2 space-y-1.5 text-sm">
          <li><strong>PII redaction.</strong> {p.guardrails.pii_redaction}. {Object.keys(usage.redactions_applied ?? {}).length ? `Applied so far: ${Object.entries(usage.redactions_applied).map(([k, v]) => `${num(v as number)} ${k.toLowerCase().replace("_", " ")}`).join(", ")}.` : ""}</li>
          <li><strong>Egress allowlist.</strong> {p.guardrails.egress}. {p.egress_allowlist.length ? `Allowed hosts: ${p.egress_allowlist.join(", ")}.` : "No external host is configured, so nothing leaves the network."}</li>
        </ul>
      </section>
      <section className="rounded-panel border border-line bg-surface p-4">
        <h3 className="text-base">Usage</h3>
        {usage.calls ? (
          <dl className="mt-2 grid gap-3 text-sm sm:grid-cols-3">
            {(["fast", "deep", "embedding"] as const).map((r) => (
              <div key={r}><dt className="font-medium capitalize">{r} model</dt><dd className="tabular text-muted">{num(usage.calls?.[r] ?? 0)} calls, about {num(((usage.approx_tokens_in?.[r] ?? 0) + (usage.approx_tokens_out?.[r] ?? 0)))} tokens{usage.latency_ms?.[r] ? `, p95 ${Math.round(usage.latency_ms[r].p95)} ms` : ""}</dd></div>
            ))}
          </dl>
        ) : <p className="text-sm text-muted">No calls yet.</p>}
      </section>
      {sys.data && (
        <section className="rounded-panel border border-line bg-surface p-4">
          <div className="flex items-center gap-2"><h3 className="text-base">System health</h3><Chip tone={sys.data.status === "ok" ? "go" : "amber"}>{sys.data.status === "ok" ? "All components up" : "Degraded"}</Chip></div>
          <ul className="mt-2 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2 lg:grid-cols-3">{Object.entries(sys.data.components).map(([k, v]) => <li key={k} className="flex items-center gap-2"><span className={`size-2 rounded-full ${v.status === "ok" ? "bg-go" : "bg-signal"}`} aria-hidden />{k}<span className="text-muted">{v.status}</span></li>)}</ul>
        </section>
      )}
    </div>
  );
}
