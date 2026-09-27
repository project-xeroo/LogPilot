import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { User } from "@/types";

/** Client session state: who is signed in, which project is active, guided/standard mode, open threads. */
interface SessionState {
  token: string | null;
  user: User | null;
  projectId: string | null;
  guided: boolean;
  theme: "light" | "dark";
  threadId: string | null; // the open conversation
  pendingPrompt: string | null; // "ask the agent about this" hand-off from any chart/table/alert
  dismissedTerms: string[]; // definitions the person has already read (plain-language mode)
  glossaryOpen: boolean;
  setAuth: (token: string, user: User) => void;
  setUser: (user: User) => void;
  logout: () => void;
  setProject: (id: string) => void;
  setGuided: (v: boolean) => void;
  setTheme: (t: "light" | "dark") => void;
  setThread: (id: string | null) => void;
  ask: (prompt: string) => void;
  clearPrompt: () => void;
  dismissTerm: (t: string) => void;
  setGlossary: (open: boolean) => void;
}

const prefersDark = typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches;

export const useSession = create<SessionState>()(
  persist(
    (set, get) => ({
      token: null,
      user: null,
      projectId: null,
      guided: false,
      theme: prefersDark ? "dark" : "light",
      threadId: null,
      pendingPrompt: null,
      dismissedTerms: [],
      glossaryOpen: false,
      setAuth: (token, user) =>
        set({ token, user, guided: user.guided_mode, projectId: get().projectId && user.projects.some((p) => p.id === get().projectId) ? get().projectId : user.projects[0]?.id ?? null, threadId: null }),
      setUser: (user) => set({ user, guided: user.guided_mode }),
      logout: () => set({ token: null, user: null, threadId: null, pendingPrompt: null }),
      setProject: (id) => set({ projectId: id, threadId: null }),
      setGuided: (v) => set({ guided: v }),
      setTheme: (theme) => set({ theme }),
      setThread: (id) => set({ threadId: id }),
      ask: (prompt) => set({ pendingPrompt: prompt }),
      clearPrompt: () => set({ pendingPrompt: null }),
      dismissTerm: (t) => set({ dismissedTerms: Array.from(new Set([...get().dismissedTerms, t])) }),
      setGlossary: (open) => set({ glossaryOpen: open }),
    }),
    {
      name: "logpilot.session",
      // threadId must survive a refresh too: it's the only handle a reloaded page has on an agent reply that
      // may still be "thinking" server-side - without it, useChat has no thread to resume watching and a
      // refresh silently drops back to a blank conversation even though the real answer is still coming.
      partialize: (s) => ({ token: s.token, user: s.user, projectId: s.projectId, guided: s.guided, theme: s.theme, dismissedTerms: s.dismissedTerms, threadId: s.threadId }),
    },
  ),
);

export const can = (user: User | null, perm: string) => !!user?.permissions.includes(perm);
