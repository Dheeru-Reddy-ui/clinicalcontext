import Link from "next/link";

import { AssistantLauncher } from "@/components/chat/assistant-launcher";
import { DemoPanel } from "@/components/marketing/demo-panel";
import { buttonVariants } from "@/components/ui/button";
import { apiUrl } from "@/lib/api";
import type { Schemas } from "@/lib/domain";

/**
 * The marketing page: what the product is, one live demo query against the
 * real pipeline (no login), and the doors in — sign in, the public
 * methodology page, system health.
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
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-8 px-4 py-10 md:px-6">
      <header className="space-y-4">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">ClinicalContext</p>
        <h1 className="text-4xl font-semibold tracking-tight">Evidence-grounded answers to health and clinical questions.</h1>
        <p className="text-lg text-muted-foreground">
          A health assistant for patients, doctors and students. Every claim is traceable to a citation in the
          medical literature, guidelines or drug labels — and it says so plainly when the evidence is weak or
          contradictory. Ask the assistant in the corner, or run a symptom check.
        </p>
        <p className="text-sm text-muted-foreground">
          A clinical decision <em>support</em> tool, not a diagnostic tool. No patient data, no PHI — enforced in code,
          not in a footer.
        </p>
        <div className="flex flex-wrap gap-2">
          <Link href="/login" className={buttonVariants()}>
            Sign in
          </Link>
          <Link href="/check" className={buttonVariants({ variant: "outline" })} data-testid="home-check">
            Symptom check
          </Link>
          <Link href="/methodology" className={buttonVariants({ variant: "outline" })}>
            How it is measured
          </Link>
          <Link href="/health" className={buttonVariants({ variant: "ghost" })}>
            System health
          </Link>
        </div>
      </header>

      <DemoPanel questions={questions} />

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
      <AssistantLauncher mode="public" checkHref="/check" />
    </main>
  );
}
