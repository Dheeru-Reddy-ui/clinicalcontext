"use client";

import { Compass, ShieldAlert, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Spec #6: distinct, calm screens for a PHI block and an out-of-scope refusal;
 * a prominent, unmissable banner for a red-flag escalation. None of these
 * are error states — they are the system doing its job — so none of them use
 * the destructive palette.
 */

export function PhiBlockedView({ onReset, className }: { onReset: () => void; className?: string }) {
  return (
    <section
      className={cn("rounded-lg border border-guard-calm-border bg-guard-calm-bg p-5 text-guard-calm-fg", className)}
      role="status"
      aria-labelledby="phi-heading"
    >
      <div className="flex items-start gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-md bg-background/60">
          <ShieldCheck className="size-5" aria-hidden />
        </span>
        <div className="space-y-2">
          <h2 id="phi-heading" className="text-base font-semibold">
            This looked like it contained patient information, so it wasn&apos;t sent.
          </h2>
          <p className="text-sm leading-6 opacity-90">
            ClinicalContext answers questions about the literature, not about individual patients.
            Names, dates of birth, record numbers and similar identifiers are stopped here, before
            anything is retrieved or stored.
          </p>
          <p className="text-sm leading-6 opacity-90">
            Rephrase as a general clinical question — for example, describe the patient as
            <em> “a 67-year-old with non-valvular atrial fibrillation”</em> rather than by name.
          </p>
          <Button size="sm" variant="outline" onClick={onReset} className="mt-1">
            Ask a different question
          </Button>
        </div>
      </div>
    </section>
  );
}

export function ScopeBlockedView({
  message,
  onReset,
  className,
}: {
  message: string;
  onReset: () => void;
  className?: string;
}) {
  return (
    <section
      className={cn("rounded-lg border bg-muted/40 p-5", className)}
      role="status"
      aria-labelledby="scope-heading"
    >
      <div className="flex items-start gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-md bg-background text-muted-foreground">
          <Compass className="size-5" aria-hidden />
        </span>
        <div className="space-y-2">
          <h2 id="scope-heading" className="text-base font-semibold">
            That&apos;s outside what this tool can answer.
          </h2>
          <p className="text-sm leading-6 text-muted-foreground">{message}</p>
          <p className="text-sm leading-6 text-muted-foreground">
            It covers questions that can be answered from published clinical evidence: therapy,
            diagnosis, prognosis, harm, and guideline recommendations.
          </p>
          <Button size="sm" variant="outline" onClick={onReset} className="mt-1">
            Ask a clinical question
          </Button>
        </div>
      </div>
    </section>
  );
}

/** Non-blocking: shown *above* the answer, prominent and unmissable. */
export function RedFlagBanner({ message, className }: { message: string; className?: string }) {
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-3 rounded-lg border-2 border-redflag bg-redflag-bg p-4 text-redflag-fg shadow-md",
        className,
      )}
    >
      <ShieldAlert className="mt-0.5 size-6 shrink-0" aria-hidden />
      <div>
        <p className="text-base font-bold uppercase tracking-wide">Red flag — escalate now</p>
        <p className="mt-1 text-sm leading-6">{message}</p>
        <p className="mt-1 text-sm leading-6 opacity-90">
          The literature answer below does not replace emergency assessment.
        </p>
      </div>
    </div>
  );
}
