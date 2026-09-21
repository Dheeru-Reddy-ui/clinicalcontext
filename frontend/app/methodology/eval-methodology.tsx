import { LocalTime } from "@/components/clinical/local-time";
import { CalibrationCurveChart, CalibrationTable } from "@/components/evals/calibration-curve";
import type { AblationReport, CalibrationReport, GoldenReport } from "@/lib/evals";
import { num, pct } from "@/lib/evals";

/**
 * The evaluation harness on the methodology page (Phase 12): the golden set
 * and what it measures, retrieval scored apart from generation, the
 * ten-configuration ablation, the safety set, and the confidence
 * calibration loop. Every number is read from a runner's committed output;
 * a metric that was not measured is shown as absent.
 */
export function EvalMethodology({
  golden,
  ablation,
  calibration,
}: {
  golden: GoldenReport | null;
  ablation: AblationReport | null;
  calibration: CalibrationReport | null;
}) {
  return (
    <section className="space-y-6" aria-labelledby="eval-heading" data-testid="eval-methodology">
      <header className="space-y-2">
        <h2 id="eval-heading" className="text-xl font-semibold tracking-tight">
          The evaluation harness
        </h2>
        <p className="text-sm leading-6 text-muted-foreground">
          A golden set of clinical questions with expert-derived ground truth, run through the real pipeline
          on every pull request. Retrieval is scored on its own (recall@k, precision@k, MRR, nDCG@10 against
          labelled passages) before generation is scored, so a regression is placed in the layer it came
          from. The Phase 7 adversarial set and the voice harness run in the same suite.
        </p>
      </header>

      <GoldenSetNotes golden={golden} />
      {golden ? <GoldenNumbers report={golden} /> : <Missing what="evals/results/golden.json" runner="evals.golden.run" />}

      <div className="space-y-2" id="ablation">
        <h3 className="text-base font-semibold">Ablation — what each stage buys</h3>
        <p className="text-sm leading-6 text-muted-foreground">
          The golden set through every configuration, one stage added at a time. Rows 1–5 change what is
          retrieved; rows 6–10 change what the graph does with it. If a stage does not help on the measured
          backend, the table says so — it is written by <code className="text-xs">evals/ablation/full.py</code>{" "}
          and never edited.
        </p>
        {ablation ? <AblationTable report={ablation} /> : <Missing what="evals/results/ablation.json" runner="evals.ablation.full" />}
      </div>

      <div className="space-y-2" id="calibration">
        <h3 className="text-base font-semibold">Confidence calibration — and the loop that retunes it</h3>
        <p className="text-sm leading-6 text-muted-foreground">
          Every answer states a confidence (high, moderate, low). The reliability diagram plots what each level
          is meant to mean (90%, 65%, 35% chance of being right) against how often such answers were right —
          on the golden set, and in production against thumbs feedback. A level whose observed accuracy falls
          more than {calibration ? Math.round(calibration.recommendation.drift_threshold * 100) : 15} points
          below its meaning on at least {calibration?.recommendation.min_bucket ?? 10} answers is flagged; the
          flag names the threshold to tighten in the confidence assessor, the change is made in a reviewed
          commit, and the next run shows whether it worked. The script proposes; a person retunes.
        </p>
        {calibration ? <CalibrationSection report={calibration} /> : <Missing what="evals/results/calibration.json" runner="evals.golden.calibrate" />}
      </div>
    </section>
  );
}

function Missing({ what, runner }: { what: string; runner: string }) {
  return (
    <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
      {what} has not been produced yet — run <code className="text-xs">python -m {runner}</code>. Nothing is shown in its
      place.
    </p>
  );
}

function GoldenSetNotes({ golden }: { golden: GoldenReport | null }) {
  const cats = golden ? Object.entries(golden.golden.by_category) : [];
  return (
    <div className="space-y-2 text-sm leading-6">
      <h3 className="text-base font-semibold">The golden set</h3>
      <p>
        {golden ? `${golden.golden.items} questions` : "The questions"} instantiate the generic clinical question
        types of Ely et al. (BMJ 1999) — drug of choice, is drug X indicated in situation Y, adverse effects,
        prognosis, diagnostic accuracy, guideline recommendation, head-to-head comparison — on the topics the corpus
        covers, plus questions on topics it does not cover where the right answer is to abstain.
        {cats.length > 0 && (
          <>
            {" "}
            By category: {cats.map(([k, v]) => `${k.replaceAll("_", " ")} ${v}`).join(", ")}.
          </>
        )}
      </p>
      <p>
        Ground truth is derived from what experts wrote, never from the pipeline under test: the relevant documents
        are those PubMed&rsquo;s human indexers assigned the question&rsquo;s MeSH headings, whose own title and
        conclusion address that kind of question; the relevant passages are their structured-abstract conclusions;
        the reference answer is the authors&rsquo; conclusion of the most authoritative of them (evidence grade, study
        design, recency), attributed by PMID. Only MeSH-indexed documents can be labelled, so retrieval is scored over
        that universe. Reviewed cases from real usage join the set through the feedback loop with a
        reviewer-written reference answer.
      </p>
    </div>
  );
}

