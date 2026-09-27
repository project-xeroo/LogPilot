import { create } from "zustand";
import type { LiveEvent } from "@/types";

/** Real-time state pushed by the agent: connection status and alerts that interrupt the console. */
export interface Interrupt {
  key: string;
  kind: "alert" | "pre_mortem" | "escalated";
  title: string;
  body: string;
  service?: string;
  alertId?: string;
  reportId?: string;
  level?: string;
}

interface LiveState {
  status: "connecting" | "open" | "closed";
  interrupts: Interrupt[];
  lastEvent: LiveEvent | null;
  setStatus: (s: LiveState["status"]) => void;
  push: (i: Interrupt) => void;
  dismiss: (key: string) => void;
  setLast: (e: LiveEvent) => void;
}

export const useLive = create<LiveState>((set) => ({
  status: "connecting",
  interrupts: [],
  lastEvent: null,
  setStatus: (status) => set({ status }),
  push: (i) => set((s) => ({ interrupts: [i, ...s.interrupts.filter((x) => x.key !== i.key)].slice(0, 5) })),
  dismiss: (key) => set((s) => ({ interrupts: s.interrupts.filter((x) => x.key !== key) })),
  setLast: (lastEvent) => set({ lastEvent }),
}));
