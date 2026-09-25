import { ArrowRight, HeartPulse, Stethoscope } from "lucide-react";
import Link from "next/link";

import { AssistantLauncher } from "@/components/chat/assistant-launcher";
import { AskAssistantButton } from "@/components/landing/ask-button";
import { Hero3D } from "@/components/landing/hero-3d";
import {
  AudienceChip,
  CheckedPill,
  SourcesCard,
  SymptomCheckPhone,
  TimelineCard,
} from "@/components/landing/hero-cards";
import {
  Audiences,
  FeatureGrid,
  HeroBackdrop,
  PrinciplesAndFooter,
  SectionBadge,
  SiteNav,
} from "@/components/landing/sections";
import { DemoPanel } from "@/components/marketing/demo-panel";
import { apiUrl } from "@/lib/api";
import type { Schemas } from "@/lib/domain";

/**
 * The public face of the product: a night-sky hero with a 3D medical still
 * life (components/landing/hero-3d), what the product does, who it is for,
 * a live answer from the real pipeline with no login, and how it stays
 * honest. The health assistant is one click away from anywhere on it.
 */

/**
 * Bounded: this page must never wait on the API. A sleeping free-tier
 * instance takes up to two minutes to wake, and an unbounded await here
 * held the whole response — no headers, no bytes — for that long, which
 * looks like an outage. Past the deadline the panel takes over on the
 * client: it wakes the API itself and says so.
 */
const SERVER_FETCH_DEADLINE_MS = 3_000;

async function loadDemoQuestions(): Promise<Schemas["DemoQuestion"][]> {
  try {
    const response = await fetch(apiUrl("/api/public/demo/questions"), {
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(SERVER_FETCH_DEADLINE_MS),
    });
    if (!response.ok) return [];
    return ((await response.json()) as Schemas["DemoQuestionsOut"]).questions;
  } catch {
    return [];
  }
}

