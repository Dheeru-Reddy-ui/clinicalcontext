"use client";

import { ExternalLink } from "lucide-react";
import { forwardRef } from "react";

import { GradeBadge, StanceDot } from "@/components/clinical/badges";
import type { Citation } from "@/lib/domain";
import { humanStudyType, yearOf } from "@/lib/text";
import { cn } from "@/lib/utils";

function sourceLink(c: Citation): string | null {
  if (c.url) return c.url;
  if (c.pmid) return `https://pubmed.ncbi.nlm.nih.gov/${c.pmid}/`;
  if (c.doi) return `https://doi.org/${c.doi}`;
  return null;
}

/**
 * The sources behind one answer, numbered as the answer cites them: what
 * each is (design, grade, year, journal), where it stands relative to the
 * answer, the passage used, and a link to the original.
 */
export const SourceList = forwardRef<
  HTMLOListElement,
  { citations: Citation[]; activeMarker?: number | null; className?: string }
>(function SourceList({ citations, activeMarker, className }, ref) {
  return (
    <ol ref={ref} className={cn("flex flex-col gap-2", className)} aria-label="Sources">
      {citations.map((c) => {
        const link = sourceLink(c);
        const year = yearOf(c.publication_date);
        return (
          <li
            key={c.marker}
            id={`source-${c.chunk_id}`}
            data-marker={c.marker}
            data-active={activeMarker === c.marker}
            className="rounded-md border bg-card p-3 text-sm transition-colors data-[active=true]:border-citation data-[active=true]:bg-citation-bg/40"
          >
            <div className="flex items-start gap-2">
              <span className="cite-chip mt-0.5 shrink-0">{c.marker}</span>
              <div className="min-w-0 flex-1">
                <p className="font-medium leading-5">
                  {link ? (
                    <a
                      href={link}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="hover:underline"
                    >
                      {c.title ?? "Untitled source"}
                      <ExternalLink className="ml-1 inline size-3 align-baseline text-muted-foreground" aria-hidden />
                    </a>
                  ) : (
                    (c.title ?? "Untitled source")
                  )}
                </p>
                <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                  <span>{humanStudyType(c.study_type)}</span>
                  {c.evidence_grade && <GradeBadge grade={c.evidence_grade} />}
                  {year && <span className="font-mono">{year}</span>}
                  {c.journal && <span className="truncate">{c.journal}</span>}
                  {c.stance && <StanceDot stance={c.stance} />}
                </div>
                <p className="mt-2 line-clamp-4 text-[13px] leading-5 text-foreground/80">{c.passage}</p>
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
});
