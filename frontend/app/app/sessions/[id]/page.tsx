"use client";

import { ArrowLeft, ChevronDown, ChevronRight } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { AnswerActions } from "@/components/answer/answer-actions";
import { fromQueryDetail } from "@/components/answer/answer-model";
import { AnswerProse } from "@/components/answer/answer-prose";
import { AnswerView } from "@/components/answer/answer-view";
import { PhiBlockedView, ScopeBlockedView } from "@/components/answer/guardrail-view";
import { QueryComposer } from "@/components/ask/query-composer";
import { ReasoningSteps } from "@/components/ask/reasoning-steps";
import { ConfidenceBadge, StatusPill } from "@/components/clinical/badges";
import { ErrorState, PageBody } from "@/components/clinical/page";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { SessionVoiceTurns } from "@/components/voice/session-voice-turns";
import { useQueryDetail, useSession } from "@/hooks/use-api";
import { useAsk } from "@/hooks/use-ask";
import type { Confidence, Schemas } from "@/lib/domain";
import { formatDate } from "@/lib/text";
import { cn } from "@/lib/utils";

export default function SessionPage() {
  const { id } = useParams<{ id: string }>();
  const session = useSession(id);
  const { state, ask, cancel, reset } = useAsk();
  const streaming = state.phase === "streaming";
  const endRef = useRef<HTMLDivElement>(null);

  // A finished turn is now in the server's thread; drop the live copy.
  useEffect(() => {
    if (state.phase === "done" && session.data?.turns.some((t) => t.query_id === state.queryId)) {
      reset();
    }
  }, [state.phase, state.queryId, session.data, reset]);

  useEffect(() => {
    if (streaming) endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [streaming, state.partialText]);

  if (session.isPending) {
    return (
      <PageBody>
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </PageBody>
    );
  }
  if (session.isError) {
    return (
      <PageBody>
        <ErrorState error={session.error} onRetry={() => void session.refetch()} />
      </PageBody>
    );
  }

  const { session: meta, turns } = session.data;

  return (
    <PageBody>
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" nativeButton={false} render={<Link href="/app/sessions" />}>
          <ArrowLeft /> Sessions
        </Button>
        <span className="ml-auto font-mono text-[11px] text-muted-foreground">
          {turns.length} turn{turns.length === 1 ? "" : "s"} · started {formatDate(meta.created_at)}
        </span>
      </div>
      <h1 className="text-lg font-semibold tracking-tight">{meta.title ?? "Untitled session"}</h1>

      <ol className="space-y-3" aria-label="Conversation">
        {turns.map((turn, i) => (
          <Turn key={turn.query_id} turn={turn} defaultOpen={i === turns.length - 1 && state.phase === "idle"} />
        ))}

        {state.phase !== "idle" && (
          <li className="space-y-3 rounded-lg border bg-card p-4" aria-live="polite">
            <p className="text-sm font-medium">{state.query}</p>
            {state.contextualizedQuery && (
              <p className="text-xs text-muted-foreground">Interpreted as: {state.contextualizedQuery}</p>
            )}
            <ReasoningSteps steps={state.steps} live={streaming} />
            {streaming && state.partialText && <AnswerProse text={state.partialText} streaming />}
            {state.phase === "blocked" && state.blocked && (
              state.blocked.by === "phi" ? <PhiBlockedView onReset={reset} /> : <ScopeBlockedView message={state.blocked.message} onReset={reset} />
            )}
            {state.phase === "error" && state.error && <ErrorState error={new Error(state.error.message)} onRetry={reset} />}
            {state.phase === "done" && state.answer && (
              <AnswerView answer={state.answer} escalationBanner={state.escalationBanner} actions={<AnswerActions answer={state.answer} />} />
            )}
          </li>
        )}
      </ol>
      <div ref={endRef} />

      <SessionVoiceTurns sessionId={id} />

      <div className="sticky bottom-4">
        <QueryComposer
          busy={streaming}
          sessionId={id}
          onSubmit={(request) => void ask({ ...request, session_id: id })}
          onCancel={cancel}
          compact
          autoFocus={false}
        />
      </div>
    </PageBody>
  );
}

function Turn({ turn, defaultOpen }: { turn: Schemas["SessionTurn"]; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const detail = useQueryDetail(open ? turn.query_id : null);
  const answer = useMemo(() => (detail.data ? fromQueryDetail(detail.data) : null), [detail.data]);
  const blocked = turn.status === "blocked";

  return (
    <li className="rounded-lg border bg-card">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-start gap-3 px-4 py-3 text-left"
      >
        <span className="mt-0.5 text-muted-foreground">{open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}</span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium">{turn.raw_query}</span>
          {turn.contextualized_query && (
            <span className="block text-xs text-muted-foreground">Interpreted as: {turn.contextualized_query}</span>
          )}
          {!open && turn.answer && (
            <span className="mt-1 line-clamp-2 block text-sm text-muted-foreground">{turn.answer.replace(/\[\d+\]/g, "")}</span>
          )}
        </span>
        <span className="flex shrink-0 items-center gap-1.5">
          {blocked && <StatusPill kind="blocked" />}
          {turn.confidence && <ConfidenceBadge level={turn.confidence as Confidence} compact />}
          {turn.abstained && <StatusPill kind="abstained" />}
          <time dateTime={turn.created_at} className="font-mono text-[11px] text-muted-foreground">
            {formatDate(turn.created_at, { hour: "2-digit", minute: "2-digit" })}
          </time>
        </span>
      </button>
      {open && (
        <div className={cn("border-t p-4")}>
          {detail.isPending ? (
            <Skeleton className="h-32 w-full" />
          ) : detail.isError ? (
            <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
          ) : answer ? (
            <AnswerView answer={answer} actions={<AnswerActions answer={answer} />} />
          ) : (
            <p className="text-sm text-muted-foreground">No answer was produced for this turn.</p>
          )}
        </div>
      )}
    </li>
  );
}
