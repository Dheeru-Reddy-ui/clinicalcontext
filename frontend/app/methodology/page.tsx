import type { Metadata } from "next";
import Link from "next/link";

import { ArchitectureDiagram } from "@/app/methodology/architecture-diagram";
import { EvalMethodology } from "@/app/methodology/eval-methodology";
import { Limitations } from "@/app/methodology/limitations";
import { LoadMethodology } from "@/app/methodology/load-methodology";
import { VoiceMethodology, type VoiceEvalReport } from "@/app/methodology/voice-methodology";
import { apiUrl } from "@/lib/api";
import type { AblationReport, CalibrationReport, GoldenReport, LoadReport } from "@/lib/evals";

/**
 * The public methodology page. Phase 12 supplies the golden-set metrics, the
 * ablation table and the calibration loop; Phase 11 the voice latency
 * percentiles, medical-term accuracy, guardrail parity and barge-in numbers;
 * Phase 13 the architecture diagram, the load test and the limitations. All
 * numbers are read live from `evals/results/*.json` — the files the runners
 * wrote — never restated by hand. When a file is missing the page says so.
 */

export const metadata: Metadata = {
  title: "Methodology",
  description: "How ClinicalContext is measured: retrieval, safety, calibration, the voice agent, load, and its limitations.",
};

const CONTENTS: { href: string; label: string }[] = [
  { href: "#architecture", label: "Architecture" },
  { href: "#eval-heading", label: "Evaluation harness" },
  { href: "#ablation", label: "Ablation" },
  { href: "#safety", label: "Safety" },
  { href: "#calibration", label: "Calibration" },
  { href: "#voice-heading", label: "Voice" },
  { href: "#load", label: "Load test" },
  { href: "#limitations", label: "Limitations" },
];

async function loadEval<T>(name: string): Promise<T | null> {
  try {
    const response = await fetch(apiUrl(`/api/public/evals/${name}`), {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

export default async function MethodologyPage() {
  const [voice, golden, ablation, calibration, load] = await Promise.all([
    loadEval<VoiceEvalReport>("voice"),
    loadEval<GoldenReport>("golden"),
    loadEval<AblationReport>("ablation"),
    loadEval<CalibrationReport>("calibration"),
    loadEval<LoadReport>("load"),
  ]);
  return (
    <main className="mx-auto max-w-3xl space-y-10 px-4 py-10 md:px-6">
      <header className="space-y-2">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">ClinicalContext</p>
        <h1 className="text-2xl font-semibold tracking-tight">Methodology</h1>
        <p className="text-sm leading-6 text-muted-foreground">
          Every number on this page is read from the output of an evaluation runner committed to the
          repository. Nothing here is typed in by hand, and a number that was not measured is shown as
          absent rather than as zero. Change the eval, redeploy, and this page changes.
        </p>
        <nav aria-label="Contents" className="flex flex-wrap gap-x-3 gap-y-1 text-sm">
          <Link href="/" className="underline underline-offset-4">
            Home
          </Link>
          {CONTENTS.map((c) => (
            <a key={c.href} href={c.href} className="text-muted-foreground underline-offset-4 hover:underline">
              {c.label}
            </a>
          ))}
        </nav>
      </header>

      <section className="space-y-3" aria-labelledby="architecture-heading" id="architecture">
        <h2 id="architecture-heading" className="text-xl font-semibold tracking-tight">
          Architecture
        </h2>
        <p className="text-sm leading-6 text-muted-foreground">
          A question is one request through guardrails, a semantic cache, and a bounded LangGraph loop over hybrid
          retrieval (dense + BM25, fused, reranked), grading, contradiction detection, extractive or LLM generation,
          and grounding verification. Voice enters the same graph after speech-to-text. Each request is one trace.
        </p>
        <ArchitectureDiagram />
      </section>

      <EvalMethodology golden={golden} ablation={ablation} calibration={calibration} />

      <VoiceMethodology report={voice} />

      <LoadMethodology report={load} />

      <Limitations golden={golden} ablation={ablation} calibration={calibration} voice={voice} load={load} />
    </main>
  );
}