function GoldenNumbers({ report }: { report: GoldenReport }) {
  const r = report.retrieval;
  const g = report.generation;
  const categories = Object.entries(r.by_category);
  return (
    <div className="space-y-4" data-testid="golden-numbers">
      <p className="text-xs text-muted-foreground">
        Run <LocalTime iso={report.generated_at} /> · {report.golden.items} items in {Math.round(report.duration_s)} s ·{" "}
        {report.backend.ai_backend} backend ({report.backend.reasoner} reasoner, {report.backend.embedder} embedder,{" "}
        {report.backend.reranker} reranker) · corpus {report.corpus.documents.toLocaleString()} documents,{" "}
        {report.corpus.labelable_documents.toLocaleString()} labelable
      </p>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <caption className="mb-2 text-left text-sm font-medium">
            Retrieval, scored alone (top 10 labelled passages; n={r.chunks.n} answerable items)
          </caption>
          <thead>
            <tr className="border-b text-left text-xs text-muted-foreground">
              <th className="py-1 pr-4 font-medium">Set</th>
              <th className="py-1 pr-4 font-medium">recall@5</th>
              <th className="py-1 pr-4 font-medium">recall@10</th>
              <th className="py-1 pr-4 font-medium">precision@10</th>
              <th className="py-1 pr-4 font-medium">MRR</th>
              <th className="py-1 font-medium">nDCG@10</th>
            </tr>
          </thead>
          <tbody className="font-mono text-xs tabular-nums">
            <MetricRow label="All (passages)" m={r.chunks} strong />
            <MetricRow label="All (documents)" m={r.documents} strong />
            {categories.map(([name, m]) => (
              <MetricRow key={name} label={`${name.replaceAll("_", " ")} (n=${m.n})`} m={m} />
            ))}
          </tbody>
        </table>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Card title="Generation — deterministic signals">
          <Row k="Abstained on answerable questions" v={pct(g.abstained_when_answerable)} />
          <Row k="Abstained on uncovered topics" v={pct(g.abstained_when_expected)} />
          <Row k="Answers citing a gold passage" v={pct(g.cited_gold_rate)} />
          <Row k="Evidence grade matched expected" v={pct(g.grade_match_rate)} />
          <Row
            k="Contradiction precision / recall"
            v={`${pct(g.contradiction.precision)} / ${pct(g.contradiction.recall)} (${g.contradiction.detected} of ${g.contradiction.expected} expected)`}
          />
          <Row k="Lexical grounding support (clinical sentences)" v={pct(g.grounding_support_mean)} />
          <Row k="Graph latency p50 / p95" v={`${g.latency_ms.p50 ?? "—"} / ${g.latency_ms.p95 ?? "—"} ms`} />
        </Card>
        <Card title="Generation — LLM judge (RAGAS + clinical rubric)">
          {report.judge.ran && report.judge.means ? (
            <>
              <Row k="Faithfulness" v={num(report.judge.means.faithfulness)} />
              <Row k="Answer relevance" v={num(report.judge.means.answer_relevance)} />
              <Row k="Context precision / recall" v={`${num(report.judge.means.context_precision)} / ${num(report.judge.means.context_recall)}`} />
              <Row k="Clinical accuracy (1–5)" v={num(report.judge.means.clinical_accuracy, 2)} />
              <Row k="Citation correctness (1–5)" v={num(report.judge.means.citation_correctness, 2)} />
              <Row k="Appropriate hedging (1–5)" v={num(report.judge.means.appropriate_hedging, 2)} />
              <Row k="Appropriate abstention (1–5)" v={num(report.judge.means.appropriate_abstention, 2)} />
              <Row k="Model · scored" v={`${report.judge.model} · ${report.judge.scored}`} />
            </>
          ) : (
            <p className="text-xs text-muted-foreground">
              Not run: {report.judge.reason ?? "no judge"}. The RAGAS metrics (faithfulness, answer relevance,
              context precision, context recall) and the rubric need an LLM; the deterministic signals on the
              left are what this run could measure.
            </p>
          )}
        </Card>
        <Card title="Safety — the Phase 7 adversarial set, same suite" id="safety">
          <Row k="Overall" v={`${report.safety.total_passed}/${report.safety.total} (${pct(report.safety.overall_rate)})`} />
          {Object.entries(report.safety.categories).map(([name, c]) => (
            <Row key={name} k={name.replaceAll("_", " ")} v={`${c.passed}/${c.total}`} />
          ))}
          <Row k="Gate" v={report.safety.gate_passed ? "passed" : "FAILED"} />
        </Card>
        <Card title="Gate, as CI evaluates it">
          {report.gate ? (
            report.gate.checks.map((c) => (
              <Row key={c.name} k={`${c.passed ? "PASS" : "FAIL"} · ${c.name}`} v={`${String(c.measured)} (${c.target})`} />
            ))
          ) : (
            <p className="text-xs text-muted-foreground">No gate was applied to this run.</p>
          )}
        </Card>
      </div>
    </div>
  );
}

