import Link from "next/link";

import { LogoMark } from "@/components/brand/logo";
import { Button } from "@/components/ui/button";

/** A clean 404 — the same page whether the link never existed, was revoked, or its org disabled sharing. */
export default function AnswerNotFound() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-3 p-6 text-center">
      <LogoMark className="size-9" />
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
