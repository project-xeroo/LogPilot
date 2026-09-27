import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { api } from "@/api/endpoints";
import { Button, ErrorNote, Input, Label } from "@/components/shared/ui";
import { useSession } from "@/store/session";
import type { User } from "@/types";

const DEMO = [
  ["admin@logpilot.local", "Admin"], ["sre@logpilot.local", "SRE"], ["dev@logpilot.local", "Developer"], ["junior@logpilot.local", "Junior engineer"], ["viewer@logpilot.local", "Viewer"],
];
const SHOW_DEMO = import.meta.env.VITE_SHOW_DEMO_LOGINS === "true";
const DEMO_PW = (import.meta.env.VITE_DEMO_PASSWORD as string | undefined) ?? "";

export default function LoginPage() {
  const token = useSession((s) => s.token);
  const theme = useSession((s) => s.theme);
  const nav = useNavigate();
  const loc = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  const login = useMutation({
    mutationFn: (v: { email: string; password: string }) => api.auth.login(v.email, v.password),
    onSuccess: (r: { access_token: string; user: User }) => { useSession.getState().setAuth(r.access_token, r.user); nav((loc.state as any)?.from ?? "/", { replace: true }); },
  });
  if (token) return <Navigate to="/" replace />;

  return (
    <main className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      <section className="relative hidden overflow-hidden bg-[hsl(205_37%_12%)] p-12 text-[hsl(180_14%_94%)] lg:flex lg:flex-col lg:justify-between">
        <div className="flex items-center gap-3"><img src="/favicon.svg" alt="" className="size-9" /><span className="text-xl font-semibold tracking-tight">LogPilot</span></div>
        <div className="max-w-md">
          <p className="font-serif text-[34px] leading-[1.15]">Your services' logs, read continuously by something that never sleeps.</p>
          <p className="mt-5 font-serif text-lg leading-relaxed text-[hsl(180_14%_94%)]/70">It notices trouble building, tells you why in plain language, and proposes what to do. You decide.</p>
        </div>
        <svg viewBox="0 0 520 90" className="w-full text-[hsl(178_55%_52%)]" aria-hidden>
          <line x1="0" y1="60" x2="520" y2="60" stroke="currentColor" strokeOpacity=".25" />
          <line x1="312" y1="10" x2="312" y2="80" stroke="hsl(42 95% 58%)" strokeDasharray="3 3" /><line x1="416" y1="10" x2="416" y2="80" stroke="hsl(8 90% 64%)" strokeDasharray="3 3" />
          <path d="M0 72 C60 70 90 66 140 64 S210 60 250 52 S300 30 340 22 S400 12 440 6" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
        </svg>
      </section>
      <section className="flex items-center justify-center bg-paper px-6 py-12">
        <form onSubmit={(e) => { e.preventDefault(); login.mutate({ email, password }); }} className="w-full max-w-sm space-y-4">
          <div className="lg:hidden mb-6 flex items-center gap-2"><img src="/favicon.svg" alt="" className="size-8" /><span className="text-xl font-semibold">LogPilot</span></div>
          <div><h1>Sign in</h1><p className="mt-1 text-sm text-muted">Use your work account to supervise the agent.</p></div>
          <div><Label htmlFor="email">Email</Label><Input id="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} autoFocus /></div>
          <div><Label htmlFor="password">Password</Label><Input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></div>
          <ErrorNote error={login.error} />
          <Button type="submit" variant="primary" size="lg" className="w-full" loading={login.isPending}>Sign in</Button>
          {SHOW_DEMO && (
            <div className="border-t border-line pt-4">
              <p className="mb-2 text-sm text-muted">Demo accounts</p>
              <div className="flex flex-wrap gap-2">{DEMO.map(([e, l]) => <Button key={e} type="button" size="sm" onClick={() => login.mutate({ email: e, password: DEMO_PW })}>{l}</Button>)}</div>
            </div>
          )}
        </form>
      </section>
    </main>
  );
}
