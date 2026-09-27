import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/utils/cn";

/** Renders agent-authored markdown in the agent's serif voice. Raw HTML is never rendered. */
export function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={cn("agent-voice", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: ({ node: _n, ...p }) => <a {...p} target="_blank" rel="noreferrer noopener" className="text-teal underline underline-offset-2" /> }}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
