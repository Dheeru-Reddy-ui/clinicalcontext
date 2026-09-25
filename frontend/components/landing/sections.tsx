import {
  ArrowRight,
  AudioLines,
  BadgeCheck,
  ChartScatter,
  Check,
  FlaskConical,
  Gauge,
  GraduationCap,
  HeartPulse,
  Menu,
  MessageCircleHeart,
  Pill,
  ShieldCheck,
  Siren,
  Stethoscope,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import type { CSSProperties, ReactNode } from "react";

import { Logo } from "@/components/brand/logo";
import { TiltCard } from "@/components/landing/tilt-card";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------ small parts */

/** The small pill above a section heading ("Features", "Live demo"). */
export function SectionBadge({ children, tone = "light" }: { children: ReactNode; tone?: "light" | "dark" }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 rounded-full px-3.5 py-1.5 text-xs font-semibold tracking-wide",
        tone === "light"
          ? "border border-indigo-200/80 bg-white text-indigo-700 shadow-[0_6px_16px_-10px_rgb(79_70_229/0.5)] dark:border-white/10 dark:bg-white/5 dark:text-indigo-200"
          : "glass-card text-indigo-100",
      )}
    >
      <span className="size-1.5 rounded-full bg-[linear-gradient(135deg,#2dd4bf,#6366f1)]" aria-hidden />
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ hero */

const BEAMS = [
  { x: "4%", width: 70, alpha: 0.16, blur: 22, period: 10, delay: -1 },
  { x: "14%", width: 130, alpha: 0.22, blur: 34, period: 12, delay: -4 },
  { x: "27%", width: 56, alpha: 0.13, blur: 16, period: 9, delay: -2 },
  { x: "66%", width: 56, alpha: 0.13, blur: 16, period: 11, delay: -6 },
  { x: "76%", width: 140, alpha: 0.24, blur: 36, period: 13, delay: -3 },
  { x: "90%", width: 80, alpha: 0.18, blur: 24, period: 10, delay: -5 },
] as const;

/** Light and paper behind the hero: beams, ECG grid, glows and a heartbeat trace. */
export function HeroBackdrop() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 z-0 overflow-hidden">
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
      {/* A soft spotlight from above onto the headline. */}
      <div className="absolute top-[-18%] left-1/2 h-[70%] w-[62%] -translate-x-1/2 rounded-full bg-[radial-gradient(closest-side,rgb(129_140_248/0.28),transparent)] blur-2xl" />
      {/* The heartbeat, passing behind the centrepiece. */}
      <svg
        className="absolute left-1/2 h-40 w-[1600px] -translate-x-1/2 -translate-y-1/2 opacity-80"
        style={{ top: "var(--anchor-y, 74%)" }}
        viewBox="0 0 1600 160"
        fill="none"
      >
        <defs>
          <linearGradient id="cc-ecg-fade" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0" stopColor="#2dd4bf" stopOpacity="0" />
            <stop offset="0.3" stopColor="#2dd4bf" stopOpacity="0.5" />
            <stop offset="0.5" stopColor="#5eead4" stopOpacity="0.9" />
            <stop offset="0.7" stopColor="#818cf8" stopOpacity="0.5" />
            <stop offset="1" stopColor="#818cf8" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path
          d="M0 92 H560 l16 -8 l14 8 H640 l10 16 l16 -92 l18 118 l12 -42 H760 q22 -26 44 0 H1600"
          stroke="url(#cc-ecg-fade)"
          strokeWidth="1.5"
          opacity="0.35"
        />
        <path
          className="ecg-trace"
          d="M0 92 H560 l16 -8 l14 8 H640 l10 16 l16 -92 l18 118 l12 -42 H760 q22 -26 44 0 H1600"
          stroke="url(#cc-ecg-fade)"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ filter: "drop-shadow(0 0 6px rgb(45 212 191 / 0.9))" }}
        />
      </svg>
      {/* Where the hero meets the page: a violet horizon. */}
      <div className="absolute inset-x-[8%] -bottom-40 h-72 rounded-[100%] bg-[radial-gradient(closest-side,rgb(139_92_246/0.55),transparent)] blur-3xl" />
    </div>
  );
}

