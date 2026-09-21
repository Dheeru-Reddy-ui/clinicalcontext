"use client";

import { BookMarked, ChevronLeft, ChevronRight, ExternalLink, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef } from "react";

import { GradeBadge } from "@/components/clinical/badges";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { Citation, Stance } from "@/lib/domain";
import { bestSupportingSpan, claimsCiting, humanStudyType, yearOf } from "@/lib/text";
import { cn } from "@/lib/utils";

interface CitationPanelProps {
  citations: Citation[];
  answer: string;
  activeMarker: number | null;
  onChange: (marker: number | null) => void;
  stances?: ReadonlyMap<number, Stance>;
  /** Hidden on the public permalink; the reader has no binders. */
  onSavePassage?: (citation: Citation) => void;
  /** Public pages cannot link into the authenticated library. */
  libraryLinks?: boolean;
  className?: string;
}

/**
 * "A clinician must be able to verify any claim in two clicks."
 *
 * Click 1 is the [n] chip; this panel is click 2. It shows the exact passage
 * retrieval cited, with the sentence that best supports the citing claim
 * highlighted, then everything needed to judge the source: journal, year,
 * study design, evidence grade, and the way out to PubMed or the DOI.
 */
export function CitationPanel({
  citations,
  answer,
  activeMarker,
  onChange,
  stances,
  onSavePassage,
  libraryLinks = true,
  className,
}: CitationPanelProps) {
  const ordered = useMemo(() => [...citations].sort((a, b) => a.marker - b.marker), [citations]);
  const index = ordered.findIndex((c) => c.marker === activeMarker);
  const citation = index >= 0 ? ordered[index] : undefined;
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (citation) headingRef.current?.focus();
  }, [citation]);

  useEffect(() => {
    if (!citation) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onChange(null);
      if (event.key === "ArrowRight" || event.key === "]") step(1);
      if (event.key === "ArrowLeft" || event.key === "[") step(-1);
    };
    const step = (delta: number) => {
      const next = ordered[index + delta];
      if (next) onChange(next.marker);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [citation, index, ordered, onChange]);

  const highlight = useMemo(() => {
    if (!citation) return null;
    return bestSupportingSpan(citation.passage, claimsCiting(answer, citation.marker));
  }, [citation, answer]);

  if (!citation) {
    return (
      <aside
        className={cn("flex flex-col items-center justify-center gap-2 p-6 text-center", className)}
        aria-label="Source panel"
      >
        <p className="text-sm font-medium">Sources</p>
        <p className="max-w-[26ch] text-sm text-muted-foreground">
          Select any <span className="cite-chip mx-0.5" aria-hidden>n</span> in the answer to read the
          passage it rests on.
        </p>
        {ordered.length > 0 && (
          <ol className="mt-4 w-full space-y-1 text-left" aria-label="All sources">
            {ordered.map((c) => (
              <li key={c.marker}>
                <button
                  type="button"
                  onClick={() => onChange(c.marker)}
                  className="flex w-full items-start gap-2 rounded-sm px-2 py-1.5 text-left text-xs hover:bg-accent focus-visible:bg-accent"
                >
                  <span className="cite-chip shrink-0">{c.marker}</span>
                  <span className="line-clamp-2 text-muted-foreground">{c.title ?? "Untitled source"}</span>
                </button>
              </li>
            ))}
          </ol>
        )}
      </aside>
    );
  }

  const year = yearOf(citation.publication_date);
  const stance = stances?.get(citation.marker);

  return (
    <aside className={cn("flex h-full flex-col", className)} aria-label={`Source ${citation.marker}`}>
      <header className="flex items-center gap-2 border-b px-4 py-2.5">
        <span className="cite-chip" data-active="true">
          {citation.marker}
        </span>
        <h2 ref={headingRef} tabIndex={-1} className="min-w-0 flex-1 truncate text-sm font-medium outline-none">
          {citation.title ?? "Untitled source"}
        </h2>
        <div className="flex items-center gap-0.5">
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon-xs"
                  disabled={index <= 0}
                  onClick={() => onChange(ordered[index - 1]?.marker ?? citation.marker)}
                  aria-label="Previous source"
                />
              }
            >
              <ChevronLeft />
            </TooltipTrigger>
            <TooltipContent>
              Previous <Kbd>[</Kbd>
            </TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon-xs"
                  disabled={index >= ordered.length - 1}
                  onClick={() => onChange(ordered[index + 1]?.marker ?? citation.marker)}
                  aria-label="Next source"
                />
              }
            >
              <ChevronRight />
            </TooltipTrigger>
            <TooltipContent>
              Next <Kbd>]</Kbd>
            </TooltipContent>
          </Tooltip>
          <Button variant="ghost" size="icon-xs" onClick={() => onChange(null)} aria-label="Close source panel">
            <X />
          </Button>
        </div>
      </header>

      <div className="scrollbar-thin flex-1 overflow-y-auto">
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 border-b px-4 py-3 text-xs">
          <dt className="text-muted-foreground">Journal</dt>
          <dd className="truncate" title={citation.journal ?? undefined}>
            {citation.journal ?? "—"}
          </dd>
          <dt className="text-muted-foreground">Year</dt>
          <dd>{year ?? "—"}</dd>
          <dt className="text-muted-foreground">Design</dt>
          <dd>{humanStudyType(citation.study_type)}</dd>
          <dt className="text-muted-foreground">Evidence</dt>
          <dd>
            <GradeBadge grade={citation.evidence_grade} showWord />
          </dd>
          {citation.section && (
            <>
              <dt className="text-muted-foreground">Section</dt>
              <dd className="capitalize">{citation.section}</dd>
            </>
          )}
          {stance && (
            <>
              <dt className="text-muted-foreground">Stance</dt>
              <dd className="capitalize">{stance}</dd>
            </>
          )}
        </dl>

        <div className="px-4 py-3">
          <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            Cited passage
          </p>
          <blockquote className="text-sm leading-6" data-testid="cited-passage">
            {highlight && highlight.end > highlight.start ? (
              <>
                {citation.passage.slice(0, highlight.start)}
                <mark className="passage-highlight" data-testid="supporting-sentence">
                  {citation.passage.slice(highlight.start, highlight.end)}
                </mark>
                {citation.passage.slice(highlight.end)}
              </>
            ) : (
              citation.passage
            )}
          </blockquote>
          {highlight && highlight.score > 0 && (
            <p className="mt-2 text-[11px] text-muted-foreground">
              Highlighted: the sentence that best matches the claim citing [{citation.marker}].
            </p>
          )}
        </div>
      </div>

      <footer className="flex flex-wrap items-center gap-1.5 border-t px-3 py-2">
        <SourceLinks citation={citation} libraryLinks={libraryLinks} />
        {onSavePassage && (
          <Button variant="ghost" size="xs" className="ml-auto" onClick={() => onSavePassage(citation)}>
            <BookMarked /> Save passage
          </Button>
        )}
      </footer>
    </aside>
  );
}

/** The way out to the paper itself: PubMed first, then the DOI, then any URL. */
export function sourceHref(c: Pick<Citation, "pmid" | "doi" | "url">): string | null {
  if (c.pmid) return `https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(c.pmid)}/`;
  if (c.doi) return `https://doi.org/${c.doi.replace(/^https?:\/\/(dx\.)?doi\.org\//, "")}`;
  return c.url ?? null;
}

function SourceLinks({ citation, libraryLinks }: { citation: Citation; libraryLinks: boolean }) {
  const href = sourceHref(citation);
  const label = citation.pmid ? "PubMed" : citation.doi ? "DOI" : "Source";
  return (
    <>
      {href && (
        <Button variant="outline" size="xs" nativeButton={false} render={<a href={href} target="_blank" rel="noreferrer noopener" />}>
          <ExternalLink /> {label}
          {citation.pmid && <span className="font-mono text-muted-foreground">{citation.pmid}</span>}
        </Button>
      )}
      {libraryLinks && (
        <Button
          variant="ghost"
          size="xs"
          nativeButton={false}
          render={<Link href={`/app/library/${citation.document_id}?chunk=${citation.chunk_id}`} />}
        >
          In library
        </Button>
      )}
    </>
  );
}
