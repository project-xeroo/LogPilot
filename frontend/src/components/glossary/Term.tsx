import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { api } from "@/api/endpoints";
import { useSession } from "@/store/session";
import { cn } from "@/utils/cn";

/** Shared glossary (cached once). Used by Term, the glossary panel and answer footnotes. */
export function useGlossary() {
  return useQuery({ queryKey: ["glossary"], queryFn: () => api.glossary(), staleTime: 3_600_000 });
}

/**
 * Plain-language mode: a label is paired with a one-line definition the first time the person meets it
 * ("p99 latency - the response time 99% of requests are faster than"). Dismissing collapses it for good.
 * In standard mode the label is plain text with the definition available on hover/focus via title.
 */
export function Term({ term, children, block }: { term: string; children?: React.ReactNode; block?: boolean }) {
  const guided = useSession((s) => s.guided);
  const dismissed = useSession((s) => s.dismissedTerms);
  const dismiss = useSession((s) => s.dismissTerm);
  const { data } = useGlossary();
  const def = data?.find((t) => t.term.toLowerCase() === term.toLowerCase())?.definition;
  const label = children ?? term;
  if (!def) return <>{label}</>;
  const open = guided && !dismissed.includes(term.toLowerCase());
  return (
    <span className={cn(block ? "block" : "inline")}>
      <span title={guided ? undefined : def} className={cn(!guided && "cursor-help decoration-dotted underline underline-offset-4 decoration-muted/60")}>{label}</span>
      {open && (
        <span className="mt-1 flex items-start gap-1.5 rounded-control bg-teal-soft px-2 py-1 text-xs font-normal leading-4 text-teal-ink">
          <span>{def}</span>
          <button type="button" onClick={() => dismiss(term.toLowerCase())} className="shrink-0 rounded p-0.5 hover:bg-teal/15" aria-label={`Hide the definition of ${term}`}>
            <X className="size-3" />
          </button>
        </span>
      )}
    </span>
  );
}

/** Footnote strip under an agent answer: definitions for glossary terms that appear in it (first encounters only). */
export function TermsInText({ text }: { text: string }) {
  const guided = useSession((s) => s.guided);
  const dismissed = useSession((s) => s.dismissedTerms);
  const dismiss = useSession((s) => s.dismissTerm);
  const { data } = useGlossary();
  if (!guided || !data) return null;
  const low = text.toLowerCase();
  const hits = data.filter((t) => t.term.length > 3 && !dismissed.includes(t.term.toLowerCase()) && new RegExp(`\\b${t.term.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(low)).slice(0, 3);
  if (!hits.length) return null;
  return (
    <ul className="mt-3 space-y-1.5 border-t border-line pt-2" aria-label="Terms used in this answer">
      {hits.map((t) => (
        <li key={t.id} className="flex items-start gap-2 text-xs text-muted">
          <span><strong className="font-semibold text-ink">{t.term}</strong> means: {t.definition}</span>
          <button type="button" className="shrink-0 rounded p-0.5 hover:bg-ink/10" onClick={() => dismiss(t.term.toLowerCase())} aria-label={`Hide the definition of ${t.term}`}>
            <X className="size-3" />
          </button>
        </li>
      ))}
    </ul>
  );
}
