"use client";

import { ChevronDown, ChevronUp, MessagesSquare, Mic, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import { AnswerActions, SaveToBinder } from "@/components/answer/answer-actions";
import { AnswerProse } from "@/components/answer/answer-prose";
import { AnswerView } from "@/components/answer/answer-view";
import { PhiBlockedView, RedFlagBanner, ScopeBlockedView } from "@/components/answer/guardrail-view";
import { QueryComposer } from "@/components/ask/query-composer";
import { ReasoningSteps } from "@/components/ask/reasoning-steps";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useAsk } from "@/hooks/use-ask";
import type { QueryRequest } from "@/lib/stream";

const EXAMPLES = [
  "First-line anticoagulation in non-valvular atrial fibrillation?",
  "How is type 2 diabetes managed with metformin?",
  "Do statins reduce cardiovascular events in primary prevention?",
  "Aspirin for primary prevention of cardiovascular disease in older adults?",
];

/** Reads `?q=` (e.g. "Ask again" from history) and `?session=` (a thread handed
 *  over from voice mode) once — isolated so the page itself never suspends on
 *  search params. */
function PrefillFromUrl({
  onPrefill,
  onSession,
}: {
  onPrefill: (q: string) => void;
  onSession: (id: string) => void;
}) {
  const params = useSearchParams();
  const q = params.get("q");
  const session = params.get("session");
  useEffect(() => {
    if (q) onPrefill(q);
  }, [q, onPrefill]);
  useEffect(() => {
    if (session) onSession(session);
  }, [session, onSession]);
  return null;
}

