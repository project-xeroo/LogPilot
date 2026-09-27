import { MessageCircleQuestion } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useSession } from "@/store/session";
import { cn } from "@/utils/cn";
import { Tip } from "@/components/shared/overlays";

/**
 * "Why is this red?" is always answerable in place: every chart, table row and alert can hand a prepared
 * question to the agent. The question lands in the chat thread on the home screen and is sent immediately.
 */
export function AskAgent({ prompt, label = "Ask why", className, iconOnly }: { prompt: string; label?: string; className?: string; iconOnly?: boolean }) {
  const ask = useSession((s) => s.ask);
  const nav = useNavigate();
  const go = (e: React.MouseEvent) => {
    e.stopPropagation();
    ask(prompt);
    nav("/");
  };
  const btn = (
    <button
      type="button"
      onClick={go}
      aria-label={iconOnly ? `${label}: ${prompt}` : undefined}
      className={cn("inline-flex items-center gap-1.5 rounded-control px-2 py-1 text-sm font-medium text-teal-ink hover:bg-teal-soft", className)}
    >
      <MessageCircleQuestion className="size-4" aria-hidden />
      {!iconOnly && label}
    </button>
  );
  return <Tip label={prompt}>{btn}</Tip>;
}
