"use client";

import { Bell, CheckCheck } from "lucide-react";
import Link from "next/link";

import { EmptyState, ErrorState, ListSkeleton, PageBody, PageHeader } from "@/components/clinical/page";
import { DigestPreferencesCard, DigestSummary } from "@/components/notifications/digest";
import { Button } from "@/components/ui/button";
import { useMarkRead, useNotifications } from "@/hooks/use-api";
import type { Schemas } from "@/lib/domain";
import { formatDate } from "@/lib/text";
import { cn } from "@/lib/utils";

type Notification = Schemas["NotificationOut"];

function describe(n: Notification): { title: string; body: string; href: string | null } {
  const p = n.payload;
  const str = (k: string) => (typeof p[k] === "string" ? (p[k] as string) : null);
  const num = (k: string) => (typeof p[k] === "number" ? (p[k] as number) : null);
  switch (n.type) {
    case "answer_updated":
    case "answer.superseded": {
      const answerId = str("answer_id");
      const version = num("version");
      const reasons = Array.isArray(p.reasons) ? (p.reasons as unknown[]).filter((r): r is string => typeof r === "string") : [];
      return {
        title: `A followed answer has a new version${version ? ` (v${version})` : ""}`,
        body: reasons.length ? reasons.join("; ") : str("query") ?? "The evidence behind an answer you follow has changed.",
        href: answerId ? `/app/answers/${answerId}/versions` : null,
      };
    }
    case "org_invite":
      return { title: "You were invited to an organisation", body: str("org_name") ?? "", href: "/onboarding" };
    case "weekly_digest":
      return { title: "Your weekly evidence digest", body: DigestSummary(p), href: null };
    default:
      return { title: n.type.replace(/[_.]/g, " "), body: str("message") ?? "", href: null };
  }
}

export default function NotificationsPage() {
  const notifications = useNotifications();
  const markRead = useMarkRead();

  return (
    <PageBody>
      <PageHeader
        title="Notifications"
        description="Living Answer updates and organisation events."
        actions={
          notifications.data && notifications.data.unread > 0 ? (
            <Button size="sm" variant="outline" onClick={() => markRead.mutate("all")} disabled={markRead.isPending}>
              <CheckCheck /> Mark all read
            </Button>
          ) : undefined
        }
      />
      <DigestPreferencesCard />
      {notifications.isPending ? (
        <ListSkeleton rows={4} />
      ) : notifications.isError ? (
        <ErrorState error={notifications.error} onRetry={() => void notifications.refetch()} />
      ) : notifications.data.notifications.length === 0 ? (
        <EmptyState
          icon={Bell}
          title="Nothing yet"
          description="Follow an answer (the bell icon on any answer) and you'll be told here when the evidence behind it changes."
        />
      ) : (
        <ol className="divide-y rounded-lg border bg-card" aria-label="Notifications">
          {notifications.data.notifications.map((n) => {
            const d = describe(n);
            const unread = !n.read_at;
            const inner = (
              <>
                <span aria-hidden className={cn("mt-1.5 size-2 shrink-0 rounded-full", unread ? "bg-primary" : "bg-transparent")} />
                <span className="min-w-0 flex-1">
                  <span className={cn("block text-sm", unread && "font-medium")}>{d.title}</span>
                  {d.body && <span className="block text-xs text-muted-foreground">{d.body}</span>}
                </span>
                <time dateTime={n.created_at} className="shrink-0 font-mono text-[11px] text-muted-foreground">
                  {formatDate(n.created_at, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                </time>
              </>
            );
            const className = "flex items-start gap-3 px-4 py-3 hover:bg-accent/50 focus-visible:bg-accent/50";
            return (
              <li key={n.id}>
                {d.href ? (
                  <Link href={d.href} className={className} onClick={() => unread && markRead.mutate(n.id)}>
                    {inner}
                  </Link>
                ) : (
                  <button type="button" className={cn(className, "w-full text-left")} onClick={() => unread && markRead.mutate(n.id)}>
                    {inner}
                  </button>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </PageBody>
  );
}
