"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BookOpenCheck, ClipboardList, GraduationCap, ListChecks, Loader2, Sparkles, TrendingUp } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { ChatPanel } from "@/components/chat/chat-panel";
import { PROGRESS_KEY, ProgressView } from "@/components/learn/tutor/progress-view";
import { QuizRunner } from "@/components/learn/tutor/quiz-runner";
import { Segmented } from "@/components/settings/ui";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useSpecialties, useToken } from "@/hooks/use-api";
import { useChat } from "@/hooks/use-chat";
import { usePreferences } from "@/hooks/use-preferences";
import { ApiError } from "@/lib/api";
import { api } from "@/lib/api-client";
import type { Schemas } from "@/lib/domain";
import { cn } from "@/lib/utils";

type Mode = "lesson" | "quiz" | "case" | "progress";
type Level = "mbbs" | "pg";

const MODES: ReadonlyArray<{ value: Mode; label: string; icon: typeof BookOpenCheck; hint: string }> = [
  { value: "lesson", label: "Lesson", icon: BookOpenCheck, hint: "Taught, then questioned" },
  { value: "quiz", label: "Quiz", icon: ListChecks, hint: "Exam-style questions" },
  { value: "case", label: "Case", icon: ClipboardList, hint: "A patient, step by step" },
  { value: "progress", label: "Progress", icon: TrendingUp, hint: "How you are doing" },
];
const COUNTS = [
  { value: "3", label: "3" },
  { value: "5", label: "5" },
  { value: "10", label: "10" },
] as const;
const LEVELS = [
  { value: "mbbs", label: "MBBS" },
  { value: "pg", label: "PG" },
] as const;

function isMode(value: string | null): value is Mode {
  return value === "lesson" || value === "quiz" || value === "case" || value === "progress";
}

/**
 * The AI medical tutor: a lesson that teaches a topic and then questions the
 * learner, exam-style quizzes and staged clinical cases written from the
 * evidence and checked before they are shown, and a practice record.
 */
