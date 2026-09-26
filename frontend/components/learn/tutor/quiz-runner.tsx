"use client";

import { ArrowRight, CheckCircle2, ClipboardList, Info, RotateCcw, Trophy, XCircle } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { MarkedText, SourceList } from "@/components/learn/marked-text";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api-client";
import type { Schemas } from "@/lib/domain";
import { cn } from "@/lib/utils";

type Quiz = Schemas["QuizOut"];

const LETTERS = ["A", "B", "C", "D", "E"];

/**
 * One quiz or clinical case, a question at a time: choose, see whether it
 * was right and why — with the source it comes from — then the next. Every
 * answer goes to the learner's own practice record.
 */
export function QuizRunner({
  quiz,
  token,
  onAgain,
  onSwitch,
  onProgress,
}: {
  quiz: Quiz;
  token: string;
  onAgain: () => void;
  onSwitch: () => void;
  onProgress: () => void;
}) {
  const [index, setIndex] = useState(0);
  const [chosen, setChosen] = useState<Array<number | null>>(() => quiz.questions.map(() => null));
  const [finished, setFinished] = useState(false);
  const [source, setSource] = useState<number | null>(null);
  const question = quiz.questions[index];
  const answered = chosen[index] ?? null;
  const score = chosen.filter((c, i) => c !== null && c === quiz.questions[i]?.answer).length;
  const isCase = quiz.mode === "case";

  const choose = useCallback(
    (option: number) => {
      if (!question || answered !== null) return;
      setChosen((all) => all.map((c, i) => (i === index ? option : c)));
      const correct = option === question.answer;
      // The learner's own record; a failed save never interrupts the quiz.
      void api
        .recordAttempt(token, {
          mode: quiz.mode,
          level: quiz.level,
          specialty: quiz.specialty ?? null,
          topic: quiz.topic,
          question: isCase && question.stage ? `${question.stage}: ${question.stem}` : question.stem,
          chosen: question.options[option] ?? "",
          answer: question.options[question.answer] ?? "",
          explanation: question.explanation.slice(0, 4000),
          correct,
        })
        .catch(() => undefined);
    },
    [answered, index, isCase, question, quiz, token],
  );

  const next = useCallback(() => {
    setSource(null);
    if (index + 1 < quiz.questions.length) setIndex(index + 1);
    else setFinished(true);
  }, [index, quiz.questions.length]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (finished || !question) return;
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      const key = event.key.toUpperCase();
      const byLetter = LETTERS.indexOf(key);
      const byNumber = Number(key) - 1;
      const pick = byLetter >= 0 ? byLetter : byNumber >= 0 && byNumber < 5 ? byNumber : -1;
      if (answered === null && pick >= 0 && pick < question.options.length) {
        event.preventDefault();
        choose(pick);
      } else if (answered !== null && event.key === "Enter") {
        event.preventDefault();
        next();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [answered, choose, finished, next, question]);

  const sources = useMemo(() => quiz.sources, [quiz.sources]);

  if (finished) {
    return (
      <Results
        quiz={quiz}
        chosen={chosen}
        score={score}
        onAgain={onAgain}
        onSwitch={onSwitch}
        onProgress={onProgress}
      />
    );
  }
  if (!question) return null;

  return (
    <div className="flex flex-col gap-4" data-testid="quiz-runner" data-mode={quiz.mode}>
      {quiz.notices.map((notice) => (
        <p key={notice} className="flex gap-2 rounded-lg border bg-muted/40 px-3 py-2 text-xs text-muted-foreground" role="note">
          <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden /> {notice}
        </p>
      ))}

      {isCase && quiz.case && (
        <section className="rounded-xl border bg-card p-4" aria-labelledby="case-heading">
          <h3 id="case-heading" className="mb-1 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            <ClipboardList className="size-3.5" aria-hidden /> The case
          </h3>
          <p className="text-sm leading-6" data-testid="case-vignette">
            {quiz.case}
          </p>
        </section>
      )}

      <section className="rounded-xl border bg-card p-4 sm:p-5" aria-live="polite">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span className="font-medium" data-testid="quiz-position">
            Question {index + 1} of {quiz.questions.length}
          </span>
          {question.stage && (
            <span className="rounded-full bg-primary/10 px-2 py-0.5 font-medium text-primary">{question.stage}</span>
          )}
          <span className="ml-auto flex gap-1" aria-hidden>
            {quiz.questions.map((q, i) => {
              const c = chosen[i];
              return (
                <span
                  key={i}
                  className={cn(
                    "size-2 rounded-full",
                    c === null || c === undefined
                      ? i === index
                        ? "bg-primary"
                        : "bg-muted"
                      : c === q.answer
                        ? "bg-conf-high"
                        : "bg-conf-low",
                  )}
                />
              );
            })}
          </span>
        </div>
        <h2 className="text-base font-medium leading-7 text-pretty" data-testid="quiz-stem">
          {question.stem}
        </h2>
        <div role="group" aria-label="Options" className="mt-4 flex flex-col gap-2">
          {question.options.map((option, i) => {
            const isAnswer = i === question.answer;
            const isChosen = i === answered;
            const revealed = answered !== null;
            return (
              <button
                key={option}
                type="button"
                disabled={revealed}
                onClick={() => choose(i)}
                className={cn(
                  "flex items-start gap-3 rounded-lg border px-3 py-2.5 text-left text-sm transition-colors",
                  !revealed && "hover:border-primary/50 hover:bg-muted/40",
                  revealed && isAnswer && "border-conf-high bg-conf-high-bg text-conf-high-fg",
                  revealed && isChosen && !isAnswer && "border-conf-low bg-conf-low-bg text-conf-low-fg",
                  revealed && !isAnswer && !isChosen && "opacity-60",
                )}
                data-testid={`quiz-option-${i}`}
                data-correct={revealed ? String(isAnswer) : undefined}
              >
                <span className="mt-px inline-flex size-5 shrink-0 items-center justify-center rounded border text-[11px] font-semibold">
                  {LETTERS[i]}
                </span>
                <span className="min-w-0 flex-1">{option}</span>
                {revealed && isAnswer && <CheckCircle2 className="size-4 shrink-0" aria-label="Right answer" />}
                {revealed && isChosen && !isAnswer && <XCircle className="size-4 shrink-0" aria-label="Your answer" />}
              </button>
            );
          })}
        </div>

        {answered !== null && (
          <div className="mt-4 flex flex-col gap-3" data-testid="quiz-feedback">
            <p className={cn("text-sm font-medium", answered === question.answer ? "text-conf-high-fg" : "text-conf-low-fg")}>
              {answered === question.answer ? "Right." : `Not quite — the answer is ${LETTERS[question.answer]}.`}
            </p>
            <p className="text-sm leading-6 text-pretty">
              <MarkedText text={question.explanation} onMarker={setSource} />
            </p>
            {source !== null && (
              <SourceList sources={sources.filter((s) => s.marker === source)} active={source} />
            )}
            <div className="flex justify-end">
              <Button onClick={next} data-testid="quiz-next">
                {index + 1 < quiz.questions.length ? "Next question" : "See your score"} <ArrowRight />
              </Button>
            </div>
          </div>
        )}
      </section>

      <details className="rounded-xl border bg-card p-3 text-sm">
        <summary className="cursor-pointer select-none font-medium">
          The {sources.length} source{sources.length === 1 ? "" : "s"} these questions were written from
        </summary>
        <div className="mt-3">
          <SourceList sources={sources} active={source} />
        </div>
      </details>
    </div>
  );
}

function Results({
  quiz,
  chosen,
  score,
  onAgain,
  onSwitch,
  onProgress,
}: {
  quiz: Quiz;
  chosen: Array<number | null>;
  score: number;
  onAgain: () => void;
  onSwitch: () => void;
  onProgress: () => void;
}) {
  const total = quiz.questions.length;
  const percent = Math.round((score / Math.max(total, 1)) * 100);
  const message =
    percent === 100 ? "Every one right." : percent >= 70 ? "Well done." : percent >= 40 ? "Getting there." : "Worth another look.";
  return (
    <section className="flex flex-col gap-4" data-testid="quiz-results" aria-labelledby="results-heading">
      <div className="flex flex-col items-center gap-2 rounded-xl border bg-card p-6 text-center">
        <Trophy className="size-8 text-amber-500" aria-hidden />
        <h2 id="results-heading" className="text-xl font-semibold tracking-tight" data-testid="quiz-score">
          {score} of {total} right
        </h2>
        <p className="text-sm text-muted-foreground">
          {message} {quiz.mode === "case" ? "Case" : "Quiz"} on {quiz.topic}.
        </p>
        <div className="mt-2 flex flex-wrap justify-center gap-2">
          <Button onClick={onAgain} data-testid="quiz-again">
            <RotateCcw /> Another {quiz.mode === "case" ? "case" : "quiz"} on this topic
          </Button>
          <Button variant="outline" onClick={onSwitch}>
            {quiz.mode === "case" ? "Take a quiz instead" : "Try a clinical case"}
          </Button>
          <Button variant="ghost" onClick={onProgress}>
            See your progress
          </Button>
        </div>
      </div>
      <ol className="flex flex-col gap-2">
        {quiz.questions.map((q, i) => {
          const right = chosen[i] === q.answer;
          return (
            <li key={q.stem} className="rounded-lg border bg-card p-3 text-sm">
              <p className="flex items-start gap-2 font-medium">
                {right ? (
                  <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-conf-high" aria-label="Right" />
                ) : (
                  <XCircle className="mt-0.5 size-4 shrink-0 text-conf-low" aria-label="Wrong" />
                )}
                <span>{q.stem}</span>
              </p>
              {!right && (
                <p className="mt-1 pl-6 text-xs text-muted-foreground">
                  You chose {chosen[i] !== null && chosen[i] !== undefined ? `“${q.options[chosen[i]!]}”` : "nothing"}; the answer is “{q.options[q.answer]}”.
                </p>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
