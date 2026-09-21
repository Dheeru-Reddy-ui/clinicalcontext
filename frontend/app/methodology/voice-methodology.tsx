import { LocalTime } from "@/components/clinical/local-time";

/** The shape `evals/voice/run.py` writes to `evals/results/voice.json`. */
export interface VoiceEvalReport {
  generated_at: string;
  duration_s: number;
  targets: Record<string, number>;
  summary: {
    backend: { voice_backend: string; stt: string; tts: string; ai_backend: string; platform: string; mode: string };
    fixtures: number;
    errors: string[];
    stt: Record<
      string,
      {
        utterances: number;
        wer_raw_mean: number | null;
        wer_corrected_mean: number | null;
        medical_terms: number;
        medical_term_error_rate_raw: number | null;
        medical_term_error_rate_corrected: number | null;
        missed_terms_corrected: string[];
      }
    >;
    endpointing: {
      complete_questions: number;
      decision_p50_ms: number | null;
      decision_p95_ms: number | null;
      hesitation_fixtures: number;
      premature_cut_offs: string[];
    };
    first_audio: {
      server: {
        legs: Record<string, { p50: number | null; p95: number | null; n: number }>;
        total_first_audio: { p50: number | null; p95: number | null; n: number };
      };
      client_observed: { p50: number | null; p95: number | null; n: number };
    };
    speculation: { fired: number; hits: number; wasted: number; hit_rate: number | null; wasted_rate: number | null };
    barge_in: {
      interruptions: number;
      acknowledged: number;
      client_stop_p50_ms: number | null;
      client_stop_p95_ms: number | null;
      server_audio_stop_p50_ms: number | null;
      server_audio_stop_p95_ms: number | null;
      backchannels: number;
      backchannels_ignored: number;
      client_stop_note: string;
    };
    lasa: {
      fixtures: number;
      transcribed_correctly: number;
      confirmed: number;
      silent_substitutions: string[];
      unrecognized?: string[];
    };
    adversarial: Record<string, { total: number; passed: number; failures: string[] }>;
    masks_used: number;
  };
  judge: { ran: boolean; reason?: string; mean_scores?: Record<string, number | null>; scored?: number };
  gate: { level: string; passed: boolean; checks: Array<{ name: string; passed: boolean | null; measured: unknown; target: unknown }> };
}

const LEG_LABELS: Record<string, string> = {
  endpoint_decision: "Endpoint decision",
  transcript_final: "Final transcript",
  guardrails: "Guardrails",
  retrieval: "Retrieval",
  llm_first_token: "LLM first token",
  first_sentence: "First sentence",
  tts_ttfb: "TTS first byte",
  client_playback: "Client playback",
};

function ms(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${Math.round(v)} ms`;
}
function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`;
}

export function VoiceMethodology({ report }: { report: VoiceEvalReport | null }) {
  return (
    <section className="space-y-6" aria-labelledby="voice-heading">
      <div>
        <h2 id="voice-heading" className="text-lg font-semibold tracking-tight">
          The voice agent
        </h2>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          Voice is a cascade — speech-to-text, the same guardrails as text, the same retrieval graph, then
          text-to-speech — not a speech-to-speech model. The numbers below come from the voice harness
          (<code className="font-mono text-xs">evals/voice/run.py</code>), which plays a fixture corpus of
          spoken questions through the real WebSocket pipeline at real-time pace and records what happened.
        </p>
      </div>

      {!report ? (
        <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground" data-testid="voice-eval-missing">
          The voice evaluation has not been run against this deployment, so there are no numbers to show.
        </p>
      ) : (
        <VoiceNumbers report={report} />
      )}

      <div className="space-y-3 text-sm leading-6" data-testid="voice-limitations">
        <h3 className="text-base font-semibold">Limitations — what &ldquo;as capable as a frontier voice mode&rdquo; honestly means here</h3>
        <p>
          <strong>Where we match frontier voice modes:</strong> interruption handling (barge-in measured in the
          client&rsquo;s audio thread, backchannels ignored) and follow-up coherence across turns. Perceived
          responsiveness is a target, not a claim: the first-audio band of frontier voice modes is p50 ≈ 1.2 s, and
          the number to hold against it is the one in the table above for the backend that was actually measured
          {report && !report.gate.checks.some((c) => c.name === "first_audio_p50_le_1200ms" && c.passed === true)
            ? " — which is currently outside that band"
            : ""}
          .
        </p>
        <p>
          <strong>Where we beat them, by design:</strong> medical-term accuracy (a recognizer boosted with the corpus
          vocabulary, a correction pass against it, and a confirmation gate for look-alike/sound-alike drug names),
          every claim cited to a source, guardrail-enforced scope, and published latency and calibration numbers.
          No general assistant does any of this.
        </p>
        <p>
          <strong>Where we don&rsquo;t match them, and why:</strong> the prosodic naturalness and emotional range of
          speech-native models, and open-domain chat, which is deliberately out of scope. The cascade was chosen
          because a clinical tool must intercept, verify, and cite — a speech-to-speech black box cannot.
        </p>
        <p className="text-muted-foreground">
          The offline backend measured here runs faster-whisper on a CPU and an operating-system voice; its latency
          is bounded by local decoding, not by the pipeline. The same harness, pointed at the cloud backend, is the
          number to compare against frontier voice modes.
        </p>
      </div>
    </section>
  );
}

