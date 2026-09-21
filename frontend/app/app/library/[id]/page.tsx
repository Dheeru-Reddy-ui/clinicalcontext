"use client";

import { ArrowLeft, ExternalLink } from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef } from "react";

import { SaveToBinder } from "@/components/answer/answer-actions";
import { GRADE_DESCRIPTION, GradeBadge } from "@/components/clinical/badges";
import { ErrorState, PageBody } from "@/components/clinical/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDocument, useDocumentChunks } from "@/hooks/use-api";
import type { EvidenceGrade } from "@/lib/domain";
import { formatDate, humanStudyType } from "@/lib/text";
import { cn } from "@/lib/utils";

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const doc = useDocument(id);
  const chunks = useDocumentChunks(id);

  if (doc.isPending) {
    return (
      <PageBody>
        <Skeleton className="h-6 w-32" />
        <Skeleton className="h-8 w-3/4" />
        <Skeleton className="h-40 w-full" />
      </PageBody>
    );
  }
  if (doc.isError) {
    return (
      <PageBody>
        <ErrorState error={doc.error} onRetry={() => void doc.refetch()} />
      </PageBody>
    );
  }
  const d = doc.data;
  const grade = (d.evidence_grade ?? null) as EvidenceGrade | null;
  const pubmed = d.pmid ? `https://pubmed.ncbi.nlm.nih.gov/${d.pmid}/` : null;
  const doi = d.doi ? `https://doi.org/${d.doi}` : null;
  const authors = d.authors
    .map((a) => (typeof a === "string" ? a : typeof a === "object" && a && "name" in a ? String((a as { name: unknown }).name) : null))
    .filter((a): a is string => Boolean(a));

  return (
    <PageBody>
      <Button variant="ghost" size="sm" className="self-start" nativeButton={false} render={<Link href="/app/library" />}>
        <ArrowLeft /> Library
      </Button>

      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant="secondary" className="font-mono text-[10px] uppercase">{d.source_type}</Badge>
          {!d.shared && <Badge variant="outline">Private to your organisation</Badge>}
          <GradeBadge grade={grade} showWord />
        </div>
        <h1 className="text-lg font-semibold leading-snug tracking-tight">{d.title}</h1>
        <p className="text-sm text-muted-foreground">
          {[d.journal, d.publication_date ? formatDate(d.publication_date, { year: "numeric", month: "long" }) : null, humanStudyType(d.study_type)]
            .filter(Boolean)
            .join(" · ")}
        </p>
        {authors.length > 0 && <p className="text-sm text-muted-foreground">{authors.join(", ")}</p>}
        <div className="flex flex-wrap gap-1.5">
          {pubmed && (
            <Button size="xs" variant="outline" nativeButton={false} render={<a href={pubmed} target="_blank" rel="noreferrer noopener" />}>
              <ExternalLink /> PubMed <span className="font-mono text-muted-foreground">{d.pmid}</span>
            </Button>
          )}
          {doi && (
            <Button size="xs" variant="outline" nativeButton={false} render={<a href={doi} target="_blank" rel="noreferrer noopener" />}>
              <ExternalLink /> DOI
            </Button>
          )}
          {!pubmed && !doi && d.url && (
            <Button size="xs" variant="outline" nativeButton={false} render={<a href={d.url} target="_blank" rel="noreferrer noopener" />}>
              <ExternalLink /> Source
            </Button>
          )}
        </div>
      </header>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <div className="space-y-4">
          {d.abstract && (
            <section className="rounded-lg border bg-card p-4">
              <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Abstract</h2>
              <p className="text-sm leading-6">{d.abstract}</p>
            </section>
          )}
          <section className="rounded-lg border bg-card" aria-labelledby="passages-heading">
            <h2 id="passages-heading" className="border-b px-4 py-2.5 text-sm font-medium">
              Passages
              <span className="ml-2 font-mono text-xs font-normal text-muted-foreground">
                {chunks.data ? `${chunks.data.total} · what retrieval can cite` : ""}
              </span>
            </h2>
            {chunks.isPending ? (
              <div className="space-y-2 p-4">
                <Skeleton className="h-16 w-full" />
                <Skeleton className="h-16 w-full" />
              </div>
            ) : chunks.isError ? (
              <ErrorState error={chunks.error} className="m-4" />
            ) : (
              <Suspense fallback={null}>
                <PassageList chunks={chunks.data.chunks} />
              </Suspense>
            )}
          </section>
        </div>

        <aside className="space-y-3 lg:sticky lg:top-4 lg:self-start">
          <section className="rounded-lg border bg-card p-4 text-sm">
            <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Evidence grade</h2>
            <GradeBadge grade={grade} showWord />
            <p className="mt-2 text-xs text-muted-foreground">{GRADE_DESCRIPTION[grade ?? "none"]}</p>
            {typeof d.classification.method === "string" && (
              <p className="mt-2 font-mono text-[11px] text-muted-foreground">classified by {d.classification.method}</p>
            )}
            {typeof d.classification.reasoning === "string" && d.classification.reasoning && (
              <p className="mt-1 text-xs text-muted-foreground">{d.classification.reasoning}</p>
            )}
          </section>
          <section className="rounded-lg border bg-card p-4 text-sm">
            <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Record</h2>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
              <dt className="text-muted-foreground">Ingested</dt>
              <dd>{formatDate(d.ingested_at)}</dd>
              <dt className="text-muted-foreground">Passages</dt>
              <dd>{d.chunk_count}</dd>
              {d.pmid && (
                <>
                  <dt className="text-muted-foreground">PMID</dt>
                  <dd className="font-mono">{d.pmid}</dd>
                </>
              )}
              {d.doi && (
                <>
                  <dt className="text-muted-foreground">DOI</dt>
                  <dd className="break-all font-mono">{d.doi}</dd>
                </>
              )}
            </dl>
          </section>
        </aside>
      </div>
    </PageBody>
  );
}

function PassageList({ chunks }: { chunks: Array<{ id: string; chunk_index: number; section: string | null; content: string; embedded: boolean }> }) {
  const params = useSearchParams();
  const target = params.get("chunk");
  const ref = useRef<HTMLLIElement>(null);
  useEffect(() => {
    ref.current?.scrollIntoView({ block: "center" });
  }, [target]);
  return (
    <ol className="divide-y">
      {chunks.map((c) => {
        const active = c.id === target;
        return (
          <li
            key={c.id}
            ref={active ? ref : undefined}
            id={`chunk-${c.id}`}
            className={cn("group px-4 py-3", active && "bg-highlight/30")}
          >
            <div className="mb-1 flex items-center gap-2 text-[11px] text-muted-foreground">
              <span className="font-mono">#{c.chunk_index + 1}</span>
              {c.section && <span className="capitalize">{c.section}</span>}
              {!c.embedded && <span className="italic">not yet indexed</span>}
              {active && <span className="font-medium text-foreground">cited passage</span>}
              <span className="ml-auto opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
                <SaveToBinder chunkId={c.id} />
              </span>
            </div>
            <p className="text-sm leading-6">{c.content}</p>
          </li>
        );
      })}
    </ol>
  );
}
