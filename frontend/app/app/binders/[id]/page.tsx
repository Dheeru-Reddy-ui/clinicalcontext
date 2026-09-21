"use client";

import { ArrowLeft, Download, ExternalLink, Presentation, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { fromSnapshot } from "@/components/answer/answer-model";
import { AnswerView } from "@/components/answer/answer-view";
import { PassageAnnotator } from "@/components/binders/passage-annotator";
import { GradeBadge } from "@/components/clinical/badges";
import { EmptyState, ErrorState, PageBody } from "@/components/clinical/page";
import { useAuth } from "@/components/providers/auth-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { keys, useBinder, useToken } from "@/hooks/use-api";
import { api } from "@/lib/api-client";
import type { EvidenceGrade } from "@/lib/domain";
import { formatDate, humanStudyType, yearOf } from "@/lib/text";
import { cn } from "@/lib/utils";
import { useQueryClient } from "@tanstack/react-query";

export default function BinderPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const binder = useBinder(id);
  const { role, me } = useAuth();
  const token = useToken();
  const client = useQueryClient();
  const [present, setPresent] = useState(false);

  if (binder.isPending) {
    return (
      <PageBody>
        <Skeleton className="h-6 w-32" />
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-48 w-full" />
      </PageBody>
    );
  }
  if (binder.isError) {
    return (
      <PageBody>
        <ErrorState error={binder.error} onRetry={() => void binder.refetch()} />
      </PageBody>
    );
  }

  const { binder: b, items } = binder.data;
  const isOwner = b.created_by === me?.user_id;
  const canCurate = role !== "viewer";

  const removeItem = async (itemId: string) => {
    try {
      await api.removeBinderItem(token, id, itemId);
      await client.invalidateQueries({ queryKey: keys.binder(id) });
      toast.success("Removed from binder.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not remove.");
    }
  };

  const deleteBinder = async () => {
    if (!window.confirm(`Delete “${b.title}”? Its items are removed; the underlying answers and passages are kept.`)) return;
    try {
      await api.deleteBinder(token, id);
      await client.invalidateQueries({ queryKey: keys.binders });
      router.push("/app/binders");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not delete.");
    }
  };

  return (
    <PageBody className={cn(present && "max-w-4xl")}>
      {!present && (
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="ghost" size="sm" nativeButton={false} render={<Link href="/app/binders" />}>
            <ArrowLeft /> Binders
          </Button>
          <div className="ml-auto flex items-center gap-1.5">
            <Button variant="outline" size="sm" onClick={() => setPresent(true)}>
              <Presentation /> Present
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void import("@/components/binders/export-binder-pdf").then((m) => m.exportBinderPdf(binder.data))}
            >
              <Download /> PDF
            </Button>
            {isOwner && (
              <Button variant="ghost" size="sm" onClick={() => void deleteBinder()} aria-label="Delete binder">
                <Trash2 />
              </Button>
            )}
          </div>
        </div>
      )}

      <header className="space-y-1">
        <div className="flex items-center gap-2">
          <h1 className={cn("font-semibold tracking-tight", present ? "text-3xl" : "text-xl")}>{b.title}</h1>
          <Badge variant={b.visibility === "org" ? "secondary" : "outline"}>{b.visibility === "org" ? "Organisation" : "Private"}</Badge>
        </div>
        {b.description && <p className={cn("text-muted-foreground", present ? "text-lg" : "text-sm")}>{b.description}</p>}
        <p className="font-mono text-[11px] text-muted-foreground">
          {b.item_count} item{b.item_count === 1 ? "" : "s"} · curated by {b.created_by_name ?? "—"} · {formatDate(b.created_at)}
        </p>
        {present && (
          <Button variant="ghost" size="sm" className="mt-2" onClick={() => setPresent(false)}>
            Exit present mode
          </Button>
        )}
      </header>

      {items.length === 0 ? (
        <EmptyState
          title="This binder is empty"
          description="Save an answer from the bookmark icon on any answer, or a passage from the library or a citation panel."
          action={
            <Button size="sm" nativeButton={false} render={<Link href="/app" />}>
              Ask a question
            </Button>
          }
        />
      ) : (
        <ol className={cn("space-y-4", present && "space-y-10")} aria-label="Binder items">
          {items.map((item, index) => (
            <li key={item.id} className="rounded-lg border bg-card">
              <div className="flex items-center gap-2 border-b px-4 py-2 text-[11px] text-muted-foreground">
                <span className="font-mono">{index + 1}</span>
                <Badge variant="outline" className="capitalize">{item.item_type}</Badge>
                <span>added by {item.added_by_name ?? "—"} · {formatDate(item.created_at)}</span>
                {!present && canCurate && (
                  <Button variant="ghost" size="xs" className="ml-auto" onClick={() => void removeItem(item.id)} aria-label="Remove from binder">
                    <Trash2 />
                  </Button>
                )}
              </div>
              <div className="p-4">
                {item.answer && (
                  <AnswerView answer={fromSnapshot(item.answer)} showFeedback={false} showFooter={!present} className={cn(present && "text-lg")} />
                )}
                {item.passage && (
                  <div className="space-y-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className={cn("font-medium", present ? "text-xl" : "text-sm")}>{item.passage.document_title}</h2>
                      <GradeBadge grade={item.passage.evidence_grade as EvidenceGrade | null} />
                      <span className="text-xs text-muted-foreground">
                        {[item.passage.journal, yearOf(item.passage.publication_date), humanStudyType(item.passage.study_type)].filter(Boolean).join(" · ")}
                      </span>
                      {!present && (
                        <Button variant="ghost" size="xs" className="ml-auto" nativeButton={false} render={<Link href={`/app/library/${item.passage.document_id}?chunk=${item.passage.chunk_id}`} />}>
                          <ExternalLink /> Open
                        </Button>
                      )}
                    </div>
                    <PassageAnnotator
                      binderId={id}
                      itemId={item.id}
                      text={item.passage.content}
                      annotations={item.annotations ?? []}
                      readOnly={!canCurate}
                      present={present}
                    />
                  </div>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </PageBody>
  );
}
