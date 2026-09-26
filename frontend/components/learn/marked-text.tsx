"use client";

import { ExternalLink } from "lucide-react";
import { Fragment } from "react";

import type { Schemas } from "@/lib/domain";
import { cn } from "@/lib/utils";

type Citation = Schemas["Citation"];

const MARKER = /\[(\d+)\]/g;

/**
 * Text whose "[n]" markers become small numbered chips; ``onMarker`` makes
 * them buttons (to show the source, or the line of a note).
 */
export function MarkedText({
  text,
  onMarker,
  label = "Source",
  className,
}: {
  text: string;
  onMarker?: (n: number) => void;
  label?: string;
  className?: string;
}) {
  const parts: Array<string | number> = [];
  let last = 0;
  for (const match of text.matchAll(MARKER)) {
    const index = match.index ?? 0;
    if (index > last) parts.push(text.slice(last, index));
    parts.push(Number(match[1]));
    last = index + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return (
    <span className={className}>
      {parts.map((part, i) =>
        typeof part === "string" ? (
          <Fragment key={i}>{part}</Fragment>
        ) : onMarker ? (
          <button
            key={i}
            type="button"
            onClick={() => onMarker(part)}
            className="mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded bg-citation-bg px-1 align-text-top text-[10px] font-semibold text-citation-fg hover:ring-1 hover:ring-citation"
            aria-label={`${label} ${part}`}
          >
            {part}
          </button>
        ) : (
          <sup key={i} className="mx-0.5 text-[10px] font-semibold text-citation-fg">
            [{part}]
          </sup>
        ),
      )}
    </span>
  );
}

function year(value: string | null): string | null {
  return value ? value.slice(0, 4) : null;
}

/** The numbered sources a quiz or lesson was written from. */
export function SourceList({ sources, active }: { sources: Citation[]; active?: number | null }) {
  return (
    <ol className="flex flex-col gap-2" data-testid="source-list">
      {sources.map((source) => {
        const href = source.url ?? (source.pmid ? `https://pubmed.ncbi.nlm.nih.gov/${source.pmid}/` : null);
        return (
          <li
            key={source.marker}
            id={`source-${source.marker}`}
            className={cn(
              "flex gap-2 rounded-lg border p-2.5 text-sm transition-colors",
              active === source.marker && "border-citation bg-citation-bg/40",
            )}
          >
            <span className="mt-0.5 inline-flex h-5 min-w-5 shrink-0 items-center justify-center rounded bg-citation-bg px-1 text-[11px] font-semibold text-citation-fg">
              {source.marker}
            </span>
            <span className="min-w-0">
              {href ? (
                <a href={href} target="_blank" rel="noopener noreferrer" className="font-medium leading-5 hover:underline">
                  {source.title ?? "Untitled source"}
                  <ExternalLink className="ml-1 inline size-3 align-baseline text-muted-foreground" aria-hidden />
                </a>
              ) : (
                <span className="font-medium leading-5">{source.title ?? "Untitled source"}</span>
              )}
              <span className="mt-0.5 block text-xs text-muted-foreground">
                {[year(source.publication_date), source.journal, source.study_type?.replace(/_/g, " "), source.evidence_grade && `grade ${source.evidence_grade}`]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
