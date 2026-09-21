import Link from "next/link";

import { Button } from "@/components/ui/button";

/** A clean 404 — the same page whether the link never existed, was revoked, or its org disabled sharing. */
export default function AnswerNotFound() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-3 p-6 text-center">
      <span className="grid size-8 place-items-center rounded-sm bg-primary font-mono text-xs font-bold text-primary-foreground">CC</span>
      <h1 className="text-lg font-semibold tracking-tight">This answer isn&apos;t available</h1>
      <p className="max-w-sm text-sm text-muted-foreground">
        The link may have been revoked, or the organisation that shared it may have turned public sharing off.
      </p>
      <Button size="sm" variant="outline" nativeButton={false} render={<Link href="/" />}>
        Go to ClinicalContext
      </Button>
    </main>
  );
}
