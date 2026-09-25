"use client";

import { ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function PageHeader({
  title,
  description,
  actions,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-wrap items-end justify-between gap-3", className)}>
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && (
        <div className="scrollbar-thin -mx-1 flex max-w-[calc(100%+0.5rem)] items-center gap-2 overflow-x-auto px-1 pb-1 sm:mx-0 sm:max-w-full sm:px-0 sm:pb-0">
          {actions}
        </div>
      )}
    </div>
  );
}

export function PageBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("mx-auto flex max-w-6xl flex-col gap-4 p-4 md:p-6", className)}>{children}</div>;
}

/** Empty states teach: what this is, and the one thing to do next. */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon?: typeof RefreshCw;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center gap-2 rounded-lg border border-dashed px-6 py-12 text-center", className)}>
      {Icon && <Icon className="size-6 text-muted-foreground" aria-hidden />}
      <p className="text-sm font-medium">{title}</p>
      {description && <p className="max-w-md text-sm text-muted-foreground">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  className,
}: {
  error: unknown;
  onRetry?: () => void;
  className?: string;
}) {
  const message = error instanceof Error ? error.message : "Something went wrong.";
  const status = (error as { status?: number }).status;
  return (
    <div role="alert" className={cn("rounded-lg border border-destructive/40 bg-destructive/5 p-4", className)}>
      <p className="text-sm font-medium">
        {status === 403 ? "You don't have access to this." : status === 404 ? "Not found." : "Couldn't load this."}
      </p>
      <p className="text-sm text-muted-foreground">{message}</p>
      {onRetry && (
        <Button size="sm" variant="outline" className="mt-3" onClick={onRetry}>
          <RefreshCw /> Try again
        </Button>
      )}
    </div>
  );
}

export function ListSkeleton({ rows = 6, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("space-y-2", className)} aria-busy aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-14 w-full" />
      ))}
    </div>
  );
}

export function Pagination({
  total,
  limit,
  offset,
  onChange,
  className,
}: {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
  className?: string;
}) {
  if (total <= limit) return null;
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.ceil(total / limit);
  return (
    <nav className={cn("flex items-center justify-between text-sm text-muted-foreground", className)} aria-label="Pagination">
      <span>
        {offset + 1}–{Math.min(offset + limit, total)} of {total.toLocaleString()}
      </span>
      <div className="flex items-center gap-1">
        <Button variant="outline" size="icon-sm" disabled={page <= 1} onClick={() => onChange(Math.max(0, offset - limit))} aria-label="Previous page">
          <ChevronLeft />
        </Button>
        <span className="px-2 font-mono text-xs">
          {page} / {pages}
        </span>
        <Button variant="outline" size="icon-sm" disabled={page >= pages} onClick={() => onChange(offset + limit)} aria-label="Next page">
          <ChevronRight />
        </Button>
      </div>
    </nav>
  );
}
