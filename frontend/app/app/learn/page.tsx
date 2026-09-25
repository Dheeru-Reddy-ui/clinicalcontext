"use client";

import { GraduationCap, Search, Star } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { Segmented } from "@/components/settings/ui";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useSpecialties } from "@/hooks/use-api";
import { usePreferences, useUpdatePreferences, type Preferences } from "@/hooks/use-preferences";
import type { Schemas } from "@/lib/domain";

const LEVEL_ORDER = ["preclinical", "paraclinical", "clinical", "pg_broad", "pg_super"];
const MBBS_LEVELS = new Set(["preclinical", "paraclinical", "clinical"]);

const SCOPES = [
  { value: "all", label: "All" },
  { value: "mbbs", label: "MBBS" },
  { value: "pg", label: "PG" },
] as const;

function inScope(level: string, scope: Preferences["learn_scope"]): boolean {
  if (scope === "all") return true;
  return scope === "mbbs" ? MBBS_LEVELS.has(level) : !MBBS_LEVELS.has(level);
}

function SpecialtyCard({ specialty, followed }: { specialty: Schemas["SpecialtyOut"]; followed: boolean }) {
  return (
    <Link
      href={`/app/learn/${specialty.slug}`}
      className="group flex flex-col gap-2 rounded-xl border bg-card p-4 transition-colors hover:border-primary/40 hover:bg-muted/30"
      data-testid={`specialty-${specialty.slug}`}
    >
      <span className="flex items-center gap-1.5 font-medium group-hover:text-primary">
        {specialty.name}
        {followed && <Star className="size-3.5 fill-amber-400 text-amber-400" aria-label="One of your subjects" />}
      </span>
      <span className="line-clamp-2 text-xs text-muted-foreground">{specialty.topics.slice(0, 4).join(" · ")}</span>
    </Link>
  );
}

/**
 * Learn: every MBBS subject and PG specialty, each with a teaching assistant,
 * its core topics, and what PubMed indexed this month.
 */
export default function LearnPage() {
  const specialties = useSpecialties();
  const [filter, setFilter] = useState("");
  // Settings → Learn: which subjects to list, and the ones pinned to the top.
  // The switch here writes the same preference.
  const { preferences, available } = usePreferences();
  const update = useUpdatePreferences();
  const scope = preferences.learn_scope;
  const followed = useMemo(() => new Set(preferences.followed_specialties), [preferences.followed_specialties]);

  const { mine, groups } = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const items = (specialties.data ?? []).filter(
      (s) =>
        !needle ||
        s.name.toLowerCase().includes(needle) ||
        s.topics.some((t) => t.toLowerCase().includes(needle)),
    );
    const listed = items.filter((s) => inScope(s.level, scope));
    return {
      mine: items.filter((s) => followed.has(s.slug)),
      groups: LEVEL_ORDER.map((level) => ({
        level,
        label: listed.find((s) => s.level === level)?.level_label ?? level,
        items: listed.filter((s) => s.level === level),
      })).filter((g) => g.items.length),
    };
  }, [specialties.data, filter, scope, followed]);

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
        <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
          <Segmented
            label="Subjects to list"
            value={scope}
            choices={SCOPES}
            onChange={(learn_scope) => update.mutate({ learn_scope })}
            disabled={!available}
            testId="learn-scope"
          />
          <div className="relative min-w-0 flex-1 sm:w-64 sm:flex-none">
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
        </div>
      </header>

      {mine.length > 0 && (
        <section aria-labelledby="level-mine" className="flex flex-col gap-3" data-testid="learn-mine">
          <h2 id="level-mine" className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            Your subjects
          </h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {mine.map((s) => (
              <SpecialtyCard key={s.slug} specialty={s} followed />
            ))}
          </div>
        </section>
      )}

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
            {group.items
              .filter((s) => !followed.has(s.slug))
              .map((s) => (
                <SpecialtyCard key={s.slug} specialty={s} followed={false} />
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
