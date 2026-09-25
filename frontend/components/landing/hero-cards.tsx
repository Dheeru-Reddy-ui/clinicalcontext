import { BookOpenCheck, Check, GraduationCap, HeartPulse, Stethoscope } from "lucide-react";
import type { CSSProperties, ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * The glass cards floating around the hero's 3D centrepiece. Each one is a
 * picture of something the product really does — the symptom check's steps,
 * how sources are shown and graded, the stance-coloured evidence timeline —
 * not a statistic: nothing here is a number the product would have to earn.
 */

function Depth({ depth, className, children }: { depth: number; className?: string; children: ReactNode }) {
  return (
    <div className={cn("parallax", className)} style={{ "--depth": depth } as CSSProperties}>
      {children}
    </div>
  );
}

const CHECK_STEPS = [
  { label: "Age", value: "Adult", done: true },
  { label: "Main symptom", value: "Fever", done: true },
  { label: "Warning signs", value: "None", done: true },
  { label: "What to do and doses", value: "", done: false },
] as const;

/** A phone showing the symptom check part-way through. */
export function SymptomCheckPhone({ className }: { className?: string }) {
  const r = 40;
  const circumference = 2 * Math.PI * r;
  const done = CHECK_STEPS.filter((s) => s.done).length;
  return (
    <Depth depth={16} className={className}>
      <div className="float-slow">
        <div
          className="w-[224px] rounded-[2.3rem] border border-white/20 bg-[#0a0f2e]/70 p-2 shadow-[0_40px_90px_-28px_rgb(2_6_23/0.95)] backdrop-blur-xl [transform:perspective(1200px)_rotateY(14deg)_rotateX(4deg)]"
        >
          <div className="relative overflow-hidden rounded-[1.95rem] bg-[linear-gradient(180deg,#171d52_0%,#0c1036_100%)] px-4 pt-3 pb-4 text-white">
            <div className="mx-auto mb-3 h-5 w-20 rounded-full bg-black/60" />
            <p className="text-[11px] font-medium tracking-wide text-indigo-200/80 uppercase">Symptom check</p>
            <p className="text-[15px] font-semibold">Fever</p>

            <div className="relative mx-auto my-3 grid size-[112px] place-items-center">
              <svg viewBox="0 0 100 100" className="absolute inset-0 -rotate-90">
                <defs>
                  <linearGradient id="cc-phone-ring" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0" stopColor="#5eead4" />
                    <stop offset="1" stopColor="#818cf8" />
                  </linearGradient>
                </defs>
                <circle cx="50" cy="50" r={r} fill="none" stroke="rgb(255 255 255 / 0.1)" strokeWidth="8" />
                <circle
                  cx="50"
                  cy="50"
                  r={r}
                  fill="none"
                  stroke="url(#cc-phone-ring)"
                  strokeWidth="8"
                  strokeLinecap="round"
                  strokeDasharray={`${(circumference * done) / CHECK_STEPS.length} ${circumference}`}
                />
              </svg>
              <div className="text-center">
                <p className="text-xl font-semibold leading-none">
                  {done}
                  <span className="text-sm text-indigo-200/70">/{CHECK_STEPS.length}</span>
                </p>
                <p className="mt-1 text-[10px] text-indigo-100/70">steps done</p>
              </div>
            </div>

            <ul className="space-y-1.5">
              {CHECK_STEPS.map((step) => (
                <li key={step.label} className="flex items-center gap-2 rounded-xl bg-white/[0.06] px-2.5 py-1.5 text-[11px]">
                  <span
                    className={cn(
                      "grid size-4 shrink-0 place-items-center rounded-full",
                      step.done ? "bg-teal-400/90 text-[#062a2a]" : "border border-dashed border-indigo-200/60",
                    )}
                  >
                    {step.done && <Check className="size-2.5" strokeWidth={3} />}
                  </span>
                  <span className="flex-1 text-indigo-50/90">{step.label}</span>
                  {step.value && <span className="text-indigo-200/70">{step.value}</span>}
                </li>
              ))}
            </ul>
            <div className="mt-3 rounded-full bg-white py-2 text-center text-[11px] font-semibold text-[#141a4a]">
              See what to do
            </div>
          </div>
        </div>
      </div>
    </Depth>
  );
}

const SOURCE_ROWS = [
  { n: 1, kind: "Clinical guideline", grade: "A" },
  { n: 2, kind: "Systematic review", grade: "A" },
  { n: 3, kind: "Randomised trial", grade: "B" },
] as const;

/** How an answer's sources look: numbered, typed, graded A–D. */
export function SourcesCard({ className }: { className?: string }) {
  return (
    <Depth depth={-12} className={className}>
      <div className="float-slower glass-card w-[262px] rounded-2xl p-4 text-white">
        <div className="flex items-center gap-2 text-[13px] font-medium">
          <span className="grid size-7 place-items-center rounded-lg bg-[linear-gradient(135deg,#818cf8,#6366f1)] shadow-[inset_0_1px_0_rgb(255_255_255/0.35)]">
            <BookOpenCheck className="size-3.5" aria-hidden />
          </span>
          Answer with sources
        </div>
        <p className="mt-2 text-[12px] leading-5 text-indigo-100/75">
          Each sentence links to the passage it came from.
        </p>
        <ul className="mt-3 space-y-1.5">
          {SOURCE_ROWS.map((row) => (
            <li key={row.n} className="flex items-center gap-2 rounded-xl bg-white/[0.06] px-2.5 py-1.5 text-[12px]">
              <span className="grid size-5 place-items-center rounded-md bg-indigo-300/20 font-mono text-[10px] font-semibold text-indigo-100">
                {row.n}
              </span>
              <span className="flex-1 text-indigo-50/90">{row.kind}</span>
              <span
                className={cn(
                  "rounded-full px-1.5 py-0.5 text-[10px] font-semibold",
                  row.grade === "A" ? "bg-emerald-300/15 text-emerald-200" : "bg-sky-300/15 text-sky-200",
                )}
              >
                Grade {row.grade}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </Depth>
  );
}

// An illustration of the timeline's encoding, not a real answer's sources.
const TIMELINE_DOTS = [
  { x: 22, y: 36, r: 6, stance: "supports" },
  { x: 58, y: 52, r: 5, stance: "neutral" },
  { x: 92, y: 30, r: 7, stance: "supports" },
  { x: 128, y: 50, r: 5, stance: "opposes" },
  { x: 160, y: 26, r: 8, stance: "supports" },
  { x: 190, y: 44, r: 6, stance: "supports" },
  { x: 214, y: 56, r: 5, stance: "neutral" },
  { x: 240, y: 32, r: 7, stance: "supports" },
] as const;

const STANCE_COLOURS = { supports: "#34d399", opposes: "#fb7185", neutral: "#94a3b8" } as const;

/** The evidence timeline's idea in miniature: each source placed by year, coloured by stance. */
export function TimelineCard({ className }: { className?: string }) {
  return (
    <Depth depth={10} className={className}>
      <div className="float-slow glass-card w-[288px] rounded-2xl p-4 text-white [animation-delay:-2s]">
        <div className="flex items-center justify-between">
          <span className="text-[13px] font-medium">Evidence timeline</span>
          <span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] text-indigo-100/80">Example</span>
        </div>
        <svg viewBox="0 0 262 84" className="mt-2 w-full" aria-hidden>
          <line x1="6" y1="70" x2="256" y2="70" stroke="rgb(255 255 255 / 0.18)" />
          {TIMELINE_DOTS.map((dot) => (
            <g key={dot.x}>
              <circle cx={dot.x} cy={dot.y} r={dot.r + 5} fill={STANCE_COLOURS[dot.stance]} opacity="0.16" />
              <circle cx={dot.x} cy={dot.y} r={dot.r} fill={STANCE_COLOURS[dot.stance]} stroke="rgb(255 255 255 / 0.7)" strokeWidth="1" />
            </g>
          ))}
          {["2014", "2018", "2022", "2026"].map((year, i) => (
            <text key={year} x={14 + i * 76} y="82" fill="rgb(199 210 254 / 0.7)" fontSize="8" textAnchor="middle">
              {year}
            </text>
          ))}
        </svg>
        <div className="mt-2 flex gap-3 text-[11px] text-indigo-100/80">
          {(["supports", "opposes", "neutral"] as const).map((stance) => (
            <span key={stance} className="inline-flex items-center gap-1.5 capitalize">
              <span className="size-2 rounded-full" style={{ background: STANCE_COLOURS[stance] }} />
              {stance}
            </span>
          ))}
        </div>
      </div>
    </Depth>
  );
}

/** A small pill under the centrepiece: what happens to every answer. */
export function CheckedPill({ className }: { className?: string }) {
  return (
    <Depth depth={6} className={className}>
      <div className="glass-card flex items-center gap-2 rounded-full py-1.5 pr-4 pl-1.5 text-[12px] font-medium text-white">
        <span className="grid size-6 place-items-center rounded-full bg-teal-400 text-[#052e2b]">
          <Check className="size-3.5" strokeWidth={3} aria-hidden />
        </span>
        Every statement checked against its source
      </div>
    </Depth>
  );
}

const AUDIENCES = {
  patients: { label: "Patients", icon: HeartPulse, gradient: "linear-gradient(135deg,#2dd4bf,#0ea5e9)" },
  doctors: { label: "Doctors", icon: Stethoscope, gradient: "linear-gradient(135deg,#818cf8,#6366f1)" },
  students: { label: "Students", icon: GraduationCap, gradient: "linear-gradient(135deg,#c084fc,#7c3aed)" },
} as const;

/** Who it answers for — the assistant has a voice for each. */
export function AudienceChip({
  audience,
  depth,
  className,
}: {
  audience: keyof typeof AUDIENCES;
  depth: number;
  className?: string;
}) {
  const { label, icon: Icon, gradient } = AUDIENCES[audience];
  return (
    <Depth depth={depth} className={className}>
      <div className="float-slower glass-card flex items-center gap-2 rounded-full py-1.5 pr-3.5 pl-1.5 text-[13px] font-medium text-white">
        <span className="grid size-7 place-items-center rounded-full shadow-[inset_0_1px_0_rgb(255_255_255/0.4)]" style={{ background: gradient }}>
          <Icon className="size-3.5" aria-hidden />
        </span>
        {label}
      </div>
    </Depth>
  );
}
