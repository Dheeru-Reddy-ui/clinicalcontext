"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, FileText, MessageSquarePlus, MessagesSquare } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef } from "react";

import { ChatPanel } from "@/components/chat/chat-panel";
import { designLabel, PAPERS_KEY } from "@/components/learn/papers/paper-library";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useToken } from "@/hooks/use-api";
import { useChat } from "@/hooks/use-chat";
import { api } from "@/lib/api-client";
import { cn } from "@/lib/utils";

const QUICK_QUESTIONS: ReadonlyArray<{ label: string; ask: string }> = [
  {
    label: "Summarise this paper",
    ask: "Summarise this paper: why it was done, what was done, the main findings with their numbers, and what the authors conclude.",
  },
  { label: "Critical appraisal", ask: "Critically appraise this paper." },
  { label: "Key results, with the numbers", ask: "What are the key results, with the exact numbers?" },
  { label: "Limitations", ask: "What are the limitations of this study?" },
  { label: "The methods, simply", ask: "Explain the methods of this study in simple terms." },
  {
    label: "What it means for practice",
    ask: "What does this study mean for clinical practice, according to the authors?",
  },
];

/**
 * One paper: questions answered from it alone (each answer citing the
 * section and page), beside its outline and the earlier conversations.
 */
export function PaperView({ id }: { id: string }) {
  const token = useToken();
  const queryClient = useQueryClient();
  const detailKey = [...PAPERS_KEY, id] as const;
  const paper = useQuery({ queryKey: detailKey, queryFn: () => api.paper(token, id), enabled: Boolean(token) });
  const chat = useChat({ kind: "paper", audience: "student", documentId: id });

  // A finished answer adds (or updates) a conversation in the list beside it.
  const wasBusy = useRef(false);
  useEffect(() => {
    if (wasBusy.current && !chat.busy) void queryClient.invalidateQueries({ queryKey: detailKey });
    wasBusy.current = chat.busy;
    // detailKey is rebuilt every render; the id it depends on is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.busy, queryClient, id]);

  if (paper.error) {
    return (
      <div className="mx-auto max-w-3xl p-6 text-center">
        <p className="font-medium">This paper isn’t available.</p>
        <p className="text-sm text-muted-foreground">It may have been deleted, or it belongs to another workspace.</p>
        <Link href="/app/learn/papers" className="mt-3 inline-block text-sm font-medium text-primary hover:underline">
          Back to your papers
        </Link>
      </div>
    );
  }
  const data = paper.data;
  const design = designLabel(data?.study_type);

  return (
    <div className="mx-auto grid max-w-7xl gap-6 p-4 md:p-6 lg:grid-cols-[minmax(0,1fr)_320px]" data-testid="paper-view">
      <section className="flex min-h-[70vh] flex-col rounded-xl border bg-card" aria-label="Questions to the paper">
        <header className="flex items-center gap-2 border-b px-4 py-3">
          <Link href="/app/learn/papers" className="text-muted-foreground hover:text-foreground" aria-label="All papers">
            <ArrowLeft className="size-4" />
          </Link>
          <h1 className="min-w-0 flex-1 truncate font-semibold tracking-tight" data-testid="paper-title">
            {data?.title ?? (paper.isLoading ? "Loading…" : "Paper")}
          </h1>
          {chat.messages.length > 0 && (
            <Button size="sm" variant="ghost" onClick={() => chat.reset()}>
              <MessageSquarePlus /> New
            </Button>
          )}
        </header>
        <ChatPanel
          chat={chat}
          voice
          showAudience={false}
          placeholder="Ask the paper anything…"
          empty={
            <div className="flex flex-col gap-4 py-8">
              <div className="text-center">
                <h2 className="text-lg font-semibold tracking-tight">Ask this paper</h2>
                <p className="text-sm text-muted-foreground">
                  Answers come only from the paper, with the section and page each point is from.
                </p>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {QUICK_QUESTIONS.map((q) => (
                  <button
                    key={q.label}
                    type="button"
                    onClick={() => void chat.send(q.ask)}
                    className="rounded-lg border px-3 py-2.5 text-left text-sm transition-colors hover:border-primary/40 hover:bg-muted/40"
                    data-testid="paper-quick"
                  >
                    {q.label}
                  </button>
                ))}
              </div>
            </div>
          }
        />
      </section>

      <aside className="flex flex-col gap-4" aria-label="About the paper">
        {paper.isLoading && <Skeleton className="h-40" />}
        {data && (
          <>
            <div className="rounded-xl border bg-card p-4 text-sm">
              <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                <FileText className="size-3.5" aria-hidden /> The paper
              </p>
              <p className="mt-2 flex flex-wrap gap-1.5 text-xs">
                {design && <span className="rounded bg-muted px-1.5 py-0.5 font-medium">{design}</span>}
                {data.evidence_grade && (
                  <span className="rounded bg-muted px-1.5 py-0.5 font-medium">Grade {data.evidence_grade}</span>
                )}
                {data.pages !== null && data.pages !== undefined && (
                  <span className="rounded bg-muted px-1.5 py-0.5">
                    {data.pages} page{data.pages === 1 ? "" : "s"}
                  </span>
                )}
              </p>
              {data.abstract && (
                <details className="mt-3">
                  <summary className="cursor-pointer select-none text-xs font-medium">Abstract</summary>
                  <p className="mt-1.5 text-xs leading-5 text-muted-foreground">{data.abstract}</p>
                </details>
              )}
            </div>

            {data.outline.length > 0 && (
              <nav className="rounded-xl border bg-card p-4" aria-label="Outline">
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Outline</p>
                <ol className="flex flex-col gap-1 text-sm" data-testid="paper-outline">
                  {data.outline.map((section, i) => (
                    <li key={`${section.title}-${i}`} className="flex justify-between gap-2">
                      <span className="min-w-0 truncate">{section.title}</span>
                      {section.page !== null && section.page !== undefined && (
                        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">p. {section.page}</span>
                      )}
                    </li>
                  ))}
                </ol>
              </nav>
            )}

            {data.conversations.length > 0 && (
              <div className="rounded-xl border bg-card p-4">
                <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  <MessagesSquare className="size-3.5" aria-hidden /> Your conversations
                </p>
                <ul className="flex flex-col gap-1">
                  {data.conversations.map((session) => (
                    <li key={session.id}>
                      <button
                        type="button"
                        onClick={() => void chat.open(session.id)}
                        className={cn(
                          "w-full truncate rounded px-2 py-1 text-left text-sm hover:bg-muted/50",
                          chat.sessionId === session.id && "bg-muted font-medium",
                        )}
                      >
                        {session.title ?? "Conversation"}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </aside>
    </div>
  );
}
