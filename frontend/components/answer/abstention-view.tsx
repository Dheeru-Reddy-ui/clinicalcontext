"use client";

import { SearchX } from "lucide-react";

import type { AnswerModel } from "@/components/answer/answer-model";
import { GradeBadge } from "@/components/clinical/badges";
import { Button } from "@/components/ui/button";
import { yearOf } from "@/lib/text";
import { cn } from "@/lib/utils";

const GRADE_EXPLANATION = {
  sufficient: "The passages were relevant, but the answer could not be grounded sentence by sentence.",
  insufficient: "Passages were found, but not enough of them were relevant and strong enough to support a grounded answer.",
  irrelevant: "Nothing retrieved was about this question.",
} as const;

/**
 * Spec #5: a dignified, useful non-answer. What was searched, what was found,
 * why it fell short, and how to ask a question the corpus can answer.
 */
export function AbstentionView({
  answer,
  onCite,
  onRetry,
  className,
}: {
  answer: AnswerModel;
  onCite: (marker: number) => void;
  onRetry?: (suggestedQuery: string) => void;
  className?: string;
}) {
  const { reasoning } = answer;
  const searched =
    reasoning.sub_questions.length > 0
      ? reasoning.sub_questions
      : [answer.contextualizedQuery ?? answer.query];
  const suggestion = suggestBetterQuestion(answer.query, reasoning.query_type);

  return (
    <section className={cn("rounded-lg border bg-card", className)} aria-labelledby="abstain-heading">
      <header className="flex items-start gap-3 border-b px-4 py-4">
        <span className="grid size-8 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
          <SearchX className="size-4" aria-hidden />
        </span>
        <div>
          <h2 id="abstain-heading" className="text-sm font-semibold">
            Not enough solid evidence to answer this
          </h2>
          <p className="text-sm text-muted-foreground">
            Rather than guess, the system abstained. Here is exactly what it tried.
          </p>
        </div>
      </header>

      <dl className="grid gap-4 p-4 md:grid-cols-3">
        <div>
          <dt className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            What was searched
          </dt>
          <dd>
            <ul className="space-y-1 text-sm">
              {searched.map((q) => (
                <li key={q} className="rounded-sm bg-muted/60 px-2 py-1 font-mono text-xs">
                  {q}
                </li>
              ))}
              {reasoning.rewrite_count > 0 && (
                <li className="text-xs text-muted-foreground">
                  + {reasoning.rewrite_count} query refinement{reasoning.rewrite_count === 1 ? "" : "s"}
                </li>
              )}
            </ul>
          </dd>
        </div>
        <div>
          <dt className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            What was found
          </dt>
          <dd>
            {answer.citations.length === 0 ? (
              <p className="text-sm text-muted-foreground">No relevant passages.</p>
            ) : (
              <ul className="space-y-1.5">
                {answer.citations.slice(0, 5).map((c) => (
                  <li key={c.marker} className="flex items-start gap-2 text-xs">
                    <button type="button" className="cite-chip mt-0.5 shrink-0" onClick={() => onCite(c.marker)} aria-label={`Open source ${c.marker}`}>
                      {c.marker}
                    </button>
                    <span className="min-w-0">
                      <span className="line-clamp-2">{c.title ?? "Untitled source"}</span>
                      <span className="flex items-center gap-1.5 text-muted-foreground">
                        {yearOf(c.publication_date) ?? "n.d."} <GradeBadge grade={c.evidence_grade} />
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </dd>
        </div>
        <div>
          <dt className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            Why it fell short
          </dt>
          <dd className="text-sm">
            <p>{GRADE_EXPLANATION[reasoning.retrieval_grade]}</p>
            <p className="mt-1 font-mono text-xs text-muted-foreground">
              retrieval graded “{reasoning.retrieval_grade}”
            </p>
          </dd>
        </div>
      </dl>

      <div className="border-t px-4 py-3">
        <p className="text-sm">
          <span className="font-medium">Try a better-formed question. </span>
          <span className="text-muted-foreground">
            Name the population, the intervention, and the outcome — the corpus answers PICO-shaped questions best.
          </span>
        </p>
        {suggestion && (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <code className="rounded-sm bg-muted px-2 py-1 text-xs">{suggestion}</code>
            {onRetry && (
              <Button size="xs" variant="outline" onClick={() => onRetry(suggestion)}>
                Ask this instead
              </Button>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

function suggestBetterQuestion(query: string, type: AnswerModel["reasoning"]["query_type"]): string | null {
  const q = query.trim().replace(/\?$/, "");
  if (q.length < 4) return null;
  switch (type) {
    case "therapy":
      return `In [population], does ${q.replace(/^(what|which|is|are|should)\s+/i, "")} improve [outcome] compared with [alternative]?`;
    case "diagnosis":
      return `In [population], how accurate is [test] for diagnosing ${q.replace(/^(how|what|is)\s+/i, "")}?`;
    case "prognosis":
      return `In [population] with ${q.replace(/^(what|how)\s+/i, "")}, what is the [outcome] at [timeframe]?`;
    default:
      return `In [population], does [intervention] affect [outcome] — ${q}?`;
  }
}
