"use client";

import { GraduationCap, Search } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useSpecialties } from "@/hooks/use-api";

const LEVEL_ORDER = ["preclinical", "paraclinical", "clinical", "pg_broad", "pg_super"];

/**
 * Learn: every MBBS subject and PG specialty, each with a teaching assistant,
 * its core topics, and what PubMed indexed this month.
 */
export default function LearnPage() {
  const specialties = useSpecialties();
  const [filter, setFilter] = useState("");

  const groups = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const items = (specialties.data ?? []).filter(
      (s) =>
        !needle ||
        s.name.toLowerCase().includes(needle) ||
        s.topics.some((t) => t.toLowerCase().includes(needle)),
    );
    return LEVEL_ORDER.map((level) => ({
      level,
      label: items.find((s) => s.level === level)?.level_label ?? level,
      items: items.filter((s) => s.level === level),
    })).filter((g) => g.items.length);
  }, [specialties.data, filter]);

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6 p-4 md:p-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
            <GraduationCap className="size-5 text-primary" aria-hidden /> Learn
          </h1>
          <p className="text-sm text-muted-foreground">
            For doctors, residents and students: every MBBS subject and PG specialty, taught from
            guidelines and the newest research.
          </p>
        </div>
        <div className="relative w-full max-w-xs">
          <Search className="absolute top-2 left-2.5 size-4 text-muted-foreground" aria-hidden />
          <Input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Find a subject or topic…"
            className="pl-8"
            aria-label="Find a subject or topic"
            data-testid="learn-filter"
          />
        </div>
      </header>

      {specialties.isLoading && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
      )}

      {groups.map((group) => (
        <section key={group.level} aria-labelledby={`level-${group.level}`} className="flex flex-col gap-3">
          <h2 id={`level-${group.level}`} className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            {group.label}
          </h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {group.items.map((s) => (
              <Link
                key={s.slug}
                href={`/app/learn/${s.slug}`}
                className="group flex flex-col gap-2 rounded-xl border bg-card p-4 transition-colors hover:border-primary/40 hover:bg-muted/30"
                data-testid={`specialty-${s.slug}`}
              >
                <span className="font-medium group-hover:text-primary">{s.name}</span>
                <span className="line-clamp-2 text-xs text-muted-foreground">{s.topics.slice(0, 4).join(" · ")}</span>
              </Link>
            ))}
          </div>
        </section>
      ))}

      {!specialties.isLoading && groups.length === 0 && (
        <p className="text-sm text-muted-foreground">Nothing matches “{filter}”. Ask about it in Chat instead.</p>
      )}
    </div>
  );
}
