import { ChatPanel } from "@/components/chat/ChatPanel";
import { PageHeader } from "@/components/shared/ui";

export default function ChatPage() {
  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader title="Chat">Ask in plain language. Every agent tool is reachable from here: search, health, forecasts, root cause, reports and deployment comparisons.</PageHeader>
      <ChatPanel tall />
    </div>
  );
}
