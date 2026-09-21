"use client";

import { Mail, Newspaper } from "lucide-react";

import { Switch } from "@/components/ui/switch";
import { useDigestPreferences, useUpdateDigestPreferences } from "@/hooks/use-api";
import { formatDate } from "@/lib/text";

/**
 * The weekly evidence digest (Phase 13): an opt-in, per user. In-app when
 * on; an email copy only when the user asks for one. The card shows when
 * the last one went out, straight from the preferences row.
 */
export function DigestPreferencesCard() {
  const prefs = useDigestPreferences();
  const update = useUpdateDigestPreferences();
  const data = prefs.data;
  const busy = update.isPending;

  return (
    <section className="rounded-lg border bg-card p-4" aria-labelledby="digest-heading" data-testid="digest-preferences">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 id="digest-heading" className="flex items-center gap-2 text-sm font-medium">
            <Newspaper className="size-4" aria-hidden /> Weekly evidence digest
          </h2>
          <p className="max-w-prose text-xs text-muted-foreground">
            Once a week: new evidence in the topics you have asked about, changes to answers you asked or follow, and
            your organisation&rsquo;s week. Off by default; nothing is sent in a week with nothing to say.
          </p>
          {data?.last_sent_at && (
            <p className="text-xs text-muted-foreground">
              Last sent {formatDate(data.last_sent_at, { month: "short", day: "numeric", year: "numeric" })}.
            </p>
          )}
        </div>
        <div className="flex flex-col gap-2">
          <label className="flex items-center justify-between gap-3 text-sm">
            <span>In-app digest</span>
            <Switch
              checked={data?.enabled ?? false}
              disabled={!data || busy}
              onCheckedChange={(enabled) => update.mutate({ enabled })}
              aria-label="Weekly in-app digest"
            />
          </label>
          <label className="flex items-center justify-between gap-3 text-sm">
            <span className="flex items-center gap-1.5">
              <Mail className="size-3.5 text-muted-foreground" aria-hidden /> Email copy
            </span>
            <Switch
              checked={data?.email ?? false}
              disabled={!data || busy || !data.enabled}
              onCheckedChange={(email) => update.mutate({ email })}
              aria-label="Email copy of the weekly digest"
            />
          </label>
        </div>
      </div>
      {update.isError && (
        <p className="mt-2 text-xs text-destructive" role="alert">
          The preference could not be saved: {update.error.message}
        </p>
      )}
    </section>
  );
}

/** One line for the notification list, from the digest payload. */
export function DigestSummary(payload: Record<string, unknown>): string {
  const docs = Array.isArray(payload.new_documents) ? payload.new_documents.length : 0;
  const changes = Array.isArray(payload.answer_changes) ? payload.answer_changes.length : 0;
  const usage = (payload.usage ?? {}) as Record<string, unknown>;
  const queries = typeof usage.queries === "number" ? usage.queries : 0;
  const topics = Array.isArray(payload.topics) ? (payload.topics as unknown[]).filter((t): t is string => typeof t === "string") : [];
  const parts = [
    `${docs} new ${docs === 1 ? "document" : "documents"} in your topics`,
    `${changes} answer ${changes === 1 ? "change" : "changes"}`,
    `${queries} ${queries === 1 ? "question" : "questions"} asked by your organisation`,
  ];
  return topics.length ? `${parts.join(" · ")} — topics: ${topics.slice(0, 5).join(", ")}` : parts.join(" · ");
}
