import Link from "next/link";

import { DemoPanel } from "@/components/marketing/demo-panel";
import { buttonVariants } from "@/components/ui/button";
import { apiUrl } from "@/lib/api";
import type { Schemas } from "@/lib/domain";

/**
 * The marketing page: what the product is, one live demo query against the
 * real pipeline (no login), and the doors in — sign in, the public
 * methodology page, system health.
 */

async function loadDemoQuestions(): Promise<Schemas["DemoQuestion"][]> {
  try {
    const response = await fetch(apiUrl("/api/public/demo/questions"), {
      cache: "no-store",
      headers: { Accept: "application/json" },
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
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-8 px-4 py-10 md:px-6">
      <header className="space-y-4">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">ClinicalContext</p>
        <h1 className="text-4xl font-semibold tracking-tight">Evidence-grounded answers to clinical questions.</h1>
        <p className="text-lg text-muted-foreground">
          Every claim traceable to a citation in the public medical literature — and explicit uncertainty when the
          evidence is weak or contradictory.
        </p>
        <p className="text-sm text-muted-foreground">
          A clinical decision <em>support</em> tool, not a diagnostic tool. No patient data, no PHI — enforced in code,
          not in a footer.
        </p>
        <div className="flex flex-wrap gap-2">
          <Link href="/login" className={buttonVariants()}>
            Sign in
          </Link>
          <Link href="/methodology" className={buttonVariants({ variant: "outline" })}>
            How it is measured
          </Link>
          <Link href="/health" className={buttonVariants({ variant: "ghost" })}>
            System health
          </Link>
        </div>
      </header>

      {questions.length > 0 ? (
        <DemoPanel questions={questions} />
      ) : (
        <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          The live demo is unavailable — the API could not be reached from this page.
        </p>
      )}

      <section className="grid gap-4 text-sm sm:grid-cols-3" aria-label="What makes it different">
        <div className="space-y-1">
          <h2 className="font-medium">Cited, graded, verified</h2>
          <p className="text-muted-foreground">
            Hybrid retrieval, reranking, contradiction detection and grounding verification — and an abstention when
            the literature is thin.
          </p>
        </div>
        <div className="space-y-1">
          <h2 className="font-medium">Measured in public</h2>
          <p className="text-muted-foreground">
            Retrieval recall, safety results, calibration, voice latency and a load test, read live from the eval
            runners on the <Link href="/methodology" className="underline underline-offset-4">methodology page</Link>.
          </p>
        </div>
        <div className="space-y-1">
          <h2 className="font-medium">Voice, same guardrails</h2>
          <p className="text-muted-foreground">
            Ask by voice: medical-vocabulary recognition, look-alike drug-name confirmation, and the same graph behind
            the spoken answer.
          </p>
        </div>
      </section>
    </main>
  );
}