export default async function Home() {
  const questions = await loadDemoQuestions();
  return (
    <div className="min-h-screen bg-[#f3f4fb] text-slate-900 dark:bg-[#060816] dark:text-white">
      <a
        href="#main"
        className="sr-only z-50 rounded-full bg-white px-4 py-2 text-sm font-medium text-slate-900 focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
      >
        Skip to content
      </a>

      <section
        data-hero
        aria-labelledby="hero-heading"
        className="hero-sky relative isolate mx-2 mt-2 overflow-hidden rounded-[28px] pb-6 text-white sm:mx-3 sm:mt-3 lg:rounded-[36px]"
      >
        <HeroBackdrop />
        <Hero3D />
        <SiteNav />

        {/* Who it answers for, drifting at the edges on wide screens. */}
        <div aria-hidden className="pointer-events-none absolute inset-0 z-[15] hidden xl:block">
          <AudienceChip audience="patients" depth={-18} className="absolute top-[31%] left-[4.5%]" />
          <AudienceChip audience="doctors" depth={14} className="absolute top-[18%] right-[4.5%]" />
          <AudienceChip audience="students" depth={-10} className="absolute top-[41%] right-[6.5%]" />
        </div>

        <div id="main" className="relative z-10 mx-auto flex max-w-6xl flex-col items-center px-5 pt-12 text-center sm:px-8 sm:pt-16">

          <span className="glass-card inline-flex items-center gap-2 rounded-full py-1 pr-4 pl-1 text-[13px] font-medium text-indigo-50">
            <span className="grid size-6 place-items-center rounded-full bg-[linear-gradient(135deg,#2dd4bf,#6366f1)]">
              <HeartPulse className="size-3.5" aria-hidden />
            </span>
            Evidence-grounded health assistant
          </span>

          <h1
            id="hero-heading"
            className="mt-6 max-w-4xl text-balance text-[clamp(2.4rem,6.2vw,4.6rem)] leading-[1.03] font-semibold tracking-[-0.045em]"
          >
            Clear medical answers,
            <span className="text-hero-gradient block pb-1">traced to the evidence.</span>
          </h1>

          <p className="mt-5 max-w-2xl text-pretty text-base leading-7 text-indigo-100/80 sm:text-lg sm:leading-8">
            Ask any health question — as a patient, a doctor or a student. Answers are written from medical research,
            guidelines and drug labels, cite a source for every claim, and say so plainly when the evidence is weak.
          </p>

          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <AskAssistantButton className="group inline-flex items-center gap-3 rounded-full bg-white py-1.5 pr-1.5 pl-6 text-[15px] font-semibold text-[#141a4a] shadow-[0_18px_40px_-16px_rgb(255_255_255/0.75)] transition-transform hover:-translate-y-0.5">
              Ask a health question
              <span className="grid size-9 place-items-center rounded-full bg-[linear-gradient(135deg,#6366f1,#7c3aed)] text-white transition-transform group-hover:translate-x-0.5">
                <ArrowRight className="size-4" aria-hidden />
              </span>
            </AskAssistantButton>
            <Link
              href="/check"
              data-testid="home-check"
              className="glass-card inline-flex items-center gap-2 rounded-full px-6 py-3 text-[15px] font-semibold text-white transition-colors hover:bg-white/15"
            >
              <Stethoscope className="size-4 text-teal-200" aria-hidden />
              Start a symptom check
            </Link>
          </div>
          <p className="mt-4 text-[13px] text-indigo-200/70">Free · No account needed · Nothing you type is stored</p>
        </div>

        {/* The stage: the 3D centrepiece sits in the middle of this box. The cards
            are pictures of the product, described properly further down the page,
            so assistive technology skips them. */}
        <div data-hero-anchor aria-hidden className="relative z-20 mx-auto mt-2 h-[380px] w-full max-w-6xl sm:h-[440px] lg:h-[480px]">
          <div className="absolute top-[3%] left-[4%] hidden lg:block xl:left-[8%]">
            <SymptomCheckPhone />
          </div>
          <div className="absolute top-[4%] left-[3%] hidden md:block lg:top-[2%] lg:right-[3%] lg:left-auto xl:right-[7%]">
            <SourcesCard />
          </div>
          <div className="absolute right-[3%] bottom-[8%] hidden md:block lg:right-[9%] xl:right-[13%]">
            <TimelineCard />
          </div>
          <div className="absolute inset-x-0 bottom-0 flex justify-center px-4">
            <CheckedPill />
          </div>
        </div>
      </section>

      <FeatureGrid />

      <section id="demo" aria-labelledby="demo-section-heading" className="mx-auto max-w-5xl scroll-mt-8 px-4 py-16 sm:px-8">
        <div className="mx-auto max-w-2xl text-center">
          <SectionBadge>Live demo</SectionBadge>
          <h2
            id="demo-section-heading"
            className="mt-5 text-balance text-3xl font-semibold tracking-[-0.03em] text-slate-900 sm:text-[2.6rem] sm:leading-[1.1] dark:text-white"
          >
            See a real answer, no sign-in.
          </h2>
          <p className="mt-4 text-pretty text-slate-600 dark:text-slate-300">
            These questions run through the real pipeline as a public tenant. Pick the one that shows a contradiction: a
            disagreement between sources is never buried.
          </p>
        </div>
        <div className="mt-10 rounded-[26px] border border-white bg-white/60 p-1.5 shadow-[0_30px_70px_-40px_rgb(30_27_75/0.55)] backdrop-blur sm:rounded-[30px] sm:p-2 dark:border-white/10 dark:bg-white/[0.03]">
          <DemoPanel
            questions={questions}
            labelledBy="demo-section-heading"
            className="rounded-[22px] border-slate-200/70 p-4 sm:rounded-[24px] sm:p-5 md:p-7 dark:border-white/10"
          />
        </div>
      </section>

      <Audiences />
      <PrinciplesAndFooter />
      <AssistantLauncher mode="public" checkHref="/check" />
    </div>
  );
}