function VoiceNumbers({ report }: { report: VoiceEvalReport }) {
  const s = report.summary;
  const legs = Object.entries(s.first_audio.server.legs).filter(([, v]) => v.n > 0);
  return (
    <div className="space-y-6" data-testid="voice-eval-numbers">
      <p className="text-xs text-muted-foreground">
        Run <LocalTime iso={report.generated_at} /> · {s.fixtures} fixtures in {Math.round(report.duration_s)} s ·{" "}
        {s.backend.voice_backend} backend ({s.backend.stt} → {s.backend.tts}) on {s.backend.platform} ·{" "}
        {s.errors.length} harness errors
      </p>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <caption className="mb-2 text-left text-sm font-medium">
            Latency waterfall — from the user&rsquo;s last word to first audio (golden questions, n={s.first_audio.client_observed.n})
          </caption>
          <thead>
            <tr className="border-b text-left text-xs text-muted-foreground">
              <th className="py-1 pr-4 font-medium">Leg</th>
              <th className="py-1 pr-4 font-medium">p50</th>
              <th className="py-1 pr-4 font-medium">p95</th>
              <th className="py-1 font-medium">n</th>
            </tr>
          </thead>
          <tbody>
            {legs.map(([leg, v]) => (
              <tr key={leg} className="border-b border-border/60">
                <td className="py-1 pr-4">{LEG_LABELS[leg] ?? leg}</td>
                <td className="py-1 pr-4 font-mono tabular-nums">{ms(v.p50)}</td>
                <td className="py-1 pr-4 font-mono tabular-nums">{ms(v.p95)}</td>
                <td className="py-1 font-mono tabular-nums">{v.n}</td>
              </tr>
            ))}
            <tr className="font-medium">
              <td className="py-1 pr-4">Total first audio (server, sent)</td>
              <td className="py-1 pr-4 font-mono tabular-nums">{ms(s.first_audio.server.total_first_audio.p50)}</td>
              <td className="py-1 pr-4 font-mono tabular-nums">{ms(s.first_audio.server.total_first_audio.p95)}</td>
              <td className="py-1 font-mono tabular-nums">{s.first_audio.server.total_first_audio.n}</td>
            </tr>
            <tr className="font-medium">
              <td className="py-1 pr-4">Total first audio (client-observed)</td>
              <td className="py-1 pr-4 font-mono tabular-nums">{ms(s.first_audio.client_observed.p50)}</td>
              <td className="py-1 pr-4 font-mono tabular-nums">{ms(s.first_audio.client_observed.p95)}</td>
              <td className="py-1 font-mono tabular-nums">{s.first_audio.client_observed.n}</td>
            </tr>
          </tbody>
        </table>
        <p className="mt-1 text-xs text-muted-foreground">
          Targets: p50 ≤ {report.targets.first_audio_p50_ms} ms, p95 ≤ {report.targets.first_audio_p95_ms} ms.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Block title="Medical-term accuracy">
          {(["all", "en-GB", "en-IN", "noise_10db"] as const).map((k) => {
            const b = s.stt[k];
            if (!b) return null;
            return (
              <p key={k} className="flex justify-between gap-3 font-mono text-xs tabular-nums">
                <span>{k === "noise_10db" ? "10 dB pink noise" : k} · {b.utterances} utt.</span>
                <span>
                  term error {pct(b.medical_term_error_rate_raw)} → {pct(b.medical_term_error_rate_corrected)} · WER{" "}
                  {pct(b.wer_raw_mean)} → {pct(b.wer_corrected_mean)}
                </span>
              </p>
            );
          })}
          <p className="text-xs text-muted-foreground">raw recognizer → after the corpus-vocabulary correction pass · target term error &lt; 5%</p>
        </Block>
        <Block title="Turn-taking">
          <Row k="Endpoint decision p50 / p95" v={`${ms(s.endpointing.decision_p50_ms)} / ${ms(s.endpointing.decision_p95_ms)}`} />
          <Row k="Hesitation fixtures cut off" v={`${s.endpointing.premature_cut_offs.length} of ${s.endpointing.hesitation_fixtures}`} />
          <Row k="Backchannels ignored" v={`${s.barge_in.backchannels_ignored} of ${s.barge_in.backchannels}`} />
          <Row k="Barge-in stop (client) p50 / p95" v={`${ms(s.barge_in.client_stop_p50_ms)} / ${ms(s.barge_in.client_stop_p95_ms)}`} />
          <Row k="Barge-in acknowledged" v={`${s.barge_in.acknowledged} of ${s.barge_in.interruptions}`} />
        </Block>
        <Block title="Speculative retrieval">
          <Row k="Fired / hit / wasted" v={`${s.speculation.fired} / ${s.speculation.hits} / ${s.speculation.wasted}`} />
          <Row k="Hit rate" v={pct(s.speculation.hit_rate)} />
          <Row k="Wasted-cost rate" v={pct(s.speculation.wasted_rate)} />
          <Row k="Masks used" v={String(s.masks_used)} />
        </Block>
        <Block title="Safety parity (spoken adversarial set)">
          {Object.entries(s.adversarial).map(([cat, v]) => (
            <Row key={cat} k={cat.replace("adversarial_", "")} v={`${v.passed} / ${v.total}`} />
          ))}
          <Row
            k="LASA: correct / confirmed / substituted / unrecognized"
            v={`${s.lasa.transcribed_correctly} / ${s.lasa.confirmed} / ${s.lasa.silent_substitutions.length} / ${s.lasa.unrecognized?.length ?? 0}`}
          />
        </Block>
      </div>

      <div className="rounded-md border p-3 text-xs">
        <p className="mb-1 font-medium">
          Gate ({report.gate.level}): {report.gate.passed ? "passed" : "failed"}
        </p>
        <ul className="grid gap-0.5 sm:grid-cols-2">
          {report.gate.checks.map((c) => (
            <li key={c.name} className="flex justify-between gap-2 font-mono">
              <span className={c.passed === false ? "text-redflag-fg" : c.passed ? "" : "text-muted-foreground"}>
                {c.passed === null ? "n/a" : c.passed ? "pass" : "FAIL"} {c.name}
              </span>
              <span className="text-muted-foreground">{String(c.measured)}</span>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-muted-foreground">
          Turn-quality rubric (LLM-as-Judge):{" "}
          {report.judge.ran
            ? `${report.judge.scored} turns scored · ${Object.entries(report.judge.mean_scores ?? {})
                .map(([k, v]) => `${k} ${v ?? "—"}/2`)
                .join(" · ")}`
            : `not run — ${report.judge.reason ?? "no key"}`}
        </p>
      </div>
    </div>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-1 rounded-md border p-3">
      <h4 className="text-sm font-medium">{title}</h4>
      {children}
    </section>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <p className="flex justify-between gap-3 font-mono text-xs tabular-nums">
      <span className="text-muted-foreground">{k}</span>
      <span>{v}</span>
    </p>
  );
}
