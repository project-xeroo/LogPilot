import { api } from "@/api/endpoints";
import { useChatStore } from "@/store/chat";
import type { ChatMsg } from "@/types";

const POLL_MS = 2500;

export function fromServer(m: any): ChatMsg {
  return {
    id: m.id, role: m.role, content: m.content, sources: m.sources, cards: m.cards, confidence: m.confidence, confidence_label: m.confidence_label,
    rating: m.rating, followups: m.followups, tools: (m.tool_calls ?? []).map((t: any) => ({ tool: t.tool, ms: t.ms, ok: t.ok })), created_at: m.created_at,
    status: m.status, steps: m.steps ?? [], pending: m.status === "thinking", error: m.status === "error",
  };
}

/**
 * A single, module-level watcher for in-progress agent replies - not a per-component effect - so that
 * however many places render the chat panel (home screen, full Chat page), there is exactly one poll loop
 * per thread, and it keeps running even while nothing is mounted to see it. This is what makes an answer
 * survive a tab switch, a full page refresh, or the chat panel unmounting mid-answer: on reload, whoever
 * asks to see the thread again just resumes watching the same server-side row (`chatSync.loadThread`) -
 * the actual work already happened, or is still happening, on the server regardless of the client.
 */
class ChatSync {
  private watching = new Set<string>();

  async loadThread(projectId: string, threadId: string): Promise<void> {
    const t = await api.chat.thread(projectId, threadId);
    const msgs = t.messages.map(fromServer);
    useChatStore.getState().setMessages(threadId, msgs);
    const last = msgs[msgs.length - 1];
    if (last?.role === "agent" && last.status === "thinking") this.watch(projectId, threadId);
  }

  watch(projectId: string, threadId: string): void {
    const key = `${projectId}:${threadId}`;
    if (this.watching.has(key)) return;
    this.watching.add(key);
    useChatStore.getState().setBusy(threadId);
    this.poll(projectId, threadId, key);
  }

  private poll(projectId: string, threadId: string, key: string): void {
    window.setTimeout(async () => {
      let stillThinking = true;
      try {
        const t = await api.chat.thread(projectId, threadId);
        const msgs = t.messages.map(fromServer);
        useChatStore.getState().setMessages(threadId, msgs);
        const last = msgs[msgs.length - 1];
        stillThinking = last?.role === "agent" && last.status === "thinking";
      } catch {
        // transient network hiccup while polling - the server-side work is unaffected, just keep trying
      }
      if (stillThinking) {
        this.poll(projectId, threadId, key);
        return;
      }
      this.watching.delete(key);
      if (useChatStore.getState().busyThread === threadId) useChatStore.getState().setBusy(null);
    }, POLL_MS);
  }
}

export const chatSync = new ChatSync();
