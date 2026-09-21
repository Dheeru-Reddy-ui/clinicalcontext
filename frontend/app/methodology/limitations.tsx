import type { AblationReport, CalibrationReport, GoldenReport, LoadReport } from "@/lib/evals";
import { pct } from "@/lib/evals";
import type { VoiceEvalReport } from "@/app/methodology/voice-methodology";

/**
 * The honest part. Every figure quoted here is read from the same result
 * files as the sections above, so this section cannot say "it works" while
 * the tables say otherwise; where a number is missing the sentence says so.
 */
export function Limitations({
  golden,
  ablation,
  calibration,
  voice,
  load,
}: {
  golden: GoldenReport | null;
  ablation: AblationReport | null;
  calibration: CalibrationReport | null;
  voice: VoiceEvalReport | null;
  load: LoadReport | null;
}) {
  const chunks = golden?.retrieval.chunks;
  const docs = golden?.retrieval.documents;
  const gen = golden?.generation;
  const goldenCurve = calibration?.curves["golden"];
  const high = goldenCurve?.buckets.find((b) => b.level === "high");
  const moderate = goldenCurve?.buckets.find((b) => b.level === "moderate");
  const offline = golden?.backend.ai_backend === "offline";
  const firstAudio = voice?.summary.first_audio.server.total_first_audio.p50 ?? null;
  const termError = voice?.summary.stt["all"]?.medical_term_error_rate_corrected ?? null;

  return (
    <section className="space-y-4" aria-labelledby="limitations-heading" id="limitations" data-testid="limitations">
      <div>
        <h2 id="limitations-heading" className="text-lg font-semibold tracking-tight">
          Limitations — what it gets wrong, and what it is not
        </h2>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          Read this section as the counterpart of the tables above. The figures are the same files, quoted here
          with the conclusions we draw from them.
        </p>
      </div>

      <div className="space-y-3 text-sm leading-6">
        <p>
          <strong>It is not a diagnostic tool.</strong> ClinicalContext answers questions about the literature it
          was given; it does not see a patient, it refuses diagnosis and dosing for a specific person, and the
          guardrails that enforce that are tested on every pull request ({golden ? `${golden.safety.total_passed}/${golden.safety.total} adversarial cases pass` : "the adversarial results have not been produced"}
          ). An answer is a cited summary of published conclusions, to be read by a clinician who decides.
        </p>

        <p>
          <strong>Retrieval misses most of what the human indexers would consider relevant.</strong>{" "}
          {chunks && docs ? (
            <>
              On the golden set, recall@10 is {pct(chunks.recall_at_10, 1)} at passage level and {pct(docs.recall_at_10, 1)} at
              document level (MRR {docs.mrr === null ? "—" : docs.mrr.toFixed(2)}): for a typical question, the top ten passages contain a small
              fraction of the labelled evidence.
            </>
          ) : (
            "The golden-set retrieval numbers have not been produced, so this cannot be quantified here."
          )}{" "}
          {offline
            ? "The measured backend is the offline one — a hashing lexical embedder and BM25 in place of Cohere embeddings and reranking — chosen so the whole pipeline runs and is measured without vendor keys; the same harness against the cloud backend is the number that would matter for a deployment, and it has not been run because there are no keys in this environment."
            : "These are cloud-backend numbers; they are what a deployment would see."}
          {ablation && ablation.rows.length > 0 && (
            <>
              {" "}
              The ablation shows the stages that help on this backend and the ones that do not; a stage that is flat
              in that table is flat, not &ldquo;works in principle&rdquo;.
            </>
          )}
        </p>

        <p>
          <strong>Its stated confidence is not yet calibrated.</strong>{" "}
          {high && moderate && high.observed !== null && moderate.observed !== null ? (
            <>
              &ldquo;High&rdquo; is meant to mean {pct(high.predicted)} likely to cite the gold source and was observed at{" "}
              {pct(high.observed)} on {high.n} answers; &ldquo;moderate&rdquo; is meant to mean {pct(moderate.predicted)} and
              was observed at {pct(moderate.observed)} on {moderate.n}. The calibration run{" "}
              {calibration?.recommendation.retune ? "recommends retuning" : "is within its drift threshold"}
              {calibration?.recommendation.retune && calibration.recommendation.reasons.length > 0
                ? ` (${calibration.recommendation.reasons.length} reason${calibration.recommendation.reasons.length === 1 ? "" : "s"} listed in the calibration section)`
                : ""}
              . Until the thresholds are retuned in a reviewed change and the next run confirms it, treat the
              confidence label as a ranking, not a probability.
            </>
          ) : (
            "The calibration report has not been produced, so the confidence labels are unverified."
          )}
        </p>

        <p>
          <strong>Abstention is imperfect in both directions.</strong>{" "}
          {gen ? (
            <>
              It abstains on {pct(gen.abstained_when_expected)} of the questions the corpus cannot answer (it should be 100%)
              and on {pct(gen.abstained_when_answerable, 1)} of the questions it could have answered. Contradiction detection
              finds {pct(gen.contradiction.recall)} of the labelled contradictions with {pct(gen.contradiction.precision)} precision
              — most flagged contradictions are not ones the labels agree with.
            </>
          ) : (
            "The generation numbers have not been produced."
          )}
        </p>

        <p>
          <strong>The corpus is narrow.</strong>{" "}
          {golden ? (
            <>
              {golden.corpus.documents.toLocaleString()} PubMed abstracts, of which {golden.corpus.labelable_documents.toLocaleString()} carry
              the MeSH indexing the golden set is built from
            </>
          ) : (
            "A corpus of PubMed abstracts"
          )}{" "}
          — abstracts, not full text; no guidelines, no drug labels, no local formularies. A question whose answer
          lives in a guideline body or a trial&rsquo;s methods section cannot be answered well from here, and the
          system is expected to abstain rather than improvise. The topics covered are the topics the ingestion
          queries asked for; a specialty outside them is simply absent.
        </p>

        <p>
          <strong>Voice is a cascade on a CPU.</strong>{" "}
          {firstAudio !== null ? (
            <>
              First audio arrives at p50 {Math.round(firstAudio)} ms on the measured offline backend (faster-whisper and an
              operating-system voice); the frontier band is around 1.2 s. Medical-term error rate after correction is{" "}
              {pct(termError, 1)}.
            </>
          ) : (
            "The voice harness has not been run, so its latency and accuracy are unmeasured here."
          )}{" "}
          Speech-native naturalness and open-domain chat are out of scope by design.
        </p>

        <p>
          <strong>Capacity is a single process on a laptop.</strong>{" "}
          {load ? (
            <>
              The load test above ran against {load.target} on {load.machine.cpu_count ?? "?"} CPUs with the{" "}
              {load.backend.ai_backend} backend;{" "}
              {load.breaking_point
                ? `it broke at ${load.breaking_point.users} users (${load.breaking_point.reason})`
                : `it did not break within the ramp, which says the ramp was short, not that the system is unbounded`}
              .{" "}
              {load.backend.ai_backend === "offline"
                ? "On the offline backend the reasoner and the BM25 reranker run inside the API process, so latency under load is CPU contention in that one process — cache hits queue behind it too, which is why they are not fast in the table either. "
                : ""}
              A cloud deployment has different latency (network round trips to the providers) and different limits
              (provider rate limits, then the database pool).
            </>
          ) : (
            "The load test has not been run, so no capacity claim is made."
          )}
        </p>

        <p>
          <strong>What is next.</strong> Run the harness against the cloud backend and publish that column beside this
          one; retune the confidence thresholds from the calibration report and show the before/after curve; grow the
          golden set from the review queue (thumbs-down cases a reviewer promotes) so ground truth stops being
          derived from indexing alone; and add full-text and guideline sources so abstention rates fall for the right
          reason.
        </p>
      </div>
    </section>
  );
}
