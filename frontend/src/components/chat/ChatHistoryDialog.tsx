import { useQuery } from "@tanstack/react-query";
import { MessageSquare } from "lucide-react";
import { api } from "@/api/endpoints";
import { Dialog, DialogContent } from "@/components/shared/overlays";
import { useSession } from "@/store/session";
import { cn } from "@/utils/cn";
import { fmtDateTime } from "@/utils/format";

/** Past conversations: `GET .../chat/threads` already existed server-side (each thread survives a refresh
 * on its own via `threadId`), this just gives it a UI a person can browse and switch between. */
export function ChatHistoryDialog({ projectId, open, onOpenChange }: { projectId: string | null; open: boolean; onOpenChange: (o: boolean) => void }) {
  const threadId = useSession((s) => s.threadId);
  const setThread = useSession((s) => s.setThread);
  const threads = useQuery({ queryKey: ["chat-threads", projectId], queryFn: () => api.chat.threads(projectId!), enabled: !!projectId && open });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Conversation history" description="Pick up a past conversation with the agent, or start a new one.">
        {threads.isLoading && <p className="py-6 text-center text-sm text-muted">Loading...</p>}
        {threads.data?.length === 0 && <p className="py-6 text-center text-sm text-muted">No past conversations yet.</p>}
        <ul className="max-h-96 space-y-1 overflow-y-auto">
          {threads.data?.map((t) => (
            <li key={t.id}>
              <button
                type="button"
                onClick={() => { setThread(t.id); onOpenChange(false); }}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-control px-3 py-2 text-left text-sm hover:bg-ink/[.06]",
                  t.id === threadId && "bg-teal-soft text-teal-ink hover:bg-teal-soft",
                )}
              >
                <MessageSquare className="size-4 shrink-0 text-muted" aria-hidden />
                <span className="min-w-0 flex-1 truncate">{t.title}</span>
                <span className="shrink-0 text-xs text-muted">{fmtDateTime(t.updated_at)}</span>
              </button>
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  );
}