export default function AskPage() {
  const { state, ask, cancel, reset } = useAsk();
  const [stepsOpen, setStepsOpen] = useState(true);
  const [composerKey, setComposerKey] = useState(0);
  const [prefill, setPrefill] = useState("");
  // A thread handed over from voice mode: follow-ups join it (context intact).
  const [handoffSession, setHandoffSession] = useState<string | null>(null);
  // The passage a reader chose to keep, from the citation panel.
  const [passageToSave, setPassageToSave] = useState<string | null>(null);
  const streaming = state.phase === "streaming";
  const threadId = state.sessionId ?? handoffSession;

  // Collapse the reasoning once the answer is in; keep it one click away.
  useEffect(() => {
    if (state.phase === "done") setStepsOpen(false);
    if (state.phase === "streaming") setStepsOpen(true);
  }, [state.phase]);

  const submit = useCallback(
    (request: QueryRequest) => {
      // Follow-ups stay in the same session so the backend can resolve
      // "what about in pregnancy?" against the previous turn.
      void ask({ ...request, session_id: request.session_id ?? threadId });
    },
    [ask, threadId],
  );

  const startOver = useCallback(() => {
    reset();
    setPrefill("");
    setComposerKey((k) => k + 1);
  }, [reset]);

  const retryWith = useCallback(
    (query: string) => {
      setPrefill(query);
      setComposerKey((k) => k + 1);
      window.scrollTo({ top: 0, behavior: "smooth" });
    },
    [],
  );

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4 p-4 md:p-6">
      <Suspense fallback={null}>
        <PrefillFromUrl onPrefill={retryWith} onSession={setHandoffSession} />
      </Suspense>
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Ask</h1>
          <p className="text-sm text-muted-foreground">
            Every claim is cited. Click any <span className="cite-chip mx-0.5" aria-hidden>n</span> to read the source.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            nativeButton={false}
            render={<Link href={threadId ? `/app/voice?session=${threadId}` : "/app/voice"} />}
            data-testid="ask-to-voice"
          >
            <Mic /> Voice
          </Button>
          {threadId && (state.phase !== "idle" || handoffSession) && (
            <>
              <Button
                variant="ghost"
                size="sm"
                nativeButton={false}
                render={<Link href={`/app/sessions/${threadId}`} />}
              >
                <MessagesSquare /> This thread
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setHandoffSession(null);
                  startOver();
                }}
              >
                <RotateCcw /> New thread
              </Button>
            </>
          )}
        </div>
      </div>

      {handoffSession && state.phase === "idle" && (
        <p className="text-sm text-muted-foreground">
          Continuing the voice thread here — follow-ups resolve against what was said.
        </p>
      )}
      <QueryComposer
        key={composerKey}
        busy={streaming}
        onSubmit={submit}
        onCancel={cancel}
        sessionId={threadId}
        initialQuery={prefill}
        compact={state.phase !== "idle"}
      />

      {state.phase === "idle" && (
        <section className="rounded-lg border border-dashed p-5" aria-label="How to ask">
          <p className="text-sm font-medium">Questions this corpus answers well</p>
          <p className="mb-3 text-sm text-muted-foreground">
            Name the population, the intervention, and the outcome. The system will abstain rather than
            guess when the literature is thin — that is a feature.
          </p>
          <ul className="grid gap-2 sm:grid-cols-2">
            {EXAMPLES.map((q) => (
              <li key={q}>
                <button
                  type="button"
                  onClick={() => retryWith(q)}
                  className="w-full rounded-md border bg-card px-3 py-2 text-left text-sm hover:bg-accent focus-visible:bg-accent"
                >
                  {q}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {state.phase !== "idle" && (
        <div className="space-y-4">
          {state.contextualizedQuery && (
            <p className="text-sm text-muted-foreground">
              Interpreted as: <span className="text-foreground">{state.contextualizedQuery}</span>
            </p>
          )}

          {(state.steps.length > 0 || streaming) && (
            <section className="rounded-lg border bg-card">
              <button
                type="button"
                onClick={() => setStepsOpen((o) => !o)}
                aria-expanded={stepsOpen}
                className="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm font-medium"
              >
                <span>
                  {streaming ? "Reasoning…" : `Reasoning · ${state.steps.length} steps`}
                  {state.finishedAt && state.startedAt && (
                    <span className="ml-2 font-mono text-xs font-normal text-muted-foreground">
                      {((state.finishedAt - state.startedAt) / 1000).toFixed(1)} s
                    </span>
                  )}
                </span>
                {stepsOpen ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
              </button>
              {stepsOpen && (
                <div className="border-t px-4 py-3">
                  <ReasoningSteps steps={state.steps} live={streaming} />
                </div>
              )}
            </section>
          )}

          {state.phase === "blocked" && state.blocked && (
            state.blocked.by === "phi" ? (
              <PhiBlockedView onReset={startOver} />
            ) : (
              <ScopeBlockedView message={state.blocked.message} onReset={startOver} />
            )
          )}

          {state.phase === "error" && state.error && (
            <Alert variant="destructive">
              <AlertTitle>The request could not be completed</AlertTitle>
              <AlertDescription>
                {state.error.message}
                {state.error.retryAfter !== null && ` Try again in ${state.error.retryAfter}s.`}
                <div className="mt-2">
                  <Button size="sm" variant="outline" onClick={() => retryWith(state.query)}>
                    Try again
                  </Button>
                </div>
              </AlertDescription>
            </Alert>
          )}

          {streaming && state.escalationBanner && <RedFlagBanner message={state.escalationBanner} />}

          {streaming && state.partialText && (
            <div className="rounded-lg border bg-card p-5">
              <AnswerProse text={state.partialText} streaming />
            </div>
          )}

          {state.phase === "done" && state.answer && (
            <>
              <AnswerView
                answer={state.answer}
                escalationBanner={state.escalationBanner}
                actions={<AnswerActions answer={state.answer} />}
                onRetry={retryWith}
                onSavePassage={(citation) => setPassageToSave(citation.chunk_id)}
              />
              {passageToSave && (
                <SaveToBinder
                  chunkId={passageToSave}
                  open
                  onOpenChange={(next) => !next && setPassageToSave(null)}
                />
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

