"use client";

import { History as HistoryIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ConfidenceBadge, StatusPill } from "@/components/clinical/badges";
import { EmptyState, ErrorState, ListSkeleton, PageBody, PageHeader, Pagination } from "@/components/clinical/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useHistory } from "@/hooks/use-api";
import type { Confidence } from "@/lib/domain";
import { formatDate, formatUsd } from "@/lib/text";

type Filter = "all" | "mine" | "contradiction" | "abstained" | "blocked";
const LIMIT = 25;

export default function HistoryPage() {
  const [filter, setFilter] = useState<Filter>("all");
  const [offset, setOffset] = useState(0);
  const params = {
    limit: LIMIT,
    offset,
    mine: filter === "mine" || undefined,
    has_contradiction: filter === "contradiction" || undefined,
    abstained: filter === "abstained" || undefined,
    status: filter === "blocked" ? "blocked" : undefined,
  };
  const history = useHistory(params);

  return (
    <PageBody>
      <PageHeader
        title="History"
        description="Every query your organisation has asked, with what came back."
        actions={
          <ToggleGroup
            value={[filter]}
            onValueChange={(v) => {
              const next = (Array.isArray(v) ? v[0] : v) as Filter | undefined;
              if (next) {
                setFilter(next);
                setOffset(0);
              }
            }}
            aria-label="Filter history"
          >
            <ToggleGroupItem value="all">All</ToggleGroupItem>
            <ToggleGroupItem value="mine">Mine</ToggleGroupItem>
            <ToggleGroupItem value="contradiction">Conflicts</ToggleGroupItem>
            <ToggleGroupItem value="abstained">Abstained</ToggleGroupItem>
            <ToggleGroupItem value="blocked">Blocked</ToggleGroupItem>
          </ToggleGroup>
        }
      />

      {history.isPending ? (
        <ListSkeleton />
      ) : history.isError ? (
        <ErrorState error={history.error} onRetry={() => void history.refetch()} />
      ) : history.data.items.length === 0 ? (
        <EmptyState
          icon={HistoryIcon}
          title={filter === "all" ? "No queries yet" : "Nothing matches this filter"}
          description={
            filter === "all"
              ? "Ask your first question and it will appear here with its confidence, sources, and cost."
              : "Try a different filter, or ask something new."
          }
          action={
            <Button size="sm" nativeButton={false} render={<Link href="/app" />}>
              Ask a question
            </Button>
          }
        />
      ) : (
        <>
          <ol className="divide-y rounded-lg border bg-card" aria-label="Queries">
            {history.data.items.map((item) => (
              <li key={item.id}>
                <Link
                  href={`/app/queries/${item.id}`}
                  className="flex flex-col gap-1.5 px-4 py-3 hover:bg-accent/50 focus-visible:bg-accent/50 sm:flex-row sm:items-center sm:gap-4"
                >
                  <span className="min-w-0 flex-1 truncate text-sm">{item.raw_query}</span>
                  <span className="flex flex-wrap items-center gap-1.5">
                    {item.mode === "comparison" && <Badge variant="outline">Comparison</Badge>}
                    {item.status === "blocked" && <StatusPill kind="blocked" />}
                    {item.confidence && <ConfidenceBadge level={item.confidence as Confidence} compact />}
                    {item.has_contradiction && <StatusPill kind="conflict" />}
                    {item.abstained && <StatusPill kind="abstained" />}
                    {item.cached && <StatusPill kind="cached" />}
                  </span>
                  <span className="flex items-center gap-3 font-mono text-[11px] text-muted-foreground sm:w-44 sm:justify-end">
                    <span>{formatUsd(item.cost_usd)}</span>
                    <time dateTime={item.created_at}>{formatDate(item.created_at, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</time>
                  </span>
                </Link>
              </li>
            ))}
          </ol>
          <Pagination total={history.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
        </>
      )}
    </PageBody>
  );
}
