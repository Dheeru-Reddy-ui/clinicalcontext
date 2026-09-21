"use client";

import dynamic from "next/dynamic";
import { useCallback, useMemo, useState, type ReactNode } from "react";

import { AbstentionView } from "@/components/answer/abstention-view";
import { AnswerFooter } from "@/components/answer/answer-footer";
import type { AnswerModel } from "@/components/answer/answer-model";
import { AnswerProse } from "@/components/answer/answer-prose";
import { ComparisonTable } from "@/components/answer/comparison-table";
import { ContradictionView } from "@/components/answer/contradiction-view";
import { FeedbackBar } from "@/components/answer/feedback-bar";
import { RedFlagBanner } from "@/components/answer/guardrail-view";
import { ConfidenceBadge, GradeBadge, StatusPill } from "@/components/clinical/badges";
import { LazyOnVisible } from "@/components/clinical/lazy-on-visible";
import { useIsDesktop } from "@/hooks/use-media-query";
import { stanceByMarker, type Citation } from "@/lib/domain";
import { cn } from "@/lib/utils";

// The citation panel and the mobile sheet only matter once a chip is clicked;
// keeping them out of the initial hydration pass is what keeps mobile TBT down.
const CitationPanel = dynamic(
  () => import("@/components/answer/citation-panel").then((m) => m.CitationPanel),
  { ssr: false, loading: () => <div className="h-full min-h-[24rem]" aria-hidden /> },
);
const MobileSourceSheet = dynamic(
  () => import("@/components/answer/mobile-source-sheet").then((m) => m.MobileSourceSheet),
  { ssr: false },
);
// Recharts is ~150 kB; the timeline renders after the answer, so load it after too.
const EvidenceTimeline = dynamic(
  () => import("@/components/answer/evidence-timeline").then((m) => m.EvidenceTimeline),
  { ssr: false, loading: () => <div className="h-56 animate-pulse rounded-lg border bg-card" aria-hidden /> },
);

interface AnswerViewProps {
  answer: AnswerModel;
  /** Live text while streaming; the model's content once done. */
  streamingText?: string;
  streaming?: boolean;
  escalationBanner?: string | null;
  /** Row of actions rendered in the header (share, follow, save, export). */
  actions?: ReactNode;
  showFeedback?: boolean;
  showFooter?: boolean;
  /** Public pages: no binder saving, no library links. */
  readOnly?: boolean;
  onSavePassage?: (citation: Citation) => void;
  onRetry?: (query: string) => void;
  className?: string;
}

export function AnswerView({
  answer,
  streamingText,
  streaming = false,
  escalationBanner,
  actions,
  showFeedback = true,
  showFooter = true,
  readOnly = false,
  onSavePassage,
  onRetry,
  className,
}: AnswerViewProps) {
  const [activeMarker, setActiveMarker] = useState<number | null>(null);
  const isDesktop = useIsDesktop();
  const known = useMemo(() => new Set(answer.citations.map((c) => c.marker)), [answer.citations]);
  const stances = useMemo(() => stanceByMarker(answer.contradiction), [answer.contradiction]);
  const onCite = useCallback((m: number) => setActiveMarker(m), []);
  const banner = escalationBanner ?? answer.reasoning.escalation_banner;
  const text = streaming ? (streamingText ?? "") : answer.content;
  const hasSources = answer.citations.length > 0;

  const panel = (
    <CitationPanel
      citations={answer.citations}
      answer={answer.content}
      activeMarker={activeMarker}
      onChange={setActiveMarker}
      stances={stances}
      onSavePassage={readOnly ? undefined : onSavePassage}
      libraryLinks={!readOnly}
      className="h-full"
    />
  );

  return (
    <div className={cn("grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]", className)}>
      <article className="min-w-0 space-y-4" aria-label="Answer">
        {banner && <RedFlagBanner message={banner} />}

        <header className="flex flex-wrap items-center gap-2">
          {answer.confidence && <ConfidenceBadge level={answer.confidence} />}
          <GradeBadge grade={answer.evidenceGrade} showWord />
          {answer.abstained && <StatusPill kind="abstained" />}
          {answer.contradiction.detected && <StatusPill kind="conflict" />}
          {answer.cached && <StatusPill kind="cached" />}
          {actions && <div className="ml-auto flex items-center gap-1">{actions}</div>}
        </header>

        {answer.contradiction.detected && (
          <ContradictionView contradiction={answer.contradiction} citations={answer.citations} onCite={onCite} />
        )}

        {answer.abstained && !streaming ? (
          <AbstentionView answer={answer} onCite={onCite} onRetry={onRetry} />
        ) : answer.comparison ? (
          <>
            <AnswerProse text={text} known={known} activeMarker={activeMarker} onCite={onCite} streaming={streaming} />
            <ComparisonTable table={answer.comparison} activeMarker={activeMarker} onCite={onCite} />
          </>
        ) : (
          <div className="rounded-lg border bg-card p-5">
            <AnswerProse text={text} known={known} activeMarker={activeMarker} onCite={onCite} streaming={streaming} />
          </div>
        )}

        {!streaming && hasSources && (
          <LazyOnVisible placeholder={<div className="h-56 rounded-lg border bg-card" aria-hidden />}>
            <EvidenceTimeline
              citations={answer.citations}
              contradiction={answer.contradiction}
              activeMarker={activeMarker}
              onCite={onCite}
            />
          </LazyOnVisible>
        )}

        {!streaming && (showFeedback || showFooter) && (
          <div className="space-y-3">
            {showFeedback && answer.answerId && !readOnly && <FeedbackBar answerId={answer.answerId} />}
            {showFooter && <AnswerFooter answer={answer} />}
          </div>
        )}
      </article>

      {/* Desktop: persistent side column. Mobile: a sheet that opens on tap. */}
      <div className="hidden min-h-[24rem] rounded-lg border bg-card lg:block lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)]">
        {panel}
      </div>
      {!isDesktop && activeMarker !== null && (
        <MobileSourceSheet open onClose={() => setActiveMarker(null)}>
          {panel}
        </MobileSourceSheet>
      )}
    </div>
  );
}
