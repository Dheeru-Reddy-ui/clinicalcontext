"use client";

import { Loader2, Scale } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { AnswerProse } from "@/components/answer/answer-prose";
import { ContradictionView } from "@/components/answer/contradiction-view";
import { ConfidenceBadge, GradeBadge } from "@/components/clinical/badges";
import { Button } from "@/components/ui/button";
import { apiUrl } from "@/lib/api";
import type { Citation, Confidence, Contradiction, EvidenceGrade } from "@/lib/domain";
import type { Schemas } from "@/lib/domain";
import { yearOf } from "@/lib/text";
import { cn } from "@/lib/utils";

type DemoQuestion = Schemas["DemoQuestion"];
type DemoAnswer = Schemas["DemoAnswerOut"];

/**
 * The marketing page's live demo (Phase 13): pick one of a few questions and
 * the real pipeline answers it, as the real tenant "public-demo", with no
 * login. Everything shown is what the API returned — the citations, the
 * confidence and grade, and the contradiction panel when the sources
 * disagree. The endpoint is rate-limited per visitor; when the limit is hit
 * the page says so.
 */
/**
 * Waking a sleeping API. On a free tier the instance stops after fifteen
 * quiet minutes and takes up to two minutes to start again; the visit that
 * finds it asleep is the one that wakes it. The server render gives up
 * quickly (app/page.tsx) and hands the panel an empty list; the panel then
 * polls for the questions itself and says what it is waiting for, instead
 * of the page hanging or claiming an outage. Give up after WAKE_DEADLINE_MS.
 */
const WAKE_POLL_MS = 5_000;
const WAKE_ATTEMPT_TIMEOUT_MS = 25_000;
const WAKE_DEADLINE_MS = 4 * 60_000;
// A single answer normally takes about a second; past this the wait is the
// instance starting, and the status line says so.
const SLOW_ANSWER_MS = 8_000;

