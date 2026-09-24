"use client";

import { ArrowLeft, ExternalLink, FlaskConical, Lightbulb, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";

import { ChatPanel } from "@/components/chat/chat-panel";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSpecialties, useSpecialtyFeed } from "@/hooks/use-api";
import { useChat } from "@/hooks/use-chat";
import { cn } from "@/lib/utils";

const DESIGN_TONE: Record<string, string> = {
  Guideline: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
  "Meta-analysis": "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-200",
  "Systematic review": "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-200",
  "Randomised trial": "bg-violet-100 text-violet-900 dark:bg-violet-950 dark:text-violet-200",
};

/**
 * One specialty: a teaching conversation scoped to it (MBBS or PG depth),
 * its core topics one click from a structured explainer, and the newest
 * guidelines, meta-analyses and trials PubMed has indexed.
 */
export default function SpecialtyPage() {
  const { slug } = useParams<{ slug: string }>();
  const specialties = useSpecialties();
  const specialty = specialties.data?.find((s) => s.slug === slug);
  const [chosenLevel, setLevel] = useState<"mbbs" | "pg" | null>(null);
  // The specialty's own level until the reader picks one (it loads after
  // the first render, so it cannot seed the state).
  const level = chosenLevel ?? (specialty?.level.startsWith("pg") ? "pg" : "mbbs");
  const chat = useChat({ kind: "learn", audience: "student", specialty: slug, level });
  const feed = useSpecialtyFeed(slug);

  const explain = (topic: string) =>
    void chat.send(
      level === "pg"
        ? `Explain ${topic} at postgraduate level: guideline-based management, key trials and recent updates.`
        : `Explain ${topic} for an MBBS student: definition, causes, pathophysiology, clinical features, investigations, management and high-yield points.`,
    );

  const topics = useMemo(() => specialty?.topics ?? [], [specialty]);

  return (
    <div className="mx-auto grid max-w-7xl gap-6 p-4 md:p-6 lg:grid-cols-[minmax(0,1fr)_340px]">
      <section className="flex min-h-[70vh] flex-col rounded-xl border bg-card">
        <header className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
          <Link href="/app/learn" className="text-muted-foreground hover:text-foreground" aria-label="All specialties">
            <ArrowLeft className="size-4" />
          </Link>
          <div className="min-w-0 flex-1">
            <h1 className="truncate font-semibold tracking-tight" data-testid="specialty-name">
              {specialty?.name ?? (specialties.isLoading ? "Loading…" : "Unknown specialty")}
            </h1>
            {specialty && <p className="text-xs text-muted-foreground">{specialty.level_label}</p>}
          </div>
          <div role="radiogroup" aria-label="Level" className="inline-flex rounded-md border p-0.5">
            {(["mbbs", "pg"] as const).map((l) => (
              <button
                key={l}
                type="button"
                role="radio"
                aria-checked={level === l}
                onClick={() => setLevel(l)}
                className={cn(
                  "rounded-[5px] px-2.5 py-1 text-xs font-medium",
                  level === l ? "bg-primary text-primary-foreground" : "text-muted-foreground",
                )}
              >
                {l === "mbbs" ? "MBBS" : "PG"}
              </button>
            ))}
          </div>
        </header>
        <ChatPanel
          chat={chat}
          voice
          showAudience={false}
          placeholder={specialty ? `Ask anything in ${specialty.name}…` : "Ask anything…"}
          empty={
            <div className="flex flex-col gap-4 py-8">
              <div className="text-center">
                <h2 className="text-lg font-semibold tracking-tight">
                  {specialty ? `Study ${specialty.name}` : "Study"}
                </h2>
                <p className="text-sm text-muted-foreground">
                  Pick a core topic for a structured explainer with sources, or ask your own question.
                </p>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {topics.map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => explain(t)}
                    className="flex items-center gap-2 rounded-lg border px-3 py-2.5 text-left text-sm transition-colors hover:border-primary/40 hover:bg-muted/40"
                    data-testid="learn-topic"
                  >
                    <Lightbulb className="size-4 shrink-0 text-amber-500" aria-hidden />
                    {t}
                  </button>
                ))}
              </div>
            </div>
          }
        />
      </section>

      <aside className="flex flex-col gap-3" aria-labelledby="latest-heading">
        <div className="flex items-center justify-between">
          <h2 id="latest-heading" className="flex items-center gap-2 text-sm font-semibold">
            <FlaskConical className="size-4 text-muted-foreground" aria-hidden /> Latest research
          </h2>
          <Button size="icon-sm" variant="ghost" onClick={() => void feed.refetch()} aria-label="Refresh">
            <RefreshCw className={cn(feed.isFetching && "animate-spin")} />
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          Guidelines, meta-analyses, systematic reviews and randomised trials PubMed added in the
          last 6 months.
        </p>
        {feed.isLoading &&
          Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-20" />)}
        {feed.data && !feed.data.available && (
          <p className="text-sm text-muted-foreground">{feed.data.message}</p>
        )}
        <ol className="flex flex-col gap-2" data-testid="research-feed">
          {feed.data?.items.map((item) => (
            <li key={item.pmid} className="rounded-lg border bg-card p-3 text-sm">
              <div className="mb-1 flex items-center gap-2 text-[11px]">
                <span className={cn("rounded px-1.5 py-0.5 font-medium", DESIGN_TONE[item.design] ?? "bg-muted")}>
                  {item.design}
                </span>
                <span className="font-mono text-muted-foreground">{item.published}</span>
              </div>
              <a
                href={item.url}
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium leading-5 hover:underline"
              >
                {item.title}
                <ExternalLink className="ml-1 inline size-3 align-baseline text-muted-foreground" aria-hidden />
              </a>
              <p className="mt-1 truncate text-xs text-muted-foreground">{item.journal}</p>
              <button
                type="button"
                onClick={() =>
                  void chat.send(
                    `Summarise this new paper and what it means for practice: "${item.title}" (${item.journal}, PMID ${item.pmid}).`,
                  )
                }
                className="mt-2 text-xs font-medium text-primary hover:underline"
              >
                Explain this paper
              </button>
            </li>
          ))}
        </ol>
        {feed.data?.available && feed.data.items.length === 0 && (
          <p className="text-sm text-muted-foreground">Nothing new in the last 6 months.</p>
        )}
      </aside>
    </div>
  );
}
