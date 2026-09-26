"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Flame, RotateCcw, Target, TrendingUp, XCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api-client";
import type { Schemas } from "@/lib/domain";
import { cn } from "@/lib/utils";

type Topic = Schemas["TopicProgressOut"];

export const PROGRESS_KEY = ["learn", "tutor", "progress"] as const;

function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;
}

function ago(iso: string): string {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  return `${days} days ago`;
}

function Stat({ label, value, hint, icon: Icon }: { label: string; value: string; hint?: string; icon: typeof Target }) {
  return (
    <div className="rounded-xl border bg-card p-3">
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Icon className="size-3.5" aria-hidden /> {label}
      </p>
      <p className="mt-1 text-2xl font-semibold tabular-nums tracking-tight">{value}</p>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function TopicRow({ topic, onPractise }: { topic: Topic; onPractise: (topic: Topic) => void }) {
  const tone = topic.accuracy >= 0.7 ? "bg-conf-high" : topic.accuracy >= 0.4 ? "bg-conf-moderate" : "bg-conf-low";
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border bg-card px-3 py-2 text-sm">
      <span className="min-w-0 flex-1 truncate font-medium">{topic.topic}</span>
      <span className="flex w-28 items-center gap-2" aria-label={`${percent(topic.accuracy)} right`}>
        <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
          <span className={cn("block h-full rounded-full", tone)} style={{ width: `${Math.round(topic.accuracy * 100)}%` }} />
        </span>
        <span className="w-9 text-right text-xs tabular-nums">{percent(topic.accuracy)}</span>
      </span>
      <span className="w-24 text-xs text-muted-foreground">
        {topic.correct}/{topic.answered} · {ago(topic.last_at)}
      </span>
      <Button size="sm" variant="ghost" onClick={() => onPractise(topic)}>
        Practise
      </Button>
    </li>
  );
}

/** The learner's own practice record: how it is going, what to revisit, and the questions they got wrong. */
export function ProgressView({ token, onPractise }: { token: string; onPractise: (topic: Topic) => void }) {
  const queryClient = useQueryClient();
  const progress = useQuery({
    queryKey: PROGRESS_KEY,
    queryFn: () => api.tutorProgress(token),
    enabled: Boolean(token),
  });
  const [confirming, setConfirming] = useState(false);
  const clear = useMutation({
    mutationFn: () => api.clearTutorProgress(token),
    onSuccess: ({ deleted }) => {
      void queryClient.invalidateQueries({ queryKey: PROGRESS_KEY });
      setConfirming(false);
      toast.success(deleted ? `Cleared ${deleted} answers.` : "Nothing to clear.");
    },
    onError: () => toast.error("Couldn't clear your progress."),
  });

  if (progress.isLoading) {
    return (
      <div className="grid gap-3 sm:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-20" />
        ))}
      </div>
    );
  }
  const data = progress.data;
  if (!data) {
    return <p className="text-sm text-muted-foreground">Your progress couldn&rsquo;t be loaded. Try again in a minute.</p>;
  }
  if (data.answered === 0) {
    return (
      <div className="rounded-xl border border-dashed p-8 text-center" data-testid="progress-empty">
        <TrendingUp className="mx-auto size-8 text-muted-foreground" aria-hidden />
        <p className="mt-2 font-medium">No practice yet</p>
        <p className="text-sm text-muted-foreground">Take a quiz or a case — every answer shows up here, topic by topic.</p>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-5" data-testid="progress-view">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Answered" value={String(data.answered)} hint={`${data.correct} right`} icon={Target} />
        <Stat label="Accuracy" value={percent(data.accuracy)} hint="all time" icon={TrendingUp} />
        <Stat
          label="This week"
          value={String(data.week_answered)}
          hint={data.week_answered ? `${percent(data.week_correct / data.week_answered)} right` : "no answers yet"}
          icon={Target}
        />
        <Stat
          label="Streak"
          value={`${data.streak_days} day${data.streak_days === 1 ? "" : "s"}`}
          hint="days in a row"
          icon={Flame}
        />
      </div>

      {data.weakest.length > 0 && (
        <section aria-labelledby="weakest-heading" className="flex flex-col gap-2">
          <h3 id="weakest-heading" className="text-sm font-semibold">
            Topics to revisit
          </h3>
          <ul className="flex flex-col gap-1.5" data-testid="progress-weakest">
            {data.weakest.map((topic) => (
              <TopicRow key={topic.topic} topic={topic} onPractise={onPractise} />
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="topics-heading" className="flex flex-col gap-2">
        <h3 id="topics-heading" className="text-sm font-semibold">
          Recent topics
        </h3>
        <ul className="flex flex-col gap-1.5" data-testid="progress-topics">
          {data.topics.map((topic) => (
            <TopicRow key={topic.topic} topic={topic} onPractise={onPractise} />
          ))}
        </ul>
      </section>

      {data.mistakes.length > 0 && (
        <section aria-labelledby="mistakes-heading" className="flex flex-col gap-2">
          <h3 id="mistakes-heading" className="text-sm font-semibold">
            Questions you got wrong
          </h3>
          <ul className="flex flex-col gap-2">
            {data.mistakes.map((mistake) => (
              <li key={`${mistake.created_at}-${mistake.question}`} className="rounded-lg border bg-card p-3 text-sm">
                <p className="text-xs text-muted-foreground">
                  {mistake.topic} · {ago(mistake.created_at)}
                </p>
                <p className="mt-1 font-medium">{mistake.question}</p>
                <p className="mt-1 flex items-start gap-1.5 text-xs">
                  <XCircle className="mt-px size-3.5 shrink-0 text-conf-low" aria-hidden />
                  <span>
                    You chose “{mistake.chosen}”; the answer is “{mistake.answer}”.
                  </span>
                </p>
                {mistake.explanation && (
                  <details className="mt-1 text-xs text-muted-foreground">
                    <summary className="cursor-pointer select-none">Why</summary>
                    <p className="mt-1 leading-5">{mistake.explanation}</p>
                  </details>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <div>
        <Button variant="ghost" size="sm" onClick={() => setConfirming(true)} data-testid="progress-reset">
          <RotateCcw /> Reset progress
        </Button>
      </div>
      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reset your progress?</DialogTitle>
            <DialogDescription>
              Every answer you have recorded — {data.answered} of them — is deleted. Your conversations with the tutor stay.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirming(false)}>
              Keep it
            </Button>
            <Button variant="destructive" onClick={() => clear.mutate()} disabled={clear.isPending} data-testid="progress-reset-confirm">
              Reset
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
