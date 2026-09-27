import { useCallback, useEffect, useRef } from "react";
import { chatSync, fromServer } from "@/api/chatSync";
import { api } from "@/api/endpoints";
import { NEW_THREAD_KEY, useChatStore } from "@/store/chat";
import { useSession } from "@/store/session";
import type { ChatMsg } from "@/types";

const uid = () => Math.random().toString(36).slice(2, 10);
const EMPTY: ChatMsg[] = []; // a stable reference: `s.byThread[key] ?? []` would allocate a new array every
// selector call and defeat Zustand's equality check, causing an infinite render loop for any thread with no
// messages yet.

/**
 * Conversation state for the chat surface. Backed by a global store (see `store/chat.ts`), not local state:
 * the same panel renders on the home screen and the full Chat page, and either one navigating away must not
 * lose what's on screen or stop watching an answer that's still being worked on. Sending is a fast REST ack
 * (the server persists the question and an empty "thinking" placeholder immediately); the actual answer is
 * produced by a background task server-side and picked up here by polling the thread, which is what makes
 * this resume correctly after a refresh, a dropped connection, or the panel remounting mid-answer.
 */
export function useChat(projectId: string | null) {
  const threadId = useSession((s) => s.threadId);
  const setThread = useSession((s) => s.setThread);
  const key = threadId ?? NEW_THREAD_KEY;
  const messages = useChatStore((s) => s.byThread[key] ?? EMPTY);
  const busy = useChatStore((s) => s.busyThread !== null && s.busyThread === key);
  const loaded = useRef<string | null>(null);

  useEffect(() => {
    if (!projectId || !threadId || loaded.current === threadId) return;
    loaded.current = threadId;
    chatSync.loadThread(projectId, threadId).catch(() => setThread(null));
  }, [projectId, threadId, setThread]);

  useEffect(() => {
    if (!threadId && loaded.current) loaded.current = null;
  }, [threadId]);

  const send = useCallback(
    async (text: string) => {
      const t = text.trim();
      if (!projectId || !t || busy) return;
      const draftKey = threadId ?? NEW_THREAD_KEY;
      const userMsg: ChatMsg = { id: `u-${uid()}`, role: "user", content: t };
      const agentMsg: ChatMsg = { id: `a-${uid()}`, role: "agent", content: "", pending: true, status: "thinking", steps: [], tools: [], cards: [] };
      useChatStore.getState().setMessages(draftKey, [...(useChatStore.getState().byThread[draftKey] ?? []), userMsg, agentMsg]);
      useChatStore.getState().setBusy(draftKey);
      try {
        const r = await api.chat.send(projectId, t, threadId);
        if (draftKey !== r.thread_id) {
          const drafted = useChatStore.getState().byThread[draftKey] ?? [];
          useChatStore.getState().setMessages(r.thread_id, drafted);
          useChatStore.getState().clearThread(draftKey);
          loaded.current = r.thread_id;
          setThread(r.thread_id);
        }
        useChatStore.getState().patchMessage(r.thread_id, agentMsg.id, (m) => ({ ...m, id: r.message_id }));
        chatSync.watch(projectId, r.thread_id);
      } catch (e: any) {
        useChatStore.getState().patchMessage(draftKey, agentMsg.id, (m) => ({ ...m, pending: false, status: "error", error: true, content: e.message ?? "Something went wrong." }));
        useChatStore.getState().setBusy(null);
      }
    },
    [projectId, threadId, busy, setThread],
  );

  const rate = useCallback(
    async (id: string, r: 1 | -1) => {
      if (!projectId) return;
      await api.chat.rate(projectId, id, r);
      useChatStore.getState().patchMessage(key, id, (m) => ({ ...m, rating: r }));
    },
    [projectId, key],
  );

  const reset = useCallback(() => {
    if (threadId) useChatStore.getState().clearThread(threadId);
    setThread(null);
    loaded.current = null;
  }, [threadId, setThread]);

  return { messages, busy, error: null as string | null, send, rate, reset };
}

export { fromServer };
