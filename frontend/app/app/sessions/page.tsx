"use client";

import { LayoutList, MessageSquarePlus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { EmptyState, ErrorState, ListSkeleton, PageBody, PageHeader, Pagination } from "@/components/clinical/page";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useSessions } from "@/hooks/use-api";
import { formatDate } from "@/lib/text";

const LIMIT = 25;

export default function SessionsPage() {
  const [mine, setMine] = useState(true);
  const [offset, setOffset] = useState(0);
  const sessions = useSessions({ limit: LIMIT, offset, mine });

  return (
    <PageBody>
      <PageHeader
        title="Sessions"
        description="Conversations — each one a thread of questions and follow-ups."
        actions={
          <>
            <ToggleGroup
              value={[mine ? "mine" : "org"]}
              onValueChange={(v) => {
                const next = Array.isArray(v) ? v[0] : v;
                if (next) {
                  setMine(next === "mine");
                  setOffset(0);
                }
              }}
              aria-label="Whose sessions"
            >
              <ToggleGroupItem value="mine">Mine</ToggleGroupItem>
              <ToggleGroupItem value="org">Organisation</ToggleGroupItem>
            </ToggleGroup>
            <Button size="sm" nativeButton={false} render={<Link href="/app" />}>
              <MessageSquarePlus /> New
            </Button>
          </>
        }
      />
      {sessions.isPending ? (
        <ListSkeleton />
      ) : sessions.isError ? (
        <ErrorState error={sessions.error} onRetry={() => void sessions.refetch()} />
      ) : sessions.data.sessions.length === 0 ? (
        <EmptyState
          icon={LayoutList}
          title="No sessions yet"
          description="A session starts with your first question. Follow-ups like “what about in pregnancy?” are resolved against what came before."
          action={
            <Button size="sm" nativeButton={false} render={<Link href="/app" />}>
              Start one
            </Button>
          }
        />
      ) : (
        <>
          <ol className="divide-y rounded-lg border bg-card" aria-label="Sessions">
            {sessions.data.sessions.map((s) => (
              <li key={s.id}>
                <Link
                  href={`/app/sessions/${s.id}`}
                  className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 hover:bg-accent/50 focus-visible:bg-accent/50"
                >
                  <span className="min-w-0 basis-full truncate text-sm sm:basis-auto sm:flex-1">
                    {s.title ?? "Untitled session"}
                  </span>
                  <span className="font-mono text-[11px] text-muted-foreground">
                    {s.query_count} turn{s.query_count === 1 ? "" : "s"}
                  </span>
                  <time dateTime={s.last_query_at ?? s.created_at} className="font-mono text-[11px] text-muted-foreground sm:w-28 sm:text-right">
                    {formatDate(s.last_query_at ?? s.created_at, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                  </time>
                </Link>
              </li>
            ))}
          </ol>
          <Pagination total={sessions.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
        </>
      )}
    </PageBody>
  );
}
