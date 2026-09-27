import { useQuery } from "@tanstack/react-query";
import { History, Paperclip, RotateCcw, SendHorizonal, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "@/api/endpoints";
import { ChatHistoryDialog } from "@/components/chat/ChatHistoryDialog";
import { MessageView } from "@/components/chat/MessageView";
import { Button } from "@/components/shared/ui";
import { UploadDialog } from "@/components/shared/UploadDialog";
import { useChat } from "@/hooks/useChat";
import { useProjectId } from "@/hooks/useProject";
import { can, useSession } from "@/store/session";
import { cn } from "@/utils/cn";

/**
 * The primary surface: talk to the agent in plain language. Opens with 4-6 example questions relevant to what the
 * agent is currently seeing, so nobody starts from a blank box. Used on the home screen and the full Chat page.
 */
export function ChatPanel({ className, tall }: { className?: string; tall?: boolean }) {
  const projectId = useProjectId();
  const user = useSession((s) => s.user);
  const guided = useSession((s) => s.guided);
  const pending = useSession((s) => s.pendingPrompt);
  const clearPrompt = useSession((s) => s.clearPrompt);
  const { messages, busy, send, rate, reset } = useChat(projectId || null);
  const [draft, setDraft] = useState("");
  const [upload, setUpload] = useState(false);
  const [history, setHistory] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  const suggestions = useQuery({ queryKey: ["suggestions", projectId], queryFn: () => api.chat.suggestions(projectId), enabled: !!projectId && can(user, "chat.use"), staleTime: 60_000 });

  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [messages]);
  useEffect(() => {
    if (pending && !busy && projectId) { send(pending); clearPrompt(); }
  }, [pending, busy, projectId, send, clearPrompt]);
  useEffect(() => {
    const el = box.current;
    if (el) { el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 140) + "px"; }
  }, [draft]);

  if (!can(user, "chat.use")) {
    return (
      <div className={cn("rounded-panel border border-line bg-surface p-6 text-sm text-muted", className)}>
        Chat isn't available for the Viewer role. You can still follow everything the agent notices in the feed, search logs and read reports.
      </div>
    );
  }

  const submit = () => { const t = draft.trim(); if (t && !busy) { send(t); setDraft(""); } };
  const empty = messages.length === 0;

  return (
    <section aria-label="Chat with the agent" className={cn("flex min-h-0 flex-col rounded-panel border border-line bg-surface", tall ? "h-[calc(100vh-9rem)] min-h-[28rem]" : "h-[calc(100vh-9rem)] min-h-[26rem]", className)}>
      <div className="flex items-center gap-2 border-b border-line px-4 py-2.5">
        <Sparkles className="size-4 text-teal" aria-hidden />
        <h2 className="text-base">Ask the agent</h2>
        <button type="button" onClick={() => setHistory(true)} className={cn("inline-flex items-center gap-1.5 rounded-control px-2 py-1 text-sm text-muted hover:bg-ink/[.06] hover:text-ink", !empty && "ml-auto")}>
          <History className="size-3.5" aria-hidden /> History
        </button>
        {!empty && <button type="button" onClick={reset} className="inline-flex items-center gap-1.5 rounded-control px-2 py-1 text-sm text-muted hover:bg-ink/[.06] hover:text-ink"><RotateCcw className="size-3.5" aria-hidden /> New conversation</button>}
      </div>
      <ChatHistoryDialog projectId={projectId || null} open={history} onOpenChange={setHistory} />

      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4" aria-live="polite">
        {empty && (
          <div className="mx-auto max-w-xl py-6">
            <p className="agent-voice">I'm watching your services' logs and forecasting failures before they happen. Ask me anything about what I'm seeing.{guided ? " I'll explain any terms as we go." : ""}</p>
            <ul className="mt-4 flex flex-wrap gap-2" aria-label="Suggested questions">
              {(suggestions.data?.prompts ?? ["How is everything doing right now?", "Which services are most likely to fail next?", "Show me the top errors from the last hour", "Summarise what happened today"]).map((p) => (
                <li key={p}><button type="button" onClick={() => send(p)} className="rounded-full border border-line bg-raised px-3.5 py-1.5 text-sm hover:border-teal hover:text-teal-ink">{p}</button></li>
              ))}
            </ul>
          </div>
        )}
        {messages.map((m) => <MessageView key={m.id} m={m} onFollowup={send} onRate={rate} />)}
        <div ref={end} />
      </div>

      <form onSubmit={(e) => { e.preventDefault(); submit(); }} className="border-t border-line p-3">
        <div className="flex items-end gap-2 rounded-panel border border-line bg-raised p-1.5 focus-within:border-teal">
          {can(user, "logs.upload") && (
            <button type="button" onClick={() => setUpload(true)} className="grid size-9 shrink-0 place-items-center rounded-control text-muted hover:bg-ink/[.06] hover:text-ink" aria-label="Upload logs"><Paperclip className="size-4" /></button>
          )}
          <label htmlFor="chat-input" className="sr-only">Message the agent</label>
          <textarea
            id="chat-input" ref={box} rows={1} value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Why did checkout fail at 3am?"
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
            className="max-h-36 min-h-9 flex-1 resize-none bg-transparent px-1 py-2 text-[15px] outline-none placeholder:text-muted/80" disabled={busy && false}
          />
          <Button type="submit" variant="primary" size="icon" disabled={!draft.trim() || busy} aria-label="Send"><SendHorizonal /></Button>
        </div>
        <p className="mt-1.5 px-1 text-xs text-muted">Answers cite the logs they came from. The agent proposes actions but never runs them.</p>
      </form>
      <UploadDialog open={upload} onOpenChange={setUpload} />
    </section>
  );
}
