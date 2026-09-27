import { useState } from "react";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { FeedList } from "@/components/shared/FeedList";
import { RiskGlance } from "@/components/risk-board/RiskGlance";
import { cn } from "@/utils/cn";

/**
 * Agent Feed (home). Chat is the primary surface and sits beside the feed of everything the agent has noticed,
 * said or drafted - answered questions appear in that same stream. On tablets/phones the two share the screen via tabs.
 */
export default function HomePage() {
  const [tab, setTab] = useState<"chat" | "feed">("chat");
  return (
    <div>
      <div role="note" className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-control bg-teal-soft px-4 py-3 text-teal-ink">
        <p className="text-sm">
          Logs on this page are pulled live from a real demo storefront — browse it, add to cart or check out and
          watch the agent notice it here within seconds.
        </p>
        <a
          href="https://testapp.fawwazkhan.dev"
          target="_blank"
          rel="noreferrer"
          className="inline-flex shrink-0 items-center justify-center whitespace-nowrap rounded-control bg-teal px-4 py-2 text-sm font-medium text-paper transition-colors hover:bg-teal/90"
        >
          Open the live demo storefront →
        </a>
      </div>
      <div role="tablist" aria-label="Home view" className="mb-4 flex gap-1 rounded-control bg-ink/[.06] p-1 lg:hidden">
        {(["chat", "feed"] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={cn("flex-1 rounded-[5px] px-3 py-1.5 text-sm font-medium", tab === t ? "bg-raised shadow-sm" : "text-muted")}>
            {t === "chat" ? "Ask the agent" : "What the agent noticed"}
          </button>
        ))}
      </div>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] 2xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <div className={cn("min-w-0", tab !== "feed" && "hidden lg:block")}>
          <RiskGlance />
          <div className="mt-6">
            <h1 className="mb-1">What the agent noticed</h1>
            <p className="mb-3 max-w-[60ch] text-sm text-muted">Alerts, anomalies, drafts and answered questions, newest first.</p>
            <FeedList />
          </div>
        </div>
        <div className={cn("min-w-0 lg:sticky lg:top-[4.2rem] lg:self-start", tab !== "chat" && "hidden lg:block")}>
          <ChatPanel />
        </div>
      </div>
    </div>
  );
}
