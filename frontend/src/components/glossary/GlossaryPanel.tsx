import { BookOpen, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Dialog, DialogContent } from "@/components/shared/overlays";
import { Input, Spinner } from "@/components/shared/ui";
import { useGlossary } from "@/components/glossary/Term";
import { useSession } from "@/store/session";

/** Persistent, searchable glossary of the log and reliability terms the agent itself uses. Open from any screen. */
export function GlossaryPanel() {
  const open = useSession((s) => s.glossaryOpen);
  const set = useSession((s) => s.setGlossary);
  const { data, isLoading } = useGlossary();
  const [q, setQ] = useState("");
  const list = useMemo(() => {
    const s = q.trim().toLowerCase();
    const rows = (data ?? []).filter((t) => !s || t.term.toLowerCase().includes(s) || t.definition.toLowerCase().includes(s));
    const by = new Map<string, typeof rows>();
    rows.forEach((t) => by.set(t.category, [...(by.get(t.category) ?? []), t]));
    return [...by.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [data, q]);

  return (
    <Dialog open={open} onOpenChange={set}>
      <DialogContent side="right" title="Glossary" description="Terms LogPilot uses in its explanations, in plain language.">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-muted" aria-hidden />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search terms, e.g. p99 or baseline" className="pl-8" aria-label="Search the glossary" autoFocus />
        </div>
        {isLoading && <Spinner label="Loading terms" />}
        {!isLoading && list.length === 0 && <p className="py-6 text-center text-sm text-muted">No term matches "{q}". Try a shorter word, or ask the agent in chat.</p>}
        <div className="space-y-5">
          {list.map(([cat, terms]) => (
            <section key={cat}>
              <h3 className="mb-1 text-sm font-semibold capitalize text-muted">{cat}</h3>
              <dl className="hairlines">
                {terms.map((t) => (
                  <div key={t.id} className="py-2">
                    <dt className="font-medium">{t.term}</dt>
                    <dd className="text-sm text-muted">{t.definition}</dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function GlossaryButton() {
  const set = useSession((s) => s.setGlossary);
  return (
    <button type="button" onClick={() => set(true)} className="inline-flex items-center gap-2 rounded-control px-2.5 py-1.5 text-sm text-muted hover:bg-ink/[.06] hover:text-ink">
      <BookOpen className="size-4" aria-hidden /> Glossary
    </button>
  );
}
