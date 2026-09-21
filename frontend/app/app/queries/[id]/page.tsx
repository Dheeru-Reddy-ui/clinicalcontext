"use client";

import { ArrowLeft, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMemo } from "react";

import { AnswerActions } from "@/components/answer/answer-actions";
import { fromQueryDetail } from "@/components/answer/answer-model";
import { AnswerView } from "@/components/answer/answer-view";
import { PhiBlockedView, ScopeBlockedView } from "@/components/answer/guardrail-view";
import { ErrorState, PageBody } from "@/components/clinical/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useQueryDetail } from "@/hooks/use-api";
import { formatDate, formatMs } from "@/lib/text";

export default function QueryDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const detail = useQueryDetail(id);
  const answer = useMemo(() => (detail.data ? fromQueryDetail(detail.data) : null), [detail.data]);

  if (detail.isPending) {
    return (
      <PageBody>
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-64 w-full" />
      </PageBody>
    );
  }
  if (detail.isError) {
    return (
      <PageBody>
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      </PageBody>
    );
  }

  const q = detail.data.query;
  const verdict = detail.data.guardrail_verdict;
  const blockedBy = q.status === "blocked" ? (verdict.blocked_by ?? "scope") : null;

  return (
    <PageBody>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" size="sm" nativeButton={false} render={<Link href="/app/history" />}>
          <ArrowLeft /> History
        </Button>
        <span className="ml-auto flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
          <time dateTime={q.created_at}>{formatDate(q.created_at, { dateStyle: "medium", timeStyle: "short" })}</time>
          {q.query_type && <Badge variant="outline">{q.query_type.replace(/_/g, " ")}</Badge>}
          {q.mode === "comparison" && <Badge variant="outline">Comparison</Badge>}
          {q.pico && <Badge variant="outline">PICO</Badge>}
        </span>
      </div>

      <header>
        <h1 className="text-lg font-semibold tracking-tight">{q.raw_query}</h1>
        {q.contextualized_query && (
          <p className="text-sm text-muted-foreground">
            Interpreted as: <span className="text-foreground">{q.contextualized_query}</span>
          </p>
        )}
      </header>

      {blockedBy === "phi" && <PhiBlockedView onReset={() => router.push("/app")} />}
      {blockedBy && blockedBy !== "phi" && (
        <ScopeBlockedView
          message={verdict.findings?.[0]?.message ?? "This question is outside the tool's scope."}
          onReset={() => router.push("/app")}
        />
      )}

      {answer && (
        <AnswerView
          answer={answer}
          actions={
            <>
              <Button
                variant="ghost"
                size="sm"
                nativeButton={false}
                render={<Link href={`/app?q=${encodeURIComponent(q.raw_query)}`} />}
              >
                <RotateCcw /> Ask again
              </Button>
              <AnswerActions answer={answer} />
            </>
          }
        />
      )}

      {detail.data.retrieval_traces.length > 0 && (
        <section className="rounded-lg border bg-card" aria-labelledby="trace-heading">
          <h2 id="trace-heading" className="border-b px-4 py-2.5 text-sm font-medium">
            Retrieval trace
            <span className="ml-2 font-mono text-xs font-normal text-muted-foreground">
              how the sources were found
            </span>
          </h2>
          <ol className="divide-y">
            {detail.data.retrieval_traces.map((t, i) => (
              <li key={i} className="grid grid-cols-[8rem_1fr_auto] gap-3 px-4 py-2 text-xs">
                <span className="font-mono">{t.stage}</span>
                <span className="text-muted-foreground">
                  {t.chunk_ids.length} passage{t.chunk_ids.length === 1 ? "" : "s"}
                  {t.scores.length > 0 && (
                    <> · top score {Math.max(...t.scores).toFixed(3)}</>
                  )}
                </span>
                <span className="font-mono text-muted-foreground">{formatMs(t.duration_ms)}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {verdict.findings && verdict.findings.length > 0 && (
        <section className="rounded-lg border bg-card" aria-labelledby="guard-heading">
          <h2 id="guard-heading" className="border-b px-4 py-2.5 text-sm font-medium">
            Guardrail verdict
          </h2>
          <ul className="divide-y">
            {verdict.findings.map((f, i) => (
              <li key={i} className="flex gap-3 px-4 py-2 text-xs">
                <span className="font-mono">{f.code ?? "finding"}</span>
                <span className="text-muted-foreground">{f.message}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </PageBody>
  );
}
