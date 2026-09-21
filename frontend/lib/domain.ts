/**
 * Answer-domain types the API carries as opaque JSON.
 *
 * The OpenAPI-generated `lib/api-types.ts` is the source of truth for every
 * endpoint's envelope. Three things it cannot describe live here, mirrored by
 * hand from the backend and kept in step with it:
 *
 *   - `Citation` / `Contradiction`      ← backend/app/schemas/answer.py
 *   - `Reasoning` (answers.reasoning)    ← backend/app/repositories/answers.py
 *   - the SSE event stream of POST /queries ← backend/app/services/ask.py
 *
 * Everything crossing the wire is narrowed through the guards at the bottom;
 * nothing in the UI reads an untyped field.
 */

import type { components } from "@/lib/api-types";

export type Schemas = components["schemas"];

export type Confidence = "high" | "moderate" | "low";
export type EvidenceGrade = "A" | "B" | "C" | "D";
export type RetrievalGrade = "sufficient" | "insufficient" | "irrelevant";
export type QueryType =
  | "therapy"
  | "diagnosis"
  | "prognosis"
  | "etiology"
  | "harm"
  | "guideline_comparison"
  | "other";
export type Stance = "supports" | "opposes" | "neutral";
export type FeedbackReason = Schemas["FeedbackRequest"]["reason"];

export interface Citation {
  marker: number;
  chunk_id: string;
  document_id: string;
  title: string | null;
  section: string | null;
  publication_date: string | null; // ISO date
  evidence_grade: EvidenceGrade | null;
  study_type: string | null;
  journal: string | null;
  pmid: string | null;
  doi: string | null;
  url: string | null;
  passage: string;
}

export interface ContradictionPosition {
  stance: string;
  markers: number[];
  year: number | null;
}

export interface Contradiction {
  detected: boolean;
  positions: ContradictionPosition[];
  axis: "temporal" | "population" | "endpoint" | "unclear" | "none";
  explanation: string;
}

/** answers.reasoning — the structured extras that survive persistence. */
export interface Reasoning {
  contradiction: Contradiction;
  sub_questions: string[];
  retrieval_grade: RetrievalGrade;
  rewrite_count: number;
  generation_mode: "llm" | "extractive";
  escalation_banner: string | null;
  prompt_versions: Record<string, string>;
  query_type: QueryType;
  is_multi_hop: boolean;
}

export interface ComparisonCell {
  entity: string;
  outcome: string;
  summary: string;
  citations: number[];
  evidence_grade: EvidenceGrade | null;
  sufficient: boolean;
}

export interface ComparisonTable {
  entities: string[];
  outcomes: string[];
  cells: ComparisonCell[];
}

/** The terminal `result` event of the stream (and the cached variant). */
export interface AnswerResultData {
  answer_id: string;
  query_id: string;
  answer: string;
  abstained: boolean;
  confidence: Confidence;
  evidence_grade: EvidenceGrade | null;
  contradiction: Contradiction;
  citations: Citation[];
  query_type: QueryType;
  is_multi_hop: boolean;
  sub_questions: string[];
  generation_mode: "llm" | "extractive";
  model: string;
  reasoning: Reasoning;
  cached: boolean;
  latency_ms?: number;
  input_tokens?: number;
  output_tokens?: number;
  cost_usd: number;
  cache_similarity?: number;
  cache_saved_usd?: number;
  comparison_table?: ComparisonTable;
}

// -- the SSE stream ------------------------------------------------------------------

export type ReasoningStage =
  | "classifying"
  | "decomposing"
  | "searching"
  | "searched"
  | "grading"
  | "rewriting"
  | "checking_conflict"
  | "conflict_found"
  | "generating"
  | "verifying"
  | "grounding_failed"
  | "grounding_pruned"
  | "abstaining"
  | "done"
  | "comparing"
  | "comparison_result";

