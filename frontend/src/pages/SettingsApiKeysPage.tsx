import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, Copy, KeyRound, Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "@/api/endpoints";
import { CodeSamples } from "@/components/apikeys/CodeSamples";
import { Dialog, DialogClose, DialogContent } from "@/components/shared/overlays";
import { Button, Chip, ErrorNote, Input, Label, PageHeader, Skeleton } from "@/components/shared/ui";
import { useProjectId } from "@/hooks/useProject";
import { can, useSession } from "@/store/session";
import type { CreatedApiKey } from "@/types";
import { cn } from "@/utils/cn";
import { fmtDateTime, timeAgo } from "@/utils/format";

const SCOPE_LABEL: Record<string, string> = {
  "logs.upload": "Ingest logs", "logs.view_sessions": "View upload sessions", "logs.search": "Search logs",
  "health.view": "View service health", "risk.view": "View failure risk",
};

function CopyableKey({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard permission denied - the value is still selectable/visible */ }
  };
  return (
    <div className="flex items-center gap-2 rounded-control border border-line bg-raised px-3 py-2">
      <code className="min-w-0 flex-1 select-all break-all font-mono text-[13px]">{value}</code>
      <Button type="button" size="sm" onClick={copy}>{copied ? <Check className="size-3.5 text-go" /> : <Copy className="size-3.5" />}{copied ? "Copied" : "Copy"}</Button>
    </div>
  );
}

function NewKey({ projectId, open, onOpenChange }: { projectId: string; open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const scopesQ = useQuery({ queryKey: ["api-key-scopes", projectId], queryFn: () => api.apiKeys.scopes(projectId), enabled: open });
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["logs.upload"]);
  const [created, setCreated] = useState<CreatedApiKey | null>(null);
  const create = useMutation({
    mutationFn: () => api.apiKeys.create(projectId, { name: name.trim(), scopes }),
    onSuccess: (k) => { setCreated(k); qc.invalidateQueries({ queryKey: ["api-keys", projectId] }); },
  });
  const close = (o: boolean) => { onOpenChange(o); if (!o) { setName(""); setScopes(["logs.upload"]); setCreated(null); create.reset(); } };
  const toggle = (s: string) => setScopes((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent title={created ? "Key created" : "Create an API key"} description={created ? "Copy it now - you won't be able to see it again." : "A narrow machine credential for an external service (e.g. your own webapp) to call this project's API without a user login."}>
        {created ? (
          <div className="space-y-4">
            <CopyableKey value={created.key} />
            <p className="text-sm text-muted">This is shown once. If you lose it, revoke this key and create a new one - the key itself can never be recovered, only its hash is stored.</p>
            <div>
              <p className="mb-2 text-sm font-medium">Send your first log line</p>
              <CodeSamples projectId={projectId} apiKey={created.key} />
            </div>
            <div className="flex justify-end"><Button variant="primary" onClick={() => close(false)}>Done</Button></div>
          </div>
        ) : (
          <>
            <div className="grid gap-3">
              <div><Label htmlFor="key-name">Name</Label><Input id="key-name" placeholder="e.g. Storefront demo app" value={name} onChange={(e) => setName(e.target.value)} /></div>
              <div>
                <Label>Scopes</Label>
                <div className="mt-1 space-y-1.5">
                  {(scopesQ.data?.scopes ?? Object.keys(SCOPE_LABEL)).map((s) => (
                    <label key={s} className="flex cursor-pointer items-center gap-2 text-sm">
                      <input type="checkbox" className="size-4 accent-teal" checked={scopes.includes(s)} onChange={() => toggle(s)} />
                      {SCOPE_LABEL[s] ?? s}
                    </label>
                  ))}
                </div>
                <p className="mt-1.5 text-xs text-muted">A key can only ever do what's checked here - nothing more, regardless of who created it.</p>
              </div>
            </div>
            <ErrorNote error={create.error} />
            <div className="flex justify-end gap-2"><DialogClose asChild><Button>Cancel</Button></DialogClose><Button variant="primary" loading={create.isPending} disabled={!name.trim() || scopes.length === 0} onClick={() => create.mutate()}>Create key</Button></div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** Settings - API Keys: project-scoped machine credentials for external services (a team's own webapp
 * pushing its logs in, or reading back its own health/risk status) that should never need a user login. */
export default function SettingsApiKeysPage() {
  const me = useSession((s) => s.user)!;
  const projectId = useProjectId();
  const qc = useQueryClient();
  const isManager = can(me, "api_keys.manage");
  const [adding, setAdding] = useState(false);
  const [showSamples, setShowSamples] = useState(false);
  const q = useQuery({ queryKey: ["api-keys", projectId], queryFn: () => api.apiKeys.list(projectId!), enabled: !!projectId });
  const revoke = useMutation({ mutationFn: (keyId: string) => api.apiKeys.revoke(projectId!, keyId), onSuccess: () => qc.invalidateQueries({ queryKey: ["api-keys", projectId] }) });

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="API keys" actions={isManager && <Button variant="primary" onClick={() => setAdding(true)}><KeyRound /> Create key</Button>}>
        Let an external service - your own webapp, a script, a CI job - call this project's API directly with a narrow, revocable credential instead of a user login. Each key is scoped to exactly the permissions it was created with.
      </PageHeader>
      {projectId && (
        <section className="mb-5 rounded-panel border border-line bg-surface p-4">
          <button type="button" onClick={() => setShowSamples((v) => !v)} aria-expanded={showSamples} className="flex w-full items-center gap-2 text-left">
            <ChevronDown className={cn("size-4 shrink-0 text-muted transition-transform", showSamples && "rotate-180")} aria-hidden />
            <span>
              <span className="block text-base">Quick start: send a log line</span>
              <span className="block text-sm text-muted">Copy-paste examples in the language you're already using - swap in a real key from below.</span>
            </span>
          </button>
          {showSamples && (
            <div className="mt-4 border-t border-line pt-4">
              <CodeSamples projectId={projectId} apiKey="YOUR_API_KEY" />
            </div>
          )}
        </section>
      )}
      {q.isLoading && <Skeleton className="h-32" />}
      <ErrorNote error={q.error} />
      {q.data?.length === 0 && <p className="rounded-panel border border-line bg-surface p-6 text-center text-sm text-muted">No API keys yet.</p>}
      {!!q.data?.length && (
        <ul className="hairlines rounded-panel border border-line bg-surface px-4">
          {q.data.map((k) => (
            <li key={k.id} className="grid items-center gap-x-4 gap-y-2 py-3 sm:grid-cols-[minmax(11rem,1.4fr)_1fr_auto]">
              <div className="min-w-0">
                <div className="flex items-center gap-2 font-medium">{k.name}{k.revoked_at && <Chip tone="signal">Revoked</Chip>}</div>
                <div className="truncate font-mono text-xs text-muted">{k.key_prefix}...</div>
                <div className="text-xs text-muted">Created {fmtDateTime(k.created_at)} - {k.last_used_at ? `last used ${timeAgo(k.last_used_at)}` : "never used"}</div>
              </div>
              <div className="flex flex-wrap gap-1">{k.scopes.map((s) => <Chip key={s}>{SCOPE_LABEL[s] ?? s}</Chip>)}</div>
              <div className="justify-self-end">
                {isManager && !k.revoked_at && (
                  <Button size="sm" variant="ghost" loading={revoke.isPending && revoke.variables === k.id} onClick={() => revoke.mutate(k.id)}>
                    <Trash2 className="size-3.5" /> Revoke
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {projectId && <NewKey projectId={projectId} open={adding} onOpenChange={setAdding} />}
    </div>
  );
}
