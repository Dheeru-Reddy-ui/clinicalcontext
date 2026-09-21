"use client";

import { Scale } from "lucide-react";

import type { Citation, Contradiction } from "@/lib/domain";
import { yearOf } from "@/lib/text";
import { cn } from "@/lib/utils";

const AXIS_LABEL: Record<Contradiction["axis"], string> = {
  temporal: "The difference tracks publication year",
  population: "The studies looked at different populations",
  endpoint: "The studies measured different endpoints",
  unclear: "The reason for the disagreement is unclear",
  none: "",
};

/**
 * Spec #4: never bury a contradiction. Positions side by side, each with its
 * sources, and the detector's explanation of why they differ.
 */
export function ContradictionView({
  contradiction,
  citations,
  onCite,
  className,
}: {
  contradiction: Contradiction;
  citations: Citation[];
  onCite: (marker: number) => void;
  className?: string;
}) {
  if (!contradiction.detected) return null;
  const byMarker = new Map(citations.map((c) => [c.marker, c]));

  return (
    <section
      className={cn("rounded-lg border border-conflict/40 bg-conflict-bg/40", className)}
      aria-labelledby="contradiction-heading"
    >
      <header className="flex items-start gap-3 px-4 pt-4">
        <span className="grid size-8 shrink-0 place-items-center rounded-md bg-conflict-bg text-conflict-fg">
          <Scale className="size-4" aria-hidden />
        </span>
        <div>
          <h2 id="contradiction-heading" className="text-sm font-semibold text-conflict-fg">
            The sources disagree
          </h2>
          <p className="text-sm text-foreground/80">
            {AXIS_LABEL[contradiction.axis] || "Two positions were found in the evidence."}
            {contradiction.axis === "temporal" &&
              " — the more recent sources reach a different conclusion, suggesting the evidence or recommendation evolved."}
          </p>
        </div>
      </header>

      <div className="grid gap-3 p-4 md:grid-cols-2">
        {contradiction.positions.map((position, i) => {
          const sources = position.markers.map((m) => byMarker.get(m)).filter((c): c is Citation => Boolean(c));
          return (
            <article key={i} className="rounded-md border bg-card p-3">
              <p className="mb-2 text-sm font-medium">
                {position.stance}
                {position.year && <span className="ml-2 font-mono text-xs text-muted-foreground">{position.year}</span>}
              </p>
              <ul className="space-y-1.5">
                {sources.map((c) => (
                  <li key={c.marker} className="flex items-start gap-2 text-xs">
                    <button type="button" className="cite-chip mt-0.5 shrink-0" onClick={() => onCite(c.marker)} aria-label={`Open source ${c.marker}`}>
                      {c.marker}
                    </button>
                    <span className="min-w-0">
                      <span className="line-clamp-2 text-foreground/90">{c.title ?? "Untitled source"}</span>
                      <span className="text-muted-foreground">
                        {[c.journal, yearOf(c.publication_date)].filter(Boolean).join(" · ")}
                      </span>
                    </span>
                  </li>
                ))}
                {sources.length === 0 && (
                  <li className="text-xs text-muted-foreground">Sources [{position.markers.join(", ")}]</li>
                )}
              </ul>
            </article>
          );
        })}
      </div>

      {contradiction.explanation && (
        <p className="border-t border-conflict/30 px-4 py-3 text-sm text-foreground/80">
          {contradiction.explanation}
        </p>
      )}
    </section>
  );
}
