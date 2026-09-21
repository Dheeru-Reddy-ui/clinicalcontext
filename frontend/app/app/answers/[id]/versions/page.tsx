"use client";

import { diffArrays } from "diff";
import { ArrowLeft, Bell, BellOff, GitCompareArrows } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";

import { ConfidenceBadge } from "@/components/clinical/badges";
import { EmptyState, ErrorState, PageBody, PageHeader } from "@/components/clinical/page";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useFollow, useVersions } from "@/hooks/use-api";
import { asCitations, type Citation, type Confidence } from "@/lib/domain";
import { formatDate, overlap, splitSentences, stripCitations } from "@/lib/text";
import { cn } from "@/lib/utils";

interface DiffPayload {
  similarity?: number;
  new_document_ids?: string[];
  dropped_document_ids?: string[];
  confidence_before?: string | null;
  confidence_after?: string | null;
  contradiction_before?: boolean;
  contradiction_after?: boolean;
  reasons?: string[];
}

type Claim = { text: string; kind: "same" | "added" | "removed" | "changed" };

/**
 * Claim-level diff: sentences are the unit. Exact matches are "same"; a
 * removed sentence that closely resembles an added one becomes a single
 * "changed" pair; the rest are pure additions/removals.
 */
function diffClaims(before: string, after: string): { left: Claim[]; right: Claim[] } {
  const a = splitSentences(stripCitations(before));
  const b = splitSentences(stripCitations(after));
  const parts = diffArrays(a, b);
  const left: Claim[] = [];
  const right: Claim[] = [];
  let pendingRemoved: string[] = [];
  const flushRemoved = () => {
    for (const t of pendingRemoved) left.push({ text: t, kind: "removed" });
    pendingRemoved = [];
  };
  for (const part of parts) {
    if (part.removed) {
      pendingRemoved.push(...part.value);
      continue;
    }
    if (part.added) {
      for (const t of part.value) {
        const idx = pendingRemoved.findIndex((r) => overlap(r, t) >= 0.5);
        if (idx >= 0) {
          left.push({ text: pendingRemoved[idx] ?? "", kind: "changed" });
          right.push({ text: t, kind: "changed" });
          pendingRemoved.splice(idx, 1);
        } else {
          right.push({ text: t, kind: "added" });
        }
      }
      flushRemoved();
      continue;
    }
    flushRemoved();
    for (const t of part.value) {
      left.push({ text: t, kind: "same" });
      right.push({ text: t, kind: "same" });
    }
  }
  flushRemoved();
  return { left, right };
}

const KIND_CLASS: Record<Claim["kind"], string> = {
  same: "",
  added: "bg-conf-high-bg text-conf-high-fg",
  removed: "bg-conf-low-bg text-conf-low-fg line-through decoration-conf-low/60",
  changed: "bg-conflict-bg text-conflict-fg",
};