const NAV_LINKS = [
  { href: "#features", label: "Features" },
  { href: "#demo", label: "Live demo" },
  { href: "/check", label: "Symptom check" },
  { href: "/methodology", label: "Methodology" },
] as const;

/** The site header inside the hero. */
export function SiteNav() {
  return (
    <header className="relative z-30 mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 pt-4 sm:px-8 sm:pt-6">
      <Link href="/" className="rounded-lg text-white focus-visible:outline-white">
        <Logo markClassName="size-8" className="text-[15px]" />
      </Link>

      <nav aria-label="Main" className="glass-card hidden items-center gap-0.5 rounded-full p-1 md:flex">
        {NAV_LINKS.map((link) => (
          <Link
            key={link.href}
            href={link.href}
            className="rounded-full px-4 py-1.5 text-sm text-indigo-100/85 transition-colors hover:bg-white/10 hover:text-white"
          >
            {link.label}
          </Link>
        ))}
      </nav>

      <div className="flex items-center gap-2">
        <Link
          href="/login"
          className="rounded-full px-3.5 py-2 text-sm font-medium text-white/90 transition-colors hover:bg-white/10 hover:text-white"
        >
          Sign in
        </Link>
        <Link
          href="/signup"
          className="hidden rounded-full bg-white px-4 py-2 text-sm font-semibold text-[#141a4a] shadow-[0_10px_24px_-12px_rgb(255_255_255/0.8)] transition-transform hover:-translate-y-px sm:inline-flex"
        >
          Get started
        </Link>
        <details className="group relative md:hidden">
          <summary
            aria-label="Menu"
            className="glass-card grid size-9 cursor-pointer list-none place-items-center rounded-full text-white [&::-webkit-details-marker]:hidden"
          >
            <Menu className="size-4" aria-hidden />
          </summary>
          <div className="glass-card absolute right-0 mt-2 flex w-52 flex-col rounded-2xl p-1.5 !bg-[#10164a]/95">
            {NAV_LINKS.map((link) => (
              <Link key={link.href} href={link.href} className="rounded-xl px-3 py-2 text-sm text-indigo-50 hover:bg-white/10">
                {link.label}
              </Link>
            ))}
            <Link href="/signup" className="mt-1 rounded-xl bg-white px-3 py-2 text-sm font-semibold text-[#141a4a]">
              Get started — free
            </Link>
          </div>
        </details>
      </div>
    </header>
  );
}

/* -------------------------------------------------------------- features */

interface Feature {
  title: string;
  body: string;
  href: string;
  action: string;
  icon: LucideIcon;
  tile: [string, string, string];
}

