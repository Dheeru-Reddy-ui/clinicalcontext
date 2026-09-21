"use client";

import { Fragment, memo, useMemo } from "react";

import { cn } from "@/lib/utils";

const CITATION = /\[(\d{1,3})\]/g;

interface AnswerProseProps {
  text: string;
  /** Markers that resolve to a real citation. Others render as plain text. */
  known?: ReadonlySet<number>;
  activeMarker?: number | null;
  onCite?: (marker: number) => void;
  /** True while tokens are still arriving: shows a caret, no chips resolve yet. */
  streaming?: boolean;
  className?: string;
}

/**
 * Answer text with `[n]` turned into citation chips.
 *
 * Works on partial text: while streaming, chips are still rendered so the
 * reader sees where evidence will attach, but they only become buttons once
 * the citation list arrives (`known`). Paragraphs split on blank lines.
 */
export const AnswerProse = memo(function AnswerProse({
  text,
  known,
  activeMarker,
  onCite,
  streaming = false,
  className,
}: AnswerProseProps) {
  const paragraphs = useMemo(() => text.split(/\n{2,}/).filter((p) => p.trim()), [text]);

  return (
    <div className={cn("prose-answer", className)} aria-live={streaming ? "polite" : undefined}>
      {paragraphs.map((paragraph, pi) => (
        <p key={pi}>
          {renderWithChips(paragraph, known, activeMarker, onCite, streaming)}
          {streaming && pi === paragraphs.length - 1 && (
            <span aria-hidden className="ml-0.5 inline-block h-4 w-0.5 animate-pulse bg-foreground align-middle" />
          )}
        </p>
      ))}
      {paragraphs.length === 0 && streaming && (
        <p>
          <span aria-hidden className="inline-block h-4 w-0.5 animate-pulse bg-foreground align-middle" />
        </p>
      )}
    </div>
  );
});

function renderWithChips(
  paragraph: string,
  known: ReadonlySet<number> | undefined,
  activeMarker: number | null | undefined,
  onCite: ((marker: number) => void) | undefined,
  streaming: boolean,
) {
  const parts: React.ReactNode[] = [];
  let last = 0;
  for (const match of paragraph.matchAll(CITATION)) {
    const index = match.index ?? 0;
    const marker = Number(match[1]);
    if (index > last) parts.push(<Fragment key={`t${index}`}>{paragraph.slice(last, index)}</Fragment>);
    const resolvable = !streaming && (known?.has(marker) ?? true) && onCite;
    parts.push(
      resolvable ? (
        <button
          key={`c${index}`}
          type="button"
          className="cite-chip mx-0.5"
          data-active={activeMarker === marker}
          onClick={() => onCite(marker)}
          aria-label={`Open source ${marker}`}
          title={`Source ${marker}`}
        >
          {marker}
        </button>
      ) : (
        <span key={`c${index}`} className="cite-chip mx-0.5 opacity-70" aria-hidden>
          {marker}
        </span>
      ),
    );
    last = index + match[0].length;
  }
  if (last < paragraph.length) parts.push(<Fragment key="tail">{paragraph.slice(last)}</Fragment>);
  return parts;
}