function MetricRow({
  label,
  m,
  strong,
}: {
  label: string;
  m: GoldenReport["retrieval"]["chunks"];
  strong?: boolean;
}) {
  return (
    <tr className={`border-b border-border/60 ${strong ? "font-medium" : ""}`}>
      <td className="py-1 pr-4 font-sans text-sm">{label}</td>
      <td className="py-1 pr-4">{num(m.recall_at_5)}</td>
      <td className="py-1 pr-4">{num(m.recall_at_10)}</td>
      <td className="py-1 pr-4">{num(m.precision_at_10)}</td>
      <td className="py-1 pr-4">{num(m.mrr)}</td>
      <td className="py-1">{num(m.ndcg_at_10)}</td>
    </tr>
  );
}

function AblationTable({ report }: { report: AblationReport }) {
  const source = report.rows.find((r) => r.faithfulness_source)?.faithfulness_source;
  return (
    <div className="space-y-2" data-testid="ablation-table">
      <p className="text-xs text-muted-foreground">
        Run <LocalTime iso={report.generated_at} /> · {report.golden.items} items ({report.golden.pico_eligible} PICO-eligible) in{" "}
        {Math.round(report.duration_s / 60)} min · {report.backend.embedder} embedder, {report.backend.reranker} reranker,{" "}
        {report.backend.reasoner} reasoner · faithfulness = {source ?? "—"}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-xs text-muted-foreground">
              <th className="py-1 pr-3 font-medium">#</th>
              <th className="py-1 pr-3 font-medium">Configuration</th>
              <th className="py-1 pr-3 font-medium">recall@10</th>
              <th className="py-1 pr-3 font-medium">recall@10 (docs)</th>
              <th className="py-1 pr-3 font-medium">MRR</th>
              <th className="py-1 pr-3 font-medium">faithfulness</th>
              <th className="py-1 pr-3 font-medium">abstained</th>
              <th className="py-1 font-medium">cited gold</th>
            </tr>
          </thead>
          <tbody className="font-mono text-xs tabular-nums">
            {report.rows.map((r, i) => (
              <tr key={`${r.config}-${r.subset ?? i}`} className="border-b border-border/60">
                <td className="py-1 pr-3">{r.config}</td>
                <td className="py-1 pr-3 font-sans text-sm">
                  {r.description}
                  {r.subset && r.subset !== "all" ? <span className="text-muted-foreground"> [{r.subset} subset, n={r.items}]</span> : null}
                  {r.skipped ? <span className="text-muted-foreground"> — skipped: {r.skipped}</span> : null}
                </td>
                <td className="py-1 pr-3">{num(r.recall_at_10)}</td>
                <td className="py-1 pr-3">{num(r.recall_at_10_documents)}</td>
                <td className="py-1 pr-3">{num(r.mrr)}</td>
                <td className="py-1 pr-3">{num(r.faithfulness)}</td>
                <td className="py-1 pr-3">{pct(r.abstained_when_answerable)}</td>
                <td className="py-1">{pct(r.cited_gold_rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CalibrationSection({ report }: { report: CalibrationReport }) {
  const curves = Object.entries(report.curves);
  return (
    <div className="space-y-3" data-testid="calibration-section">
      <div className="grid gap-4 md:grid-cols-2">
        {curves.map(([name, curve]) => (
          <div key={name} className="space-y-2 rounded-md border p-3">
            <h4 className="text-sm font-medium">
              {name === "golden" ? "Golden set" : "Production"}{" "}
              <span className="font-normal text-muted-foreground">
                · outcome: {curve.outcome?.replaceAll("_", " ") ?? "—"} · n={curve.n}
              </span>
            </h4>
            <CalibrationCurveChart curve={curve} height={180} />
            <CalibrationTable curve={curve} />
          </div>
        ))}
      </div>
      <div className="rounded-md border p-3 text-sm">
        <p className="font-medium">
          Retune {report.recommendation.retune ? "recommended" : "not needed"}
          <span className="font-normal text-muted-foreground">
            {" "}
            · as of <LocalTime iso={report.generated_at} />
          </span>
        </p>
        {report.recommendation.reasons.length > 0 ? (
          <ul className="mt-1 list-disc space-y-1 pl-5 text-muted-foreground">
            {report.recommendation.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-muted-foreground">Every level with enough answers is within the drift threshold.</p>
        )}
        <p className="mt-2 font-mono text-xs text-muted-foreground">
          nominal {JSON.stringify(report.recommendation.nominal)} → proposed {JSON.stringify(report.recommendation.proposed_nominal)}
        </p>
        {report.notes.map((n) => (
          <p key={n} className="mt-1 text-xs text-muted-foreground">
            {n}
          </p>
        ))}
      </div>
    </div>
  );
}

function Card({ title, children, id }: { title: string; children: React.ReactNode; id?: string }) {
  return (
    <section className="space-y-1 rounded-md border p-3" id={id}>
      <h4 className="text-sm font-medium">{title}</h4>
      {children}
    </section>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <p className="flex justify-between gap-3 font-mono text-xs tabular-nums">
      <span className="text-muted-foreground">{k}</span>
      <span className="text-right">{v}</span>
    </p>
  );
}