const FEATURES: Feature[] = [
  {
    title: "Chat assistant",
    body: "Ask anything, the way you would ask a colleague. Answers come from research, guidelines and drug labels, with the sources underneath.",
    href: "/app/chat",
    action: "Open the chat",
    icon: MessageCircleHeart,
    tile: ["#a5b4fc", "#4f46e5", "rgb(79 70 229 / 0.55)"],
  },
  {
    title: "Symptom check",
    body: "Warning signs first, then what to do and safe medicine doses for the person’s age — from NHS, NICE and WHO guidance.",
    href: "/check",
    action: "Start a check",
    icon: Stethoscope,
    tile: ["#5eead4", "#0d9488", "rgb(13 148 136 / 0.5)"],
  },
  {
    title: "Prescribing support",
    body: "For clinicians: treatment options by condition, drawn from guidelines and drug labels and linked to them, for your judgement.",
    href: "/app/treatment",
    action: "See the options",
    icon: Pill,
    tile: ["#c4b5fd", "#7c3aed", "rgb(124 58 237 / 0.5)"],
  },
  {
    title: "Learn by specialty",
    body: "41 MBBS and PG specialties, each with the newest high-evidence papers from PubMed, for students and doctors.",
    href: "/app/learn",
    action: "Browse specialties",
    icon: GraduationCap,
    tile: ["#7dd3fc", "#0284c7", "rgb(2 132 199 / 0.5)"],
  },
  {
    title: "Voice conversation",
    body: "Talk to the assistant and hear the answer read back, with speech recognition tuned for medical words.",
    href: "/app/chat?voice=1",
    action: "Try voice",
    icon: AudioLines,
    tile: ["#67e8f9", "#0891b2", "rgb(8 145 178 / 0.5)"],
  },
  {
    title: "Evidence timeline",
    body: "Every source on a timeline by year, coloured by whether it supports or opposes the answer — disagreement is shown, never buried.",
    href: "/methodology",
    action: "How it works",
    icon: ChartScatter,
    tile: ["#6ee7b7", "#059669", "rgb(5 150 105 / 0.5)"],
  },
  {
    title: "Live research",
    body: "When the library is thin on a question, PubMed is searched as you ask, and the new papers join the library.",
    href: "/methodology",
    action: "How it works",
    icon: FlaskConical,
    tile: ["#fcd34d", "#d97706", "rgb(217 119 6 / 0.5)"],
  },
  {
    title: "Private by design",
    body: "Personal health details are refused before any model sees them, and nothing typed into the public chat is stored.",
    href: "/methodology#limitations",
    action: "Read the limits",
    icon: ShieldCheck,
    tile: ["#a5b4fc", "#312e81", "rgb(49 46 129 / 0.55)"],
  },
];

function IconTile({ icon: Icon, tile, className }: { icon: LucideIcon; tile: Feature["tile"]; className?: string }) {
  return (
    <span
      className={cn("icon-tile grid size-12 place-items-center rounded-2xl text-white", className)}
      style={{ "--tile-from": tile[0], "--tile-to": tile[1], "--tile-shadow": tile[2] } as CSSProperties}
      aria-hidden
    >
      <Icon className="size-[22px] drop-shadow-[0_1px_1px_rgb(0_0_0/0.25)]" />
    </span>
  );
}

export function FeatureGrid() {
  return (
    <section id="features" aria-labelledby="features-heading" className="mx-auto max-w-6xl scroll-mt-8 px-4 pt-24 pb-10 sm:px-8">
      <div className="mx-auto max-w-2xl text-center">
        <SectionBadge>Features</SectionBadge>
        <h2
          id="features-heading"
          className="mt-5 text-balance text-3xl font-semibold tracking-[-0.03em] text-slate-900 sm:text-[2.6rem] sm:leading-[1.1] dark:text-white"
        >
          Everything a health question needs,
          <span className="block text-slate-400 dark:text-indigo-200/60">with the evidence one click away.</span>
        </h2>
      </div>

      <ul className="mt-14 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
        {FEATURES.map((feature) => (
          <li key={feature.title}>
            <TiltCard className="flex h-full flex-col rounded-[24px] border border-slate-200/80 bg-white p-6 shadow-[inset_0_1px_0_#fff,0_22px_44px_-30px_rgb(30_27_75/0.45)] hover:shadow-[inset_0_1px_0_#fff,0_30px_60px_-28px_rgb(79_70_229/0.45)] dark:border-white/10 dark:bg-white/[0.04] dark:shadow-none">
              <IconTile icon={feature.icon} tile={feature.tile} />
              <h3 className="mt-5 text-[17px] font-semibold tracking-tight text-slate-900 dark:text-white">
                <Link
                  href={feature.href}
                  className="after:absolute after:inset-0 after:rounded-[24px] focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:outline-offset-2 focus-visible:after:outline-indigo-500"
                >
                  {feature.title}
                </Link>
              </h3>
              <p className="mt-2 flex-1 text-sm leading-6 text-slate-600 dark:text-slate-300">{feature.body}</p>
              <span aria-hidden className="mt-5 inline-flex items-center gap-1.5 text-sm font-semibold text-indigo-600 dark:text-indigo-300">
                {feature.action}
                <ArrowRight className="size-4 transition-transform duration-300 group-hover/tilt:translate-x-1" />
              </span>
            </TiltCard>
          </li>
        ))}
      </ul>
    </section>
  );
}

