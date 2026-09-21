"use client";

import { useEffect, useState } from "react";

import { formatDate } from "@/lib/text";

/**
 * A timestamp that hydrates cleanly on server-rendered pages.
 *
 * Locale and timezone differ between the server and the viewer, so the same
 * `toLocaleString` call produces different text and React reports a
 * hydration mismatch. Render a deterministic UTC string first, then swap to
 * the viewer's local format once mounted.
 */
export function LocalTime({
  iso,
  options,
  className,
}: {
  iso: string | null | undefined;
  options?: Intl.DateTimeFormatOptions;
  className?: string;
}) {
  const key = JSON.stringify(options ?? null);
  const [text, setText] = useState(() => `${formatDate(iso, { ...(options ?? {}), timeZone: "UTC" })} UTC`);
  useEffect(() => {
    setText(formatDate(iso, options));
    // options is compared by value via `key`; a fresh object per render must not re-run this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [iso, key]);
  if (!iso) return <span className={className}>—</span>;
  return (
    <time dateTime={iso} className={className} suppressHydrationWarning>
      {text}
    </time>
  );
}
