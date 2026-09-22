import { FileCheck2, ShieldCheck, Scale } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

/**
 * The frame every sign-in screen sits in.
 *
 * Signing in is the first thing anyone sees, so it carries the same claim the
 * marketing page makes rather than floating a bare card on an empty
 * background: what the product promises, and the three constraints that make
 * the promise credible. On a narrow screen the panel collapses to the
 * wordmark — the form is what matters there, and it stays above the fold.
 *
 * Restraint, per the design system: no gradients, one accent, the same
 * neutral surfaces as the app behind it.
 */

const PROMISES = [
  {
    icon: FileCheck2,
    title: "Every claim is cited",
    body: "Each sentence traces to a passage in the public medical literature, one click away.",
  },
  {
    icon: Scale,
    title: "Disagreement is shown, not buried",
    body: "When sources conflict, the answer says so and puts both sides next to each other.",
  },
  {
    icon: ShieldCheck,
    title: "No patient data, ever",
    body: "PHI is refused before a model is called — enforced in code, not promised in a footer.",
  },
] as const;

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      {/* The claim. Decorative on small screens, so it is simply not rendered. */}
      <aside className="relative hidden flex-col justify-between border-r bg-sidebar p-10 lg:flex xl:p-14">
        <Link href="/" className="inline-flex items-center gap-2.5 self-start rounded-sm">
          <span className="grid size-7 place-items-center rounded-sm bg-primary font-mono text-xs font-bold text-primary-foreground">
            CC
          </span>
          <span className="font-semibold tracking-tight">ClinicalContext</span>
        </Link>

        <div className="max-w-md">
          <h1 className="text-balance text-3xl font-semibold tracking-tight xl:text-4xl">
            Evidence-grounded answers to clinical questions.
          </h1>
          <p className="mt-4 text-pretty text-muted-foreground">
            Every claim traceable to a citation in the public medical literature — and explicit
            uncertainty when the evidence is weak or contradictory.
          </p>

          <ul className="mt-10 flex flex-col gap-6">
            {PROMISES.map(({ icon: Icon, title, body }) => (
              <li key={title} className="flex gap-3.5">
                <Icon className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
                <div>
                  <p className="text-sm font-medium">{title}</p>
                  <p className="mt-1 text-sm text-pretty text-muted-foreground">{body}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <p className="max-w-md text-xs text-muted-foreground">
          A clinical decision <em>support</em> tool, not a diagnostic tool. It does not diagnose,
          prescribe, or replace clinical judgement.
        </p>
      </aside>

      <main className="flex min-h-screen flex-col items-center justify-center gap-8 px-4 py-10 sm:px-6">
        <Link href="/" className="inline-flex items-center gap-2.5 rounded-sm lg:hidden">
          <span className="grid size-7 place-items-center rounded-sm bg-primary font-mono text-xs font-bold text-primary-foreground">
            CC
          </span>
          <span className="font-semibold tracking-tight">ClinicalContext</span>
        </Link>
        {children}
      </main>
    </div>
  );
}