/* ------------------------------------------------------------- audiences */

const AUDIENCES = [
  {
    who: "For patients",
    icon: HeartPulse,
    tile: ["#5eead4", "#0d9488", "rgb(13 148 136 / 0.5)"] as Feature["tile"],
    glow: "rgb(45 212 191 / 0.35)",
    points: ["Plain-language answers", "A symptom check that looks for danger signs first", "Over-the-counter doses by age, from NHS guidance"],
    href: "/check",
    action: "Start a symptom check",
  },
  {
    who: "For doctors",
    icon: Stethoscope,
    tile: ["#a5b4fc", "#4f46e5", "rgb(79 70 229 / 0.55)"] as Feature["tile"],
    glow: "rgb(99 102 241 / 0.35)",
    points: ["Clinician-level answers with graded sources", "Prescribing support from guidelines and drug labels", "Conflicting studies set side by side"],
    href: "/app/treatment",
    action: "Open prescribing support",
  },
  {
    who: "For students",
    icon: GraduationCap,
    tile: ["#d8b4fe", "#7c3aed", "rgb(124 58 237 / 0.5)"] as Feature["tile"],
    glow: "rgb(168 85 247 / 0.32)",
    points: ["Explanations that teach the reasoning", "41 MBBS and PG specialties", "The newest high-evidence papers in each"],
    href: "/app/learn",
    action: "Browse specialties",
  },
] as const;

export function Audiences() {
  return (
    <section aria-labelledby="audiences-heading" className="mx-auto max-w-6xl px-4 py-16 sm:px-8">
      <div className="mx-auto max-w-2xl text-center">
        <SectionBadge>Who it’s for</SectionBadge>
        <h2
          id="audiences-heading"
          className="mt-5 text-balance text-3xl font-semibold tracking-[-0.03em] text-slate-900 sm:text-[2.6rem] sm:leading-[1.1] dark:text-white"
        >
          One assistant, three voices.
        </h2>
        <p className="mt-4 text-pretty text-slate-600 dark:text-slate-300">
          Say who you are and it answers at your level. The evidence underneath is the same.
        </p>
      </div>
      <ul className="mt-12 grid gap-5 md:grid-cols-3">
        {AUDIENCES.map((a) => (
          <li key={a.who}>
            <TiltCard className="flex h-full flex-col overflow-hidden rounded-[26px] border border-slate-200/80 bg-white shadow-[0_22px_44px_-30px_rgb(30_27_75/0.45)] dark:border-white/10 dark:bg-white/[0.04]">
              <div className="relative grid h-36 place-items-center overflow-hidden bg-[linear-gradient(180deg,#0b1036,#1a1a66)]">
                <div className="hero-grid absolute inset-0 opacity-70" aria-hidden />
                <div
                  aria-hidden
                  className="absolute size-44 rounded-full blur-2xl"
                  style={{ background: `radial-gradient(closest-side, ${a.glow}, transparent)` }}
                />
                <IconTile icon={a.icon} tile={a.tile} className="relative size-16 rounded-[20px] [&_svg]:size-7" />
              </div>
              <div className="flex flex-1 flex-col p-6">
                <h3 className="text-lg font-semibold tracking-tight text-slate-900 dark:text-white">{a.who}</h3>
                <ul className="mt-3 flex-1 space-y-2">
                  {a.points.map((point) => (
                    <li key={point} className="flex gap-2 text-sm leading-6 text-slate-600 dark:text-slate-300">
                      <Check className="mt-1 size-4 shrink-0 text-teal-600 dark:text-teal-300" aria-hidden />
                      {point}
                    </li>
                  ))}
                </ul>
                <Link
                  href={a.href}
                  className="mt-5 inline-flex items-center gap-1.5 self-start text-sm font-semibold text-indigo-600 hover:underline dark:text-indigo-300"
                >
                  {a.action} <ArrowRight className="size-4" aria-hidden />
                </Link>
              </div>
            </TiltCard>
          </li>
        ))}
      </ul>
    </section>
  );
}