export function DemoPanel({
  questions: initial,
  labelledBy,
  className,
}: {
  questions: DemoQuestion[];
  /** The id of a heading outside the panel that names it; the panel then shows none of its own. */
  labelledBy?: string;
  className?: string;
}) {
  const [questions, setQuestions] = useState<DemoQuestion[]>(initial);
  const [picked, setPicked] = useState<DemoQuestion | null>(initial[0] ?? null);
  const [answer, setAnswer] = useState<DemoAnswer | null>(null);
  const [busy, setBusy] = useState(false);
  const [slow, setSlow] = useState(false);
  const [waking, setWaking] = useState(initial.length === 0);
  const [error, setError] = useState<string | null>(null);
  const [activeMarker, setActiveMarker] = useState<number | null>(null);
  const sourcesRef = useRef<HTMLOListElement>(null);

  const run = useCallback(async (q: DemoQuestion) => {
    setBusy(true);
    setSlow(false);
    setError(null);
    setAnswer(null);
    setActiveMarker(null);
    const slowTimer = setTimeout(() => setSlow(true), SLOW_ANSWER_MS);
    try {
      const response = await fetch(apiUrl("/api/public/demo"), {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ question_id: q.id }),
      });
      if (response.status === 429) {
        const retry = response.headers.get("Retry-After");
        setError(`The demo is rate-limited — try again in ${retry ?? "a few"} seconds.`);
        return;
      }
      if (!response.ok) {
        setError(`The demo could not answer (HTTP ${response.status}).`);
        return;
      }
      setAnswer((await response.json()) as DemoAnswer);
    } catch {
      setError("The API is not reachable from this page.");
    } finally {
      clearTimeout(slowTimer);
      setSlow(false);
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    // The first question runs on arrival so the page opens on a real answer.
    if (picked && !answer && !busy && !error) void run(picked);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // No questions from the server: the API was asleep. Poll until it answers.
    if (!waking) return;
    let cancelled = false;
    const started = Date.now();
    const attempt = async (): Promise<void> => {
      if (cancelled) return;
      try {
        const response = await fetch(apiUrl("/api/public/demo/questions"), {
          headers: { Accept: "application/json" },
          signal: AbortSignal.timeout(WAKE_ATTEMPT_TIMEOUT_MS),
        });
        if (response.ok) {
          const loaded = ((await response.json()) as Schemas["DemoQuestionsOut"]).questions;
          if (cancelled) return;
          setQuestions(loaded);
          setWaking(false);
          const first = loaded[0] ?? null;
          setPicked(first);
          if (first) void run(first);
          return;
        }
      } catch {
        // Not up yet, or this attempt timed out — try again below.
      }
      if (cancelled) return;
      if (Date.now() - started > WAKE_DEADLINE_MS) {
        setWaking(false);
        setError("The API did not wake up in time. Reload the page to try again.");
        return;
      }
      setTimeout(() => void attempt(), WAKE_POLL_MS);
    };
    void attempt();
    return () => {
      cancelled = true;
    };
  }, [waking, run]);

  const citations: Citation[] = (answer?.citations ?? []).map((c) => ({
    marker: Number(c.marker),
    chunk_id: "",
    document_id: "",
    title: (c.title as string | null) ?? null,
    section: (c.section as string | null) ?? null,
    publication_date: (c.publication_date as string | null) ?? null,
    evidence_grade: (c.evidence_grade as EvidenceGrade | null) ?? null,
    study_type: (c.study_type as string | null) ?? null,
    journal: (c.journal as string | null) ?? null,
    pmid: (c.pmid as string | null) ?? null,
    doi: (c.doi as string | null) ?? null,
    url: (c.url as string | null) ?? null,
    passage: (c.passage as string | null) ?? "",
  }));
  const known = new Set(citations.map((c) => c.marker));
  const contradiction = (answer?.contradiction ?? null) as Contradiction | null;
  const onCite = (marker: number) => {
    setActiveMarker(marker);
    sourcesRef.current?.querySelector(`[data-marker="${marker}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };

  return (
    <section
      className={cn("w-full space-y-4 rounded-lg border bg-card p-4 md:p-5", className)}
      aria-labelledby={labelledBy ?? "demo-heading"}
      data-testid="demo-panel"
    >
      {!labelledBy && (
        <header className="space-y-1">
          <h2 id="demo-heading" className="text-base font-semibold">
            Try it — no login
          </h2>
          <p className="text-sm text-muted-foreground">
            These questions run through the real pipeline as a public tenant. Pick one that shows a contradiction: the
            product never buries a disagreement between sources.
          </p>
        </header>
      )}

      <div className="flex flex-wrap gap-2" role="group" aria-label="Demo questions">
        {questions.map((q) => (
          <Button
            key={q.id}
            type="button"
            size="sm"
            variant={picked?.id === q.id ? "default" : "outline"}
            disabled={busy}
            onClick={() => {
              setPicked(q);
              void run(q);
            }}
            title={q.why}
          >
            {q.shows === "contradiction" && <Scale className="size-3.5" aria-hidden />}
            {q.question}
          </Button>
        ))}
      </div>

      {waking && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status" data-testid="demo-waking">
          <Loader2 className="size-4 animate-spin" aria-hidden /> Waking the API — it sleeps after fifteen quiet minutes
          and takes up to two minutes to start. Your visit is what wakes it.
        </p>
      )}
      {busy && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
          <Loader2 className="size-4 animate-spin" aria-hidden />{" "}
          {slow
            ? "Still working — the API is starting up, which takes up to two minutes on the free tier. The answer follows."
            : "Retrieving, grading, and checking the sources against each other…"}
        </p>
      )}
      {error && (
        <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground" role="alert">
          {error}
        </p>
      )}

      {answer && !busy && (
        <div className="space-y-4" data-testid="demo-answer">
          {answer.blocked ? (
            <p className="rounded-md border p-3 text-sm">
              A guardrail stopped this question: {answer.blocked}. That should not happen for a literature question — it is
              a regression, and it is shown rather than hidden.
            </p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                {answer.confidence && <ConfidenceBadge level={answer.confidence as Confidence} />}
                <GradeBadge grade={(answer.evidence_grade as EvidenceGrade | null) ?? null} showWord />
                <span>
                  {answer.abstained ? "abstained" : `${citations.length} sources`} · {answer.generation_mode} ·{" "}
                  {answer.cached ? "served from the semantic cache" : `${(answer.latency_ms / 1000).toFixed(1)} s`}
                </span>
              </div>

              {contradiction && contradiction.detected && (
                <ContradictionView contradiction={contradiction} citations={citations} onCite={onCite} />
              )}

              <AnswerProse text={answer.answer} known={known} activeMarker={activeMarker} onCite={onCite} className="text-sm" />

              {citations.length > 0 && (
                <ol ref={sourcesRef} className="space-y-1.5 border-t pt-3" aria-label="Sources">
                  {citations.map((c) => (
                    <li
                      key={c.marker}
                      data-marker={c.marker}
                      className={cn("flex items-start gap-2 rounded-sm px-1 py-0.5 text-xs", activeMarker === c.marker && "bg-accent")}
                    >
                      <span className="cite-chip mt-0.5 shrink-0">{c.marker}</span>
                      <span className="min-w-0">
                        <span className="text-foreground/90">{c.title ?? "Untitled source"}</span>{" "}
                        <span className="text-muted-foreground">
                          {[c.journal, yearOf(c.publication_date), c.study_type].filter(Boolean).join(" · ")}
                          {c.evidence_grade && ` · grade ${c.evidence_grade}`}
                        </span>
                        {c.pmid && (
                          <a
                            href={c.url ?? `https://pubmed.ncbi.nlm.nih.gov/${c.pmid}/`}
                            target="_blank"
                            rel="noreferrer"
                            className="ml-1 underline underline-offset-2"
                          >
                            PMID {c.pmid}
                          </a>
                        )}
                        {activeMarker === c.marker && c.passage && (
                          <blockquote className="mt-1 border-l-2 pl-2 text-muted-foreground">{c.passage}</blockquote>
                        )}
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
