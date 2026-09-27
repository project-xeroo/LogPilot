import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { UserPlus } from "lucide-react";
import { useState } from "react";
import { api } from "@/api/endpoints";
import { Dialog, DialogClose, DialogContent, Switch } from "@/components/shared/overlays";
import { Button, Chip, ErrorNote, Input, Label, PageHeader, Select, Skeleton } from "@/components/shared/ui";
import { can, useSession } from "@/store/session";
import type { Role } from "@/types";
import { fmtDateTime, timeAgo, titleCase } from "@/utils/format";

const ROLES: { v: Role; d: string }[] = [
  { v: "admin", d: "Full system access: every tool, users, provider settings and autonomy policy." },
  { v: "sre", d: "Every project. Everything a Developer can do, plus alert configuration and forecasting autonomy tiers." },
  { v: "developer", d: "Assigned projects: upload, search, chat, RCA, alerts, reports and approving propose-only actions." },
  { v: "junior_engineer", d: "Like a Developer, but starts in guided mode and can't approve actions until promoted." },
  { v: "viewer", d: "Read-only: health, search results, reports and alerts. No uploads, chat or approvals." },
];

function NewUser({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const me = useSession((s) => s.user)!;
  const [f, setF] = useState({ name: "", email: "", role: "developer", password: "" });
  const create = useMutation({ mutationFn: () => api.users.create({ ...f, project_ids: me.projects.map((p) => p.id) }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["users"] }); onOpenChange(false); setF({ name: "", email: "", role: "developer", password: "" }); } });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Add a person" description="They get access to all current projects. Junior Engineers start in guided mode.">
        <div className="grid gap-3">
          <div><Label htmlFor="nu-name">Name</Label><Input id="nu-name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
          <div><Label htmlFor="nu-email">Email</Label><Input id="nu-email" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></div>
          <div><Label htmlFor="nu-role">Role</Label><Select id="nu-role" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{ROLES.map((r) => <option key={r.v} value={r.v}>{titleCase(r.v)}</option>)}</Select><p className="mt-1 text-sm text-muted">{ROLES.find((r) => r.v === f.role)?.d}</p></div>
          <div><Label htmlFor="nu-pw">Temporary password</Label><Input id="nu-pw" type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /><p className="mt-1 text-xs text-muted">At least 10 characters.</p></div>
        </div>
        <ErrorNote error={create.error} />
        <div className="flex justify-end gap-2"><DialogClose asChild><Button>Cancel</Button></DialogClose><Button variant="primary" loading={create.isPending} disabled={!f.name || !f.email || f.password.length < 10} onClick={() => create.mutate()}>Add person</Button></div>
      </DialogContent>
    </Dialog>
  );
}

/** Settings - Roles & Onboarding: assign roles, and promote a Junior Engineer out of guided mode. */
export default function SettingsRolesPage() {
  const me = useSession((s) => s.user)!;
  const qc = useQueryClient();
  const isAdmin = can(me, "users.manage");
  const [adding, setAdding] = useState(false);
  const q = useQuery({ queryKey: ["users"], queryFn: api.users.list });
  const inval = () => qc.invalidateQueries({ queryKey: ["users"] });
  const promote = useMutation({ mutationFn: (id: string) => api.users.promote(id), onSuccess: inval });
  const update = useMutation({ mutationFn: (v: { id: string; b: Parameters<typeof api.users.update>[1] }) => api.users.update(v.id, v.b), onSuccess: inval });
  const err = promote.error || update.error;

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Roles and onboarding" actions={isAdmin && <Button variant="primary" onClick={() => setAdding(true)}><UserPlus /> Add a person</Button>}>
        Roles decide what each person can do and how much of the agent's autonomy they can configure or approve. New engineers start in guided mode; promote them when they're ready to make production calls.
      </PageHeader>
      <ErrorNote error={err} className="mb-3" />
      <section className="mb-5 rounded-panel border border-line bg-surface p-4">
        <h2 className="mb-2 text-base">What each role can do</h2>
        <dl className="grid gap-x-8 gap-y-2 text-sm md:grid-cols-2">{ROLES.map((r) => <div key={r.v}><dt className="font-medium">{titleCase(r.v)}</dt><dd className="text-muted">{r.d}</dd></div>)}</dl>
      </section>
      {q.isLoading && <Skeleton className="h-48" />}
      <ErrorNote error={q.error} />
      <ul className="hairlines rounded-panel border border-line bg-surface px-4">
        {q.data?.map((u) => (
          <li key={u.id} className="grid items-center gap-x-4 gap-y-2 py-3 sm:grid-cols-[minmax(11rem,1.4fr)_9rem_8rem_auto]">
            <div className="min-w-0"><div className="flex items-center gap-2 font-medium">{u.name}{u.id === me.id && <Chip tone="teal">You</Chip>}{!u.is_active && <Chip tone="signal">Deactivated</Chip>}</div><div className="truncate text-sm text-muted">{u.email}</div><div className="text-xs text-muted">{u.last_login_at ? `Last sign-in ${timeAgo(u.last_login_at)}` : "Never signed in"}{u.promoted_at ? `. Promoted ${fmtDateTime(u.promoted_at)}` : ""}</div></div>
            <div>
              {isAdmin && u.id !== me.id ? (
                <><Label htmlFor={`r-${u.id}`} className="sr-only">Role for {u.name}</Label><Select id={`r-${u.id}`} value={u.role} onChange={(e) => update.mutate({ id: u.id, b: { role: e.target.value } })}>{ROLES.map((r) => <option key={r.v} value={r.v}>{titleCase(r.v)}</option>)}</Select></>
              ) : <Chip>{titleCase(u.role)}</Chip>}
            </div>
            <label className="flex items-center gap-2 text-sm"><Switch checked={u.guided_mode} disabled={!isAdmin} onCheckedChange={(v) => update.mutate({ id: u.id, b: { guided_mode: v } })} aria-label={`Guided mode for ${u.name}`} />Guided mode</label>
            <div className="flex gap-2 justify-self-end">
              {u.role === "junior_engineer" && <Button size="sm" variant="primary" loading={promote.isPending && promote.variables === u.id} onClick={() => promote.mutate(u.id)}>Promote to Developer</Button>}
              {isAdmin && u.id !== me.id && <Button size="sm" variant="ghost" onClick={() => update.mutate({ id: u.id, b: { is_active: !u.is_active } })}>{u.is_active ? "Deactivate" : "Reactivate"}</Button>}
            </div>
          </li>
        ))}
      </ul>
      <NewUser open={adding} onOpenChange={setAdding} />
    </div>
  );
}
