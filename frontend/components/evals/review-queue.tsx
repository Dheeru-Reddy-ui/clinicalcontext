"use client";

import { ClipboardCheck } from "lucide-react";
import Link from "next/link";

import { LocalTime } from "@/components/clinical/local-time";
import { Skeleton } from "@/components/ui/skeleton";
import { useReviewQueue } from "@/hooks/use-api";

/**
 * The review queue (Phase 12, owners only): answers this org flagged as
 * wrong or unsupported, waiting to be reviewed and promoted into the golden
 * set — or already promoted or rejected. Promotion itself happens with
 * `python -m evals.golden.promote`, which needs the reviewer to write the
 * reference answer; this list is where a reviewer starts.
 */
export function ReviewQueue({ enabled }: { enabled: boolean }) {
  const queue = useReviewQueue(undefined, enabled);
  const data = queue.data;
  if (!enabled) return null;
  return (
    <section className="rounded-lg border bg-card" aria-labelledby="review-queue-heading">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2.5">
        <h2 id="review-queue-heading" className="flex items-center gap-2 text-sm font-medium">
          <ClipboardCheck className="size-4" aria-hidden /> Golden-set review queue
        </h2>
        {data && (
          <span className="font-mono text-xs text-muted-foreground">
            {data.pending} pending · {data.promoted} promoted · {data.rejected} rejected
          </span>
        )}
      </header>
      {queue.isPending ? (
        <div className="space-y-2 p-4">
          <Skeleton className="h-6 w-full" />
        </div>
      ) : !data || data.items.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">
          Nothing flagged. A thumbs-down marked <em>wrong</em> or <em>unsupported</em> lands here.
        </p>
      ) : (
        <ol className="divide-y">
          {data.items.map((item) => (
            <li key={item.id} className="space-y-1 px-4 py-2.5 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Link href={`/app/queries/${item.query_id}`} className="min-w-0 flex-1 truncate font-medium underline-offset-4 hover:underline">
                  {item.question}
                </Link>
                <span
                  className={`rounded px-1.5 py-0.5 font-mono text-[11px] ${
                    item.status === "pending"
                      ? "bg-amber-500/15 text-amber-700 dark:text-amber-300"
                      : item.status === "promoted"
                        ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                        : "bg-muted text-muted-foreground"
                  }`}
                >
                  {item.status}
                  {item.golden_id ? ` · ${item.golden_id}` : ""}
                </span>
              </div>
              <p className="truncate text-xs text-muted-foreground">
                {item.reason ?? "—"}
                {item.comment ? ` — “${item.comment}”` : ""} · confidence {item.confidence ?? "—"} · {item.citations} citation
                {item.citations === 1 ? "" : "s"} · <LocalTime iso={item.created_at} />
              </p>
              {item.status === "pending" && (
                <p className="font-mono text-[11px] text-muted-foreground">
                  uv run python -m evals.golden.promote show {item.id}
                </p>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
