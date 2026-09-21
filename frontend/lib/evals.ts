/**
 * The shapes the Phase 12 runners write (evals/golden/run.py,
 * evals/ablation/full.py, evals/golden/calibrate.py). Read live from
 * `/api/public/evals/{golden,ablation,calibration}`; never restated by hand.
 */

export interface RetrievalMeans {
  n: number;
  recall_at_5: number | null;
  recall_at_10: number | null;
  precision_at_5: number | null;
  precision_at_10: number | null;
  mrr: number | null;
  ndcg_at_10: number | null;
}

export interface CalibrationBucket {
  level: string;
  n: number;
  predicted: number;
  observed: number | null;
  gap: number | null;
}

export interface CalibrationCurve {
  n: number;
  brier: number | null;
  ece: number | null;
  buckets: CalibrationBucket[];
  outcome?: string;
  generated_at?: string;
}

export interface GateCheck {
  name: string;
  passed: boolean;
  measured: unknown;
  target: string;
}

export interface GoldenReport {
  generated_at: string;
  duration_s: number;
  backend: { ai_backend: string; reasoner: string; embedder: string; reranker: string; platform: string };
  corpus: { documents: number; chunks: number; labelable_documents: number; signature: string };
  golden: { set: string; items: number; by_category: Record<string, number>; corpus_signature: string | null };
  retrieval: {
    chunks: RetrievalMeans;
    documents: RetrievalMeans;
    by_category: Record<string, RetrievalMeans>;
    latency_ms: { p50: number | null; p95: number | null };
  };
  generation: {
    answered: number;
    abstained_when_answerable: number | null;
    abstained_when_expected: number | null;
    cited_gold_rate: number | null;
    grade_match_rate: number | null;
    contradiction: { expected: number; detected: number; precision: number | null; recall: number | null };
    grounding_support_mean: number | null;
    rewrites_per_answer: number | null;
    generation_mode: Record<string, number>;
    latency_ms: { p50: number | null; p95: number | null };
    blocked: string[];
    errors: string[];
  };
  calibration: CalibrationCurve;
  judge: {
    ran: boolean;
    reason?: string;
    model?: string;
    scored?: number;
    means?: Record<string, number | null>;
  };
  safety: {
    total: number;
    total_passed: number;
    overall_rate: number;
    gate_passed: boolean;
    categories: Record<string, { total: number; passed: number; failures: string[] }>;
  };
  voice: { ran: boolean; reason?: string; source?: string; first_audio?: { p50: number | null; p95: number | null } };
  gate?: { level: string; checks: GateCheck[]; failed: string[]; passed: boolean };
}

export interface AblationRow {
  config: number;
  description: string;
  subset?: string;
  items?: number;
  recall_at_10?: number | null;
  recall_at_10_documents?: number | null;
  mrr?: number | null;
  ndcg_at_10?: number | null;
  faithfulness?: number | null;
  faithfulness_source?: string;
  abstained_when_answerable?: number | null;
  cited_gold_rate?: number | null;
  contradiction?: { expected: number; detected: number; precision: number | null; recall: number | null };
  skipped?: string;
}

export interface AblationReport {
  generated_at: string;
  duration_s: number;
  backend: GoldenReport["backend"];
  corpus: GoldenReport["corpus"] & { fixed_chunks: number };
  golden: { set: string; items: number; pico_eligible: number; by_category: Record<string, number> };
  rows: AblationRow[];
}

export interface CalibrationReport {
  generated_at: string;
  curves: Record<string, CalibrationCurve & { backend?: GoldenReport["backend"] }>;
  notes: string[];
  recommendation: {
    retune: boolean;
    drift_threshold: number;
    min_bucket: number;
    reasons: string[];
    nominal: Record<string, number>;
    proposed_nominal: Record<string, number>;
  };
}

export interface LoadEndpoint {
  method: string;
  requests: number;
  failures: number;
  error_rate: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  avg_ms: number | null;
  max_ms: number | null;
  rps: number;
}

export interface LoadStage {
  users: number;
  seconds: number;
  total: LoadEndpoint;
  asks: number;
  cache_hit_rate: number | null;
  first_event: { count: number; p50_ms: number | null; p95_ms: number | null; p99_ms: number | null };
  by_endpoint: Record<string, LoadEndpoint>;
  failures: { name: string; error: string; count: number }[];
  broke?: string | null;
}

/** evals/results/load.json — written by `python -m evals.load.run`. */
export interface LoadReport {
  generated_at: string;
  target: string;
  backend: GoldenReport["backend"];
  machine: { platform: string; processor: string; cpu_count: number | null; python: string };
  questions: number;
  criteria: { max_error_rate: number; p95_limit_ms: number };
  sustained: LoadStage;
  ramp: LoadStage[];
  breaking_point: { users: number; reason: string } | null;
  capacity_users: number | null;
  notes: string[];
}

export const pct = (v: number | null | undefined, digits = 0): string =>
  v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`;

export const num = (v: number | null | undefined, digits = 3): string =>
  v === null || v === undefined ? "—" : v.toFixed(digits);