export type StreamEvent =
  | {
      stage: "accepted";
      message: string;
      data: { query_id: string; session_id: string; contextualized_query: string };
    }
  | {
      stage: "blocked";
      message: string;
      data: { blocked_by: "phi" | "scope" | string | null; code: string | null };
    }
  | { stage: "escalation"; message: string; data: Record<string, never> }
  | { stage: "token"; message: string; data: { text: string } }
  | { stage: "result"; message: string; data: AnswerResultData }
  | { stage: "error"; message: string; data: { type: string } }
  | { stage: ReasoningStage; message: string; data: Record<string, unknown> };

export type GuardrailKind = "phi" | "scope" | "red_flag";

// -- narrowing ----------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Parse one `data:` payload. Unknown shapes are surfaced, never silently dropped. */
export function parseStreamEvent(raw: string): StreamEvent {
  const value: unknown = JSON.parse(raw);
  if (!isRecord(value) || typeof value.stage !== "string") {
    throw new Error("malformed stream event");
  }
  const message = typeof value.message === "string" ? value.message : "";
  const data = isRecord(value.data) ? value.data : {};
  // Structural fields are validated where the UI depends on them; the
  // backend is the same codebase, so this is a guard against transport
  // corruption, not a second schema.
  return { stage: value.stage, message, data } as StreamEvent;
}

export function asCitations(value: unknown): Citation[] {
  return Array.isArray(value)
    ? value.filter(
        (c): c is Citation =>
          isRecord(c) && typeof c.marker === "number" && typeof c.passage === "string",
      )
    : [];
}

export function asContradiction(value: unknown): Contradiction {
  if (!isRecord(value)) return EMPTY_CONTRADICTION;
  return {
    detected: value.detected === true,
    positions: Array.isArray(value.positions)
      ? value.positions.filter(
          (p): p is ContradictionPosition => isRecord(p) && Array.isArray(p.markers),
        )
      : [],
    axis: (value.axis as Contradiction["axis"]) ?? "none",
    explanation: typeof value.explanation === "string" ? value.explanation : "",
  };
}

export const EMPTY_CONTRADICTION: Contradiction = {
  detected: false,
  positions: [],
  axis: "none",
  explanation: "",
};

export function asReasoning(value: unknown): Reasoning {
  const r = isRecord(value) ? value : {};
  return {
    contradiction: asContradiction(r.contradiction),
    sub_questions: Array.isArray(r.sub_questions)
      ? r.sub_questions.filter((s): s is string => typeof s === "string")
      : [],
    retrieval_grade: (r.retrieval_grade as RetrievalGrade) ?? "insufficient",
    rewrite_count: typeof r.rewrite_count === "number" ? r.rewrite_count : 0,
    generation_mode: r.generation_mode === "llm" ? "llm" : "extractive",
    escalation_banner: typeof r.escalation_banner === "string" ? r.escalation_banner : null,
    prompt_versions: isRecord(r.prompt_versions)
      ? Object.fromEntries(
          Object.entries(r.prompt_versions).filter(
            (e): e is [string, string] => typeof e[1] === "string",
          ),
        )
      : {},
    query_type: (r.query_type as QueryType) ?? "other",
    is_multi_hop: r.is_multi_hop === true,
  };
}

export function asComparisonTable(value: unknown): ComparisonTable | null {
  if (!isRecord(value) || !Array.isArray(value.cells)) return null;
  return value as unknown as ComparisonTable;
}

/**
 * Stance of each citation marker, derived from the contradiction detector.
 * Markers in a "supports" position are green, "against" red, the rest neutral.
 */
export function stanceByMarker(contradiction: Contradiction): Map<number, Stance> {
  const map = new Map<number, Stance>();
  if (!contradiction.detected) return map;
  for (const position of contradiction.positions) {
    const stance: Stance = /against|not support|oppos|no benefit|inferior/i.test(
      position.stance,
    )
      ? "opposes"
      : "supports";
    for (const marker of position.markers) map.set(marker, stance);
  }
  return map;
}
