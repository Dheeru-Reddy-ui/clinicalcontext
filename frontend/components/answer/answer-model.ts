/**
 * One shape for "an answer", however it reached the UI.
 *
 * The live stream, the persisted query detail, a binder snapshot, and the
 * public permalink all carry the same facts in slightly different envelopes.
 * Normalising here means the citation panel, the contradiction view, the
 * timeline and the footer are written once and behave identically everywhere.
 */

import type { QueryDetail } from "@/lib/api-client";
import {
  asCitations,
  asComparisonTable,
  asContradiction,
  asReasoning,
  type AnswerResultData,
  type Citation,
  type ComparisonTable,
  type Confidence,
  type Contradiction,
  type EvidenceGrade,
  type Reasoning,
  type Schemas,
} from "@/lib/domain";

export interface AnswerModel {
  answerId: string | null;
  queryId: string | null;
  query: string;
  /** What was actually searched after multi-turn resolution, when it differs. */
  contextualizedQuery: string | null;
  content: string;
  citations: Citation[];
  confidence: Confidence | null;
  evidenceGrade: EvidenceGrade | null;
  abstained: boolean;
  contradiction: Contradiction;
  reasoning: Reasoning;
  comparison: ComparisonTable | null;
  /** Provenance for the honesty footer. */
  model: string | null;
  promptVersion: string | null;
  latencyMs: number | null;
  inputTokens: number | null;
  outputTokens: number | null;
  costUsd: number | null;
  cached: boolean;
  cacheSavedUsd: number | null;
  answeredAt: string | null;
}

function asConfidence(value: unknown): Confidence | null {
  return value === "high" || value === "moderate" || value === "low" ? value : null;
}

function asGrade(value: unknown): EvidenceGrade | null {
  return value === "A" || value === "B" || value === "C" || value === "D" ? value : null;
}

export function fromStreamResult(query: string, data: AnswerResultData): AnswerModel {
  const reasoning = asReasoning(data.reasoning);
  return {
    answerId: data.answer_id,
    queryId: data.query_id,
    query,
    contextualizedQuery: null,
    content: data.answer,
    citations: asCitations(data.citations),
    confidence: asConfidence(data.confidence),
    evidenceGrade: asGrade(data.evidence_grade),
    abstained: data.abstained,
    contradiction: asContradiction(data.contradiction),
    reasoning,
    comparison: asComparisonTable(data.comparison_table),
    model: data.model,
    promptVersion: Object.values(reasoning.prompt_versions).sort().join(", ") || null,
    latencyMs: data.latency_ms ?? null,
    inputTokens: data.input_tokens ?? null,
    outputTokens: data.output_tokens ?? null,
    costUsd: data.cost_usd,
    cached: data.cached,
    cacheSavedUsd: data.cache_saved_usd ?? null,
    answeredAt: new Date().toISOString(),
  };
}

export function fromQueryDetail(detail: QueryDetail): AnswerModel | null {
  const a = detail.answer;
  if (!a) return null;
  const reasoning = asReasoning(a.reasoning);
  return {
    answerId: a.id,
    queryId: detail.query.id,
    query: detail.query.raw_query,
    contextualizedQuery: detail.query.contextualized_query,
    content: a.content,
    citations: asCitations(a.citations),
    confidence: asConfidence(a.confidence),
    evidenceGrade: asGrade(a.evidence_grade),
    abstained: a.abstained,
    contradiction: reasoning.contradiction,
    reasoning,
    comparison: asComparisonTable(a.comparison_table),
    model: a.model,
    promptVersion: a.prompt_version,
    latencyMs: a.latency_ms,
    inputTokens: a.input_tokens,
    outputTokens: a.output_tokens,
    costUsd: a.cost_usd,
    cached: a.cached,
    cacheSavedUsd: a.cache_saved_usd,
    answeredAt: a.created_at,
  };
}

export function fromSnapshot(snapshot: Schemas["AnswerSnapshot"]): AnswerModel {
  const reasoning = asReasoning(snapshot.reasoning);
  return {
    answerId: snapshot.id,
    queryId: null,
    query: snapshot.query,
    contextualizedQuery: null,
    content: snapshot.content,
    citations: asCitations(snapshot.citations),
    confidence: asConfidence(snapshot.confidence),
    evidenceGrade: asGrade(snapshot.evidence_grade),
    abstained: snapshot.abstained,
    contradiction: reasoning.contradiction,
    reasoning,
    comparison: null,
    model: null,
    promptVersion: null,
    latencyMs: null,
    inputTokens: null,
    outputTokens: null,
    costUsd: null,
    cached: false,
    cacheSavedUsd: null,
    answeredAt: snapshot.created_at,
  };
}

export function fromPublic(page: Schemas["PublicAnswerOut"]): AnswerModel {
  const reasoning = asReasoning(page.reasoning);
  return {
    answerId: null,
    queryId: null,
    query: page.query,
    contextualizedQuery: null,
    content: page.content,
    citations: asCitations(page.citations),
    confidence: asConfidence(page.confidence),
    evidenceGrade: asGrade(page.evidence_grade),
    abstained: page.abstained,
    contradiction: reasoning.contradiction,
    reasoning,
    comparison: null,
    model: page.model,
    promptVersion: page.prompt_version,
    latencyMs: null,
    inputTokens: null,
    outputTokens: null,
    costUsd: null,
    cached: false,
    cacheSavedUsd: null,
    answeredAt: page.answered_at,
  };
}
