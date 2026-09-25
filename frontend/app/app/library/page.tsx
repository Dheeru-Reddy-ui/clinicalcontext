"use client";

import { Library as LibraryIcon, Search, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { GradeBadge } from "@/components/clinical/badges";
import { EmptyState, ErrorState, ListSkeleton, PageBody, PageHeader, Pagination } from "@/components/clinical/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useDocuments } from "@/hooks/use-api";
import { humanStudyType, yearOf } from "@/lib/text";

const LIMIT = 25;
const STUDY_TYPES = [
  "systematic_review",
  "meta_analysis",
  "randomized_controlled_trial",
  "cohort_study",
  "case_control_study",
  "case_series",
  "case_report",
  "clinical_guideline",
  "narrative_review",
  "other",
];
const SOURCES = ["pubmed", "pmc", "guideline", "uploaded"];

export default function LibraryPage() {
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [grade, setGrade] = useState("");
  const [studyType, setStudyType] = useState("");
  const [source, setSource] = useState("");
  const [scope, setScope] = useState<"all" | "shared" | "private">("all");
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    const t = window.setTimeout(() => {
      setDebounced(search.trim());
      setOffset(0);
    }, 250);
    return () => window.clearTimeout(t);
  }, [search]);

  const docs = useDocuments({
    limit: LIMIT,
    offset,
    search: debounced || undefined,
    evidence_grade: grade || undefined,
    study_type: studyType || undefined,
    source_type: source || undefined,
    scope,
  });

  const anyFilter = Boolean(debounced || grade || studyType || source || scope !== "all");

  return (
    <PageBody>
      <PageHeader
        title="Library"
        description={
          docs.data
            ? `${docs.data.total.toLocaleString()} document${docs.data.total === 1 ? "" : "s"} — the shared corpus plus your organisation's private uploads.`
            : "The shared corpus plus your organisation's private uploads."
        }
      />

      <div className="grid gap-2 rounded-lg border bg-card p-3 sm:grid-cols-2 xl:grid-cols-[1fr_auto_auto_auto_auto]">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search titles…"
            aria-label="Search documents by title"
            className="pl-8"
          />
        </div>
        <Select label="Grade" value={grade} onChange={(v) => { setGrade(v); setOffset(0); }} options={["A", "B", "C", "D"].map((g) => [g, `Grade ${g}`])} />
        <Select label="Design" value={studyType} onChange={(v) => { setStudyType(v); setOffset(0); }} options={STUDY_TYPES.map((t) => [t, humanStudyType(t)])} />
        <Select label="Source" value={source} onChange={(v) => { setSource(v); setOffset(0); }} options={SOURCES.map((s) => [s, s === "pmc" ? "PMC" : s.charAt(0).toUpperCase() + s.slice(1)])} />
        <Select
          label="Scope"
          value={scope === "all" ? "" : scope}
          onChange={(v) => { setScope((v || "all") as typeof scope); setOffset(0); }}
          options={[["shared", "Shared corpus"], ["private", "Private uploads"]]}
          allLabel="All"
        />
        {anyFilter && (
          <Button
            variant="ghost"
            size="sm"
            className="sm:col-span-2 sm:justify-self-start xl:col-span-5"
            onClick={() => { setSearch(""); setGrade(""); setStudyType(""); setSource(""); setScope("all"); setOffset(0); }}
          >
            <X /> Clear filters
          </Button>
        )}
      </div>

      {docs.isPending ? (
        <ListSkeleton rows={8} />
      ) : docs.isError ? (
        <ErrorState error={docs.error} onRetry={() => void docs.refetch()} />
      ) : docs.data.documents.length === 0 ? (
        <EmptyState
          icon={LibraryIcon}
          title="No documents match"
          description="Try a broader title search or fewer filters. Private uploads appear here once ingested from Admin."
        />
      ) : (
        <>
          <ol className="divide-y rounded-lg border bg-card" aria-label="Documents">
            {docs.data.documents.map((d) => (
              <li key={d.id}>
                <Link href={`/app/library/${d.id}`} className="grid gap-1 px-4 py-3 hover:bg-accent/50 focus-visible:bg-accent/50 sm:grid-cols-[1fr_auto]">
                  <span className="min-w-0">
                    <span className="line-clamp-2 text-sm font-medium">{d.title}</span>
                    <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                      {[d.journal, yearOf(d.publication_date), humanStudyType(d.study_type)].filter(Boolean).join(" · ")}
                    </span>
                  </span>
                  <span className="flex items-center gap-1.5 self-start">
                    {!d.shared && <Badge variant="outline">Private</Badge>}
                    <Badge variant="secondary" className="font-mono text-[10px] uppercase">{d.source_type}</Badge>
                    <GradeBadge grade={d.evidence_grade as "A" | "B" | "C" | "D" | null} />
                    <span className="font-mono text-[11px] text-muted-foreground">{d.chunk_count} passages</span>
                  </span>
                </Link>
              </li>
            ))}
          </ol>
          <Pagination total={docs.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
        </>
      )}
    </PageBody>
  );
}

function Select({
  label,
  value,
  onChange,
  options,
  allLabel = "Any",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: Array<[string, string]>;
  allLabel?: string;
}) {
  const id = `lib-${label.toLowerCase()}`;
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <Label htmlFor={id} className="w-14 shrink-0 text-xs text-muted-foreground xl:w-auto">
        {label}
      </Label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 min-w-0 flex-1 rounded-md border bg-background px-2 text-sm focus-visible:outline-2 focus-visible:outline-ring xl:max-w-56 xl:flex-none"
      >
        <option value="">{allLabel}</option>
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </div>
  );
}