export default function VersionsPage() {
  const { id } = useParams<{ id: string }>();
  const versions = useVersions(id);
  const follow = useFollow(id);
  const [pair, setPair] = useState<[number, number] | null>(null);

  const list = versions.data?.versions ?? [];
  const [oldIdx, newIdx] = pair ?? [Math.max(0, list.length - 2), list.length - 1];
  const older = list[oldIdx];
  const newer = list[newIdx];

  const claims = useMemo(() => (older && newer ? diffClaims(older.content, newer.content) : null), [older, newer]);
  const diff = (newer?.diff ?? {}) as DiffPayload;
  const newCitations: Citation[] = useMemo(() => {
    if (!newer) return [];
    const newDocs = new Set(diff.new_document_ids ?? []);
    return asCitations(newer.citations).filter((c) => newDocs.has(c.document_id));
  }, [newer, diff.new_document_ids]);

  if (versions.isPending) {
    return (
      <PageBody>
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-64 w-full" />
      </PageBody>
    );
  }
  if (versions.isError) {
    return (
      <PageBody>
        <ErrorState error={versions.error} onRetry={() => void versions.refetch()} />
      </PageBody>
    );
  }

  return (
    <PageBody>
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" nativeButton={false} render={<Link href="/app/history" />}>
          <ArrowLeft /> History
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="ml-auto"
          aria-pressed={versions.data.following}
          disabled={follow.isPending}
          onClick={() => follow.mutate(!versions.data.following)}
        >
          {versions.data.following ? <Bell className="fill-current" /> : <BellOff />}
          {versions.data.following ? "Following" : "Follow"}
        </Button>
      </div>
      <PageHeader
        title="Living Answer — version history"
        description={
          versions.data.superseded
            ? "The evidence behind this answer changed. Added claims are green, removed red, reworded amber."
            : "This answer has not been superseded. Follow it to be told when the evidence changes."
        }
      />

      {list.length < 2 ? (
        <EmptyState icon={GitCompareArrows} title="Only one version so far" description="A diff appears here after the nightly re-query finds a material change in the evidence." />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-muted-foreground">Compare</span>
            <select
              aria-label="Older version"
              value={oldIdx}
              onChange={(e) => setPair([Number(e.target.value), newIdx])}
              className="h-8 rounded-md border bg-background px-2 text-sm"
            >
              {list.map((v, i) => (
                <option key={v.version} value={i} disabled={i >= newIdx}>
                  v{v.version} · {formatDate(v.created_at)}
                </option>
              ))}
            </select>
            <span className="text-muted-foreground">with</span>
            <select
              aria-label="Newer version"
              value={newIdx}
              onChange={(e) => setPair([oldIdx, Number(e.target.value)])}
              className="h-8 rounded-md border bg-background px-2 text-sm"
            >
              {list.map((v, i) => (
                <option key={v.version} value={i} disabled={i <= oldIdx}>
                  v{v.version} · {formatDate(v.created_at)}
                </option>
              ))}
            </select>
          </div>

          <section className="grid gap-3 rounded-lg border bg-card p-4 sm:grid-cols-3" aria-label="What changed">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Confidence</p>
              <p className="mt-1 flex items-center gap-2">
                {older?.confidence && <ConfidenceBadge level={older.confidence as Confidence} compact />}
                <span aria-hidden>→</span>
                {newer?.confidence && <ConfidenceBadge level={newer.confidence as Confidence} compact />}
                {older?.confidence === newer?.confidence && <span className="text-xs text-muted-foreground">unchanged</span>}
              </p>
            </div>
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Sources</p>
              <p className="mt-1 text-sm">
                +{(diff.new_document_ids ?? []).length} new · −{(diff.dropped_document_ids ?? []).length} dropped
                {typeof diff.similarity === "number" && (
                  <span className="ml-2 font-mono text-xs text-muted-foreground">text similarity {(diff.similarity * 100).toFixed(0)}%</span>
                )}
              </p>
            </div>
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Why it changed</p>
              <ul className="mt-1 text-sm">
                {(diff.reasons ?? []).map((r) => (
                  <li key={r}>{r}</li>
                ))}
                {(diff.reasons ?? []).length === 0 && <li className="text-muted-foreground">No reason recorded.</li>}
              </ul>
            </div>
          </section>

          {claims && (
            <div className="grid gap-3 lg:grid-cols-2">
              <VersionColumn title={`v${older?.version} — before`} date={older?.created_at} claims={claims.left} />
              <VersionColumn title={`v${newer?.version} — after`} date={newer?.created_at} claims={claims.right} />
            </div>
          )}

          {newCitations.length > 0 && (
            <section className="rounded-lg border bg-card" aria-labelledby="new-cites">
              <h2 id="new-cites" className="border-b px-4 py-2.5 text-sm font-medium">
                New citations that drove the change
              </h2>
              <ul className="divide-y">
                {newCitations.map((c) => (
                  <li key={c.chunk_id} className="px-4 py-2 text-sm">
                    <Link href={`/app/library/${c.document_id}?chunk=${c.chunk_id}`} className="hover:underline">
                      {c.title ?? "Untitled source"}
                    </Link>
                    <span className="ml-2 text-xs text-muted-foreground">
                      {[c.journal, c.publication_date?.slice(0, 4)].filter(Boolean).join(" · ")}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </PageBody>
  );
}

function VersionColumn({ title, date, claims }: { title: string; date?: string; claims: Claim[] }) {
  return (
    <section className="rounded-lg border bg-card">
      <h2 className="flex items-center justify-between border-b px-4 py-2.5 text-sm font-medium">
        {title}
        {date && <time dateTime={date} className="font-mono text-[11px] font-normal text-muted-foreground">{formatDate(date)}</time>}
      </h2>
      <p className="space-y-1 p-4 text-sm leading-7">
        {claims.map((c, i) => (
          <span key={i} className={cn("rounded-sm px-0.5 box-decoration-clone", KIND_CLASS[c.kind])} data-kind={c.kind}>
            {c.text}{" "}
          </span>
        ))}
      </p>
    </section>
  );
}
