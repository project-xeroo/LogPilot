import { create } from "zustand";
import type { ChatMsg } from "@/types";

/**
 * Conversation messages, keyed by thread id (a not-yet-created conversation uses the literal key "__new__"
 * until the server assigns a real id). Deliberately a plain in-memory store, not per-component state: the
 * same ChatPanel is rendered on both the home screen and the full Chat page, and navigating between them -
 * or any other route - must not reset what's on screen or stop watching an answer that is still being
 * worked on server-side.
 */
interface ChatState {
  byThread: Record<string, ChatMsg[]>;
  busyThread: string | null;
  setMessages: (threadId: string, msgs: ChatMsg[]) => void;
  patchMessage: (threadId: string, id: string, fn: (m: ChatMsg) => ChatMsg) => void;
  setBusy: (threadId: string | null) => void;
  clearThread: (threadId: string) => void;
}

export const NEW_THREAD_KEY = "__new__";

export const useChatStore = create<ChatState>((set) => ({
  byThread: {},
  busyThread: null,
  setMessages: (threadId, msgs) => set((s) => ({ byThread: { ...s.byThread, [threadId]: msgs } })),
  patchMessage: (threadId, id, fn) =>
    set((s) => ({ byThread: { ...s.byThread, [threadId]: (s.byThread[threadId] ?? []).map((m) => (m.id === id ? fn(m) : m)) } })),
  setBusy: (threadId) => set({ busyThread: threadId }),
  clearThread: (threadId) =>
    set((s) => {
      const b = { ...s.byThread };
      delete b[threadId];
      return { byThread: b };
    }),
}));