/* ----------------------------------------------------- principles, footer */

const PRINCIPLES = [
  {
    icon: BadgeCheck,
    title: "Cited, graded, verified",
    body: "Hybrid retrieval, reranking, contradiction detection and a grounding check on every answer — and a plain “not enough evidence” when the literature is thin.",
  },
  {
    icon: Gauge,
    title: "Measured in public",
    body: "Retrieval recall, safety results, calibration, voice latency and a load test, read live from the evaluation runs on the methodology page.",
  },
  {
    icon: Siren,
    title: "Safety before answers",
    body: "Emergency warning signs come first, with 112 and 108 to call. It supports decisions; it does not diagnose.",
  },
] as const;

const FOOTER_LINKS = [
  {
    heading: "Product",
    links: [
      { href: "/check", label: "Symptom check" },
      { href: "#demo", label: "Live demo" },
      { href: "#features", label: "Features" },
    ],
  },
  {
    heading: "Trust",
    links: [
      { href: "/methodology", label: "Methodology" },
      { href: "/methodology#limitations", label: "Limitations" },
      { href: "/health", label: "System health" },
    ],
  },
  {
    heading: "Account",
    links: [
      { href: "/login", label: "Sign in" },
      { href: "/signup", label: "Create a free account" },
    ],
  },
] as const;

/** The dark close of the page: how it stays honest, then the footer. */
export function PrinciplesAndFooter() {
  return (
    <div className="hero-sky relative isolate mx-2 mb-2 overflow-hidden rounded-[28px] text-white sm:mx-3 sm:mb-3 lg:rounded-[36px]">
      <div aria-hidden className="hero-grid absolute inset-0 -z-10 opacity-60" />
      <div className="mx-auto max-w-6xl px-5 pt-16 pb-10 sm:px-8 lg:pt-20">
        <section aria-label="How it stays honest" className="grid gap-4 md:grid-cols-3">
          {PRINCIPLES.map(({ icon: Icon, title, body }) => (
            <div key={title} className="glass-card rounded-3xl p-6">
              <span className="grid size-10 place-items-center rounded-xl bg-white/10 text-teal-200 shadow-[inset_0_1px_0_rgb(255_255_255/0.2)]">
                <Icon className="size-5" aria-hidden />
              </span>
              <h2 className="mt-4 text-base font-semibold">{title}</h2>
              <p className="mt-2 text-sm leading-6 text-indigo-100/75">{body}</p>
            </div>
          ))}
        </section>

        <footer className="mt-14 border-t border-white/10 pt-10">
          <div className="flex flex-col gap-10 md:flex-row md:justify-between">
            <div className="max-w-sm">
              <Logo markClassName="size-8" />
              <p className="mt-4 text-sm leading-6 text-indigo-100/70">
                Evidence-grounded answers to health and clinical questions, for patients, doctors and students.
              </p>
            </div>
            <nav aria-label="Footer" className="grid grid-cols-2 gap-8 sm:grid-cols-3">
              {FOOTER_LINKS.map((column) => (
                <div key={column.heading}>
                  <p className="text-xs font-semibold tracking-wider text-indigo-200/60 uppercase">{column.heading}</p>
                  <ul className="mt-3 space-y-2">
                    {column.links.map((link) => (
                      <li key={link.href}>
                        <Link href={link.href} className="text-sm text-indigo-50/90 hover:text-white">
                          {link.label}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </nav>
          </div>

          <p className="mt-12 border-t border-white/10 pt-6 text-xs leading-5 text-indigo-100/60">
            A clinical decision <em>support</em> tool, not a diagnostic tool, and not a substitute for a doctor. In an
            emergency call 112 — or 108 for an ambulance in India — or your local emergency number.
          </p>
        </footer>
      </div>
    </div>
  );
}
