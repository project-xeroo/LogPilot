import { useQuery } from "@tanstack/react-query";
import { Compass, FileText, Gauge, KeyRound, LogOut, MessagesSquare, Moon, Rocket, Search, ShieldAlert, SlidersHorizontal, Sun, Upload, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "@/api/endpoints";
import { AlertInterrupts } from "@/components/alerts/AlertInterrupt";
import { GlossaryButton, GlossaryPanel } from "@/components/glossary/GlossaryPanel";
import { Switch, Tip, TooltipProvider } from "@/components/shared/overlays";
import { Button, Chip } from "@/components/shared/ui";
import { UploadDialog } from "@/components/shared/UploadDialog";
import { useLiveEvents } from "@/hooks/useLiveEvents";
import { useProject } from "@/hooks/useProject";
import { useLive } from "@/store/live";
import { can, useSession } from "@/store/session";
import { cn } from "@/utils/cn";
import { titleCase } from "@/utils/format";

const NAV = [
  { to: "/", label: "Home", icon: Compass, perm: "feed.view", end: true },
  { to: "/chat", label: "Chat", icon: MessagesSquare, perm: "chat.use" },
  { to: "/risk", label: "Failure risk", icon: Gauge, perm: "risk.view" },
  { to: "/alerts", label: "Alerts and approvals", icon: ShieldAlert, perm: "alerts.view", badge: true },
  { to: "/search", label: "Search", icon: Search, perm: "logs.search" },
  { to: "/reports", label: "Reports", icon: FileText, perm: "reports.view" },
  { to: "/deployments", label: "Deployments", icon: Rocket, perm: "deployments.view" },
  { to: "/settings/policy", label: "Autonomy and policy", icon: SlidersHorizontal, perm: "policy.view", group: "Settings" },
  { to: "/settings/roles", label: "Roles and onboarding", icon: Users, perm: "users.promote", group: "Settings" },
  { to: "/settings/api-keys", label: "API keys", icon: KeyRound, perm: "api_keys.manage", group: "Settings" },
];

export function AppShell() {
  useLiveEvents();
  const nav = useNavigate();
  const { user, projectId, guided, theme } = useSession();
  const { setProject, setGuided, setTheme, logout, setUser } = useSession.getState();
  const project = useProject();
  const status = useLive((s) => s.status);
  const [upload, setUpload] = useState(false);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const approvals = useQuery({
    queryKey: ["approvals", projectId],
    queryFn: () => api.alerts.approvals(projectId!),
    enabled: !!projectId && can(user, "alerts.view"),
    refetchInterval: 30_000,
  });
  const waiting = (approvals.data?.actions.length ?? 0) + (approvals.data?.pending_alerts.length ?? 0);

  const toggleGuided = async (v: boolean) => {
    setGuided(v);
    try {
      await api.auth.setGuided(v);
      if (user) setUser({ ...user, guided_mode: v });
    } catch { /* keep local preference if the save fails */ }
  };

  if (!user) return null;
  const items = NAV.filter((n) => can(user, n.perm));

  return (
    <TooltipProvider>
      <a href="#main" className="skip-link">Skip to content</a>
      <AlertInterrupts />
      <div className="flex min-h-screen flex-col md:flex-row">
        {/* navigation rail: full labels from "lg" up (most laptops), icons on tablet, bottom bar on phones.
            The active item gets three redundant cues - not just a colour tint - so "where am I" never
            depends on subtle contrast: a solid left accent bar, a filled icon, and bold teal text. */}
        <nav aria-label="Main" className="fixed inset-x-0 bottom-0 z-40 flex justify-around border-t border-line bg-surface px-1 py-1 md:sticky md:inset-auto md:top-0 md:h-screen md:w-14 md:shrink-0 md:flex-col md:justify-start md:gap-0.5 md:border-r md:border-t-0 md:px-2 md:py-3 lg:w-60 lg:px-3">
          <div className="hidden items-center gap-2.5 px-1.5 pb-3 md:flex">
            <img src="/favicon.svg" alt="" className="size-7 shrink-0" />
            <span className="hidden text-lg font-semibold tracking-tight lg:inline">LogPilot</span>
          </div>
          {user.projects.length > 1 && (
            <label className="hidden px-1 pb-2 lg:block">
              <span className="sr-only">Active project</span>
              <select value={projectId ?? ""} onChange={(e) => setProject(e.target.value)} className="field h-8 text-sm">
                {user.projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </label>
          )}
          {items.map((n, i) => {
            const startsGroup = n.group && items[i - 1]?.group !== n.group;
            return (
              <div key={n.to} className="contents md:block">
                {startsGroup && <div className="mx-2 mb-1 mt-3 hidden border-t border-line pt-2 text-xs text-muted lg:block">{n.group}</div>}
                <Tip label={n.label}>
                  <NavLink
                    to={n.to}
                    end={n.end}
                    className={({ isActive }) => cn(
                      "relative flex flex-1 flex-col items-center gap-0.5 rounded-control px-2 py-1.5 text-[11px] text-muted hover:bg-ink/[.06] hover:text-ink md:flex-none md:flex-row md:gap-3 md:px-2.5 md:py-2 md:text-sm lg:px-3",
                      "before:absolute before:rounded-full before:bg-teal before:transition-opacity before:content-['']",
                      // bottom bar (phones): accent along the top edge; side rail (tablet+): accent down the left edge
                      "before:inset-x-2 before:top-0 before:h-[3px] md:before:inset-x-0 md:before:inset-y-1.5 md:before:left-0 md:before:top-auto md:before:h-auto md:before:w-[3px]",
                      isActive ? "bg-teal-soft font-semibold text-teal-ink before:opacity-100 hover:bg-teal-soft hover:text-teal-ink" : "before:opacity-0",
                    )}
                  >
                    <n.icon className="size-[18px] shrink-0" strokeWidth={2.25} aria-hidden />
                    <span className="max-w-[4.5rem] truncate md:sr-only lg:not-sr-only lg:max-w-none">{n.label}</span>
                    {n.badge && waiting > 0 && (
                      <span className="absolute right-1 top-0.5 grid min-w-4 place-items-center rounded-full bg-signal px-1 text-[10px] font-semibold leading-4 text-white lg:static lg:ml-auto" aria-label={`${waiting} waiting for a decision`}>{waiting}</span>
                    )}
                  </NavLink>
                </Tip>
              </div>
            );
          })}
          <div className="mt-auto hidden space-y-1 border-t border-line pt-3 lg:block">
            <div className="px-2 text-sm"><div className="truncate font-medium">{user.name}</div><Chip className="mt-1">{titleCase(user.role)}</Chip></div>
            <button type="button" onClick={() => { logout(); nav("/login"); }} className="flex w-full items-center gap-2 rounded-control px-2 py-1.5 text-sm text-muted hover:bg-ink/[.06] hover:text-ink"><LogOut className="size-4" aria-hidden /> Sign out</button>
          </div>
        </nav>

        <div className="flex min-w-0 flex-1 flex-col pb-16 md:pb-0">
          <header className="sticky top-0 z-30 flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-paper/90 px-4 py-2 backdrop-blur md:px-6">
            <div className="mr-auto flex items-baseline gap-3">
              <span className="font-semibold">{project?.name ?? "No project"}</span>
              <span className="hidden text-sm text-muted sm:inline">{project?.environment}</span>
            </div>
            <span className="inline-flex items-center gap-1.5 text-sm text-muted" role="status">
              <span className={cn("size-2 rounded-full", status === "open" ? "bg-go" : status === "connecting" ? "bg-amber" : "bg-signal")} aria-hidden />
              {status === "open" ? "Live" : status === "connecting" ? "Connecting" : "Reconnecting"}
            </span>
            <Tip label="Plain-language mode explains terms and shows the agent's reasoning first. On by default for Junior Engineers.">
              <label className="flex cursor-pointer items-center gap-2 text-sm">
                <Switch checked={guided} onCheckedChange={toggleGuided} aria-label="Guided mode" />
                Guided mode
              </label>
            </Tip>
            <GlossaryButton />
            <button type="button" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} className="rounded-control p-2 text-muted hover:bg-ink/[.06] hover:text-ink" aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}>
              {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </button>
            {can(user, "logs.upload") && <Button variant="primary" size="sm" onClick={() => setUpload(true)}><Upload /> Upload logs</Button>}
          </header>
          <main id="main" className="min-w-0 flex-1 px-4 py-5 md:px-6">
            <Outlet />
          </main>
        </div>
      </div>
      <UploadDialog open={upload} onOpenChange={setUpload} />
      <GlossaryPanel />
    </TooltipProvider>
  );
}