export function Tutor() {
  const token = useToken();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const specialties = useSpecialties();
  const { preferences } = usePreferences();

  const [mode, setModeState] = useState<Mode>(isMode(params.get("mode")) ? (params.get("mode") as Mode) : "lesson");
  const [topic, setTopic] = useState(params.get("topic") ?? "");
  const [specialty, setSpecialty] = useState<string>(params.get("specialty") ?? "");
  const [level, setLevel] = useState<Level>(
    params.get("level") === "pg" || (params.get("level") !== "mbbs" && preferences.learn_depth === "pg") ? "pg" : "mbbs",
  );
  const [count, setCount] = useState<"3" | "5" | "10">("5");
  const [quiz, setQuiz] = useState<Schemas["QuizOut"] | null>(null);
  const autoStarted = useRef(false);

  const chosen = specialties.data?.find((s) => s.slug === specialty) ?? null;
  const suggestions = useMemo(() => {
    const pool = chosen ? [chosen] : (specialties.data ?? []);
    // Several subjects share a topic ("Sepsis"): each is suggested once.
    return Array.from(new Set(pool.flatMap((s) => s.topics))).slice(0, 400);
  }, [chosen, specialties.data]);

  const chat = useChat({ kind: "tutor", audience: "student", specialty: specialty || null, level });

  const setMode = (next: Mode) => {
    setModeState(next);
    const search = new URLSearchParams(params.toString());
    search.set("mode", next);
    router.replace(`${pathname}?${search.toString()}`, { scroll: false });
  };

  const write = useMutation({
    mutationFn: (kind: "quiz" | "case") =>
      api.tutorQuiz(token, {
        topic: topic.trim(),
        specialty: specialty || null,
        level,
        count: Number(count),
        mode: kind,
      }),
    onSuccess: (result) => setQuiz(result),
  });

  const start = (event?: FormEvent) => {
    event?.preventDefault();
    const what = topic.trim();
    if (mode === "lesson") {
      if (!what) return;
      chat.reset();
      void chat.send(
        `Teach me ${what}${chosen ? ` (${chosen.name})` : ""} at ${level === "pg" ? "postgraduate" : "MBBS"} level.`,
      );
      return;
    }
    if (mode === "quiz" || mode === "case") {
      if (what.length < 2) return;
      setQuiz(null);
      write.mutate(mode);
    }
  };

  // A link from a subject's topic ("/app/learn/tutor?mode=quiz&topic=…&start=1") starts at once.
  useEffect(() => {
    if (autoStarted.current || !token || params.get("start") !== "1" || !topic.trim()) return;
    autoStarted.current = true;
    start();
    // Once, on arrival.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const practise = (next: Schemas["TopicProgressOut"]) => {
    setTopic(next.topic);
    if (next.specialty) setSpecialty(next.specialty);
    setQuiz(null);
    setMode("quiz");
  };

  const unavailable = write.error instanceof ApiError ? write.error : null;

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-5 p-4 md:p-6" data-testid="tutor">
      <header>
        <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
          <GraduationCap className="size-5 text-primary" aria-hidden /> AI Medical Tutor
        </h1>
        <p className="text-sm text-muted-foreground">
          Learn a topic, test yourself with exam-style questions, or work through a patient — every fact taught from
          guidelines and research, with the source one tap away.
        </p>
      </header>

      <div role="tablist" aria-label="Tutor" className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {MODES.map(({ value, label, icon: Icon, hint }) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={mode === value}
            onClick={() => setMode(value)}
            className={cn(
              "flex items-center gap-2.5 rounded-xl border px-3 py-2.5 text-left transition-colors",
              mode === value ? "border-primary bg-primary/5" : "hover:bg-muted/40",
            )}
            data-testid={`tutor-mode-${value}`}
          >
            <Icon className={cn("size-4 shrink-0", mode === value ? "text-primary" : "text-muted-foreground")} aria-hidden />
            <span className="min-w-0">
              <span className="block text-sm font-medium">{label}</span>
              <span className="block truncate text-xs text-muted-foreground">{hint}</span>
            </span>
          </button>
        ))}
      </div>

      {mode !== "progress" && (
        <form onSubmit={start} className="grid gap-3 rounded-xl border bg-card p-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,14rem)]" data-testid="tutor-setup">
          <div className="flex flex-col gap-1.5 sm:col-span-2">
            <Label htmlFor="tutor-topic">Topic</Label>
            <Input
              id="tutor-topic"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              list="tutor-topics"
              maxLength={120}
              placeholder="e.g. heart failure, diabetic ketoacidosis, beta-lactam antibiotics"
              data-testid="tutor-topic"
            />
            <datalist id="tutor-topics">
              {suggestions.map((t) => (
                <option key={t} value={t} />
              ))}
            </datalist>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="tutor-specialty">Subject (optional)</Label>
            <select
              id="tutor-specialty"
              value={specialty}
              onChange={(e) => setSpecialty(e.target.value)}
              className="h-9 w-full min-w-0 rounded-md border bg-transparent px-2.5 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 dark:bg-input/30"
              data-testid="tutor-specialty"
            >
              <option value="">Any subject</option>
              {(specialties.data ?? []).map((s) => (
                <option key={s.slug} value={s.slug}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1.5">
              <span className="text-sm font-medium">Level</span>
              <Segmented label="Level" value={level} choices={LEVELS} onChange={setLevel} testId="tutor-level" />
            </div>
            {mode !== "lesson" && (
              <div className="flex flex-col gap-1.5">
                <span className="text-sm font-medium">Questions</span>
                <Segmented label="Questions" value={count} choices={COUNTS} onChange={setCount} testId="tutor-count" />
              </div>
            )}
          </div>
          <div className="flex items-end sm:col-span-2">
            <Button
              type="submit"
              disabled={!token || topic.trim().length < 2 || write.isPending || chat.busy}
              data-testid="tutor-start"
            >
              {write.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />}
              {mode === "lesson" ? "Start the lesson" : mode === "quiz" ? "Write my quiz" : "Start a case"}
            </Button>
          </div>
        </form>
      )}

      {mode === "lesson" && (
        <section className="flex min-h-[60vh] flex-col rounded-xl border bg-card" aria-label="Lesson">
          <ChatPanel
            chat={chat}
            voice
            showAudience={false}
            placeholder="Answer the tutor's question, or ask your own…"
            empty={
              <div className="flex flex-col items-center gap-3 px-4 py-10 text-center">
                <BookOpenCheck className="size-8 text-primary" aria-hidden />
                <h2 className="text-lg font-semibold tracking-tight">How a lesson works</h2>
                <p className="max-w-lg text-sm text-muted-foreground">
                  Name a topic above. The tutor teaches it in short sections from guidelines and research, then asks
                  you a question. Answer in your own words — it tells you what you got right, fills the gaps, and asks
                  the next one, a little harder.
                </p>
              </div>
            }
          />
        </section>
      )}

      {(mode === "quiz" || mode === "case") && (
        <section aria-label={mode === "quiz" ? "Quiz" : "Case"}>
          {write.isPending && (
            <div className="flex flex-col items-center gap-2 rounded-xl border bg-card p-8 text-center" role="status" data-testid="tutor-writing">
              <Loader2 className="size-6 animate-spin text-primary" aria-hidden />
              <p className="font-medium">{mode === "case" ? "Writing your case…" : "Writing your questions…"}</p>
              <p className="max-w-md text-sm text-muted-foreground">
                Finding the evidence on {topic.trim()}, then checking every explanation against its source before it
                reaches you.
              </p>
            </div>
          )}
          {!write.isPending && unavailable && (
            <div className="rounded-xl border bg-card p-5 text-sm" role="alert" data-testid="tutor-unavailable">
              <p className="font-medium">{unavailable.message}</p>
              {mode === "case" && (
                <Button className="mt-3" variant="outline" onClick={() => setMode("quiz")}>
                  Take a quiz instead
                </Button>
              )}
            </div>
          )}
          {!write.isPending && !unavailable && write.error && (
            <p className="rounded-xl border bg-card p-5 text-sm" role="alert">
              Couldn’t write the questions. Check your connection and try again.
            </p>
          )}
          {!write.isPending && quiz && quiz.mode === mode && (
            <QuizRunner
              key={`${quiz.topic}-${quiz.questions[0]?.stem ?? ""}`}
              quiz={quiz}
              token={token}
              onAgain={() => {
                void queryClient.invalidateQueries({ queryKey: PROGRESS_KEY });
                setQuiz(null);
                write.mutate(mode);
              }}
              onSwitch={() => {
                setQuiz(null);
                setMode(mode === "case" ? "quiz" : "case");
              }}
              onProgress={() => {
                void queryClient.invalidateQueries({ queryKey: PROGRESS_KEY });
                setMode("progress");
              }}
            />
          )}
          {!write.isPending && !quiz && !write.error && (
            <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
              {mode === "quiz"
                ? "Name a topic above and the tutor writes single-best-answer questions from the evidence — each explanation checked against its source."
                : "Name a topic above for a patient to work through: the diagnosis, the test that confirms it, the first steps in management."}
            </p>
          )}
        </section>
      )}

      {mode === "progress" && <ProgressView token={token} onPractise={practise} />}
    </div>
  );
}
