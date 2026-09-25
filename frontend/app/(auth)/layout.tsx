import { FileCheck2, ShieldCheck, Scale } from "lucide-react";
import Link from "next/link";
import type { CSSProperties, ReactNode } from "react";

import { Logo } from "@/components/brand/logo";
import { HeroStill } from "@/components/landing/hero-still";

/**
 * The frame every sign-in screen sits in.
 *
 * Signing in is the first thing anyone sees after the landing page, so it
 * carries the same night-sky panel — beams, ECG-paper grid, the medical
 * still life — and the three constraints that make the product's promise
 * credible. The still life is the flat illustration, never the WebGL scene:
 * signing in should cost nothing. On a narrow screen the panel collapses to
 * the wordmark — the form is what matters there, and it stays above the fold.
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

const BEAMS = [
  { x: "8%", width: 90, alpha: 0.18, blur: 26, period: 11, delay: -2 },
  { x: "58%", width: 70, alpha: 0.14, blur: 20, period: 9, delay: -5 },
  { x: "82%", width: 120, alpha: 0.22, blur: 32, period: 13, delay: -1 },
] as const;

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-[#f5f6fb] lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] dark:bg-background">
      {/* The claim. Decorative on small screens, so it is simply not rendered. */}
      <aside className="hero-sky relative isolate m-3 hidden flex-col justify-between overflow-hidden rounded-[32px] p-10 text-white lg:flex xl:p-14">
        <div aria-hidden className="pointer-events-none absolute inset-0 -z-10 overflow-hidden">
          <div className="hero-grid absolute inset-0" />
          {BEAMS.map((beam) => (
            <span
              key={beam.x}
              className="hero-beam"
              style={
                {
                  "--beam-x": beam.x,
                  "--beam-width": `${beam.width}px`,
                  "--beam-alpha": beam.alpha,
                  "--beam-blur": `${beam.blur}px`,
                  "--beam-period": `${beam.period}s`,
                  "--beam-delay": `${beam.delay}s`,
                } as CSSProperties
              }
            />
          ))}
          {/* Top right, clear of the text: the headline and the promises sit left of it. */}
          <div className="absolute -top-20 -right-32 size-[340px] opacity-95 xl:-right-24">
            <HeroStill anchor={{ x: 0.5, y: 0.5 }} className="w-[340px]" />
          </div>
        </div>

        <Link href="/" className="self-start rounded-lg text-white">
          <Logo markClassName="size-8" />
        </Link>

        <div className="max-w-md">
          <h1 className="text-balance text-3xl font-semibold tracking-[-0.03em] xl:text-4xl">
            Evidence-grounded answers to <span className="text-hero-gradient">clinical questions.</span>
          </h1>
          <p className="mt-4 text-pretty text-indigo-100/80">
            Every claim traceable to a citation in the public medical literature — and explicit
            uncertainty when the evidence is weak or contradictory.
          </p>

          <ul className="mt-9 flex flex-col gap-3">
            {PROMISES.map(({ icon: Icon, title, body }) => (
              <li key={title} className="glass-card flex gap-3.5 rounded-2xl p-4">
                <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-white/10 shadow-[inset_0_1px_0_rgb(255_255_255/0.2)]">
                  <Icon className="size-4 text-teal-200" aria-hidden />
                </span>
                <div>
                  <p className="text-sm font-medium">{title}</p>
                  <p className="mt-1 text-sm text-pretty text-indigo-100/75">{body}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <p className="max-w-md text-xs text-indigo-100/60">
          A clinical decision <em>support</em> tool, not a diagnostic tool. It does not diagnose,
          prescribe, or replace clinical judgement.
        </p>
      </aside>

      {/* Every auth form is a Card: lift it off a faint lavender page, like the landing's cards. */}
      <main className="flex min-h-screen flex-col items-center justify-center gap-8 px-4 py-10 sm:px-6 [&_[data-slot=card]]:rounded-2xl [&_[data-slot=card]]:shadow-[0_24px_60px_-32px_rgb(30_27_75/0.45)] [&_[data-slot=card]]:[--card-spacing:--spacing(6)]">
        <Link href="/" className="rounded-lg lg:hidden">
          <Logo markClassName="size-8" />
        </Link>
        {children}
      </main>
    </div>
  );
}
