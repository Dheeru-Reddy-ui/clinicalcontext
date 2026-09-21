/**
 * Small, dependency-free text helpers shared by the answer renderer, the
 * citation panel, the diff view, and the exporters.
 */

const SENTENCE_BOUNDARY = /(?<=[.!?])\s+(?=[A-Z0-9(\["])/;
const CITATION = /\[(\d{1,3})\]/g;
const WORD = /[a-z0-9]+/g;

const STOP = new Set(
  "the a an of to in and or for with is are was were be on at by as that this does do what which how much many should i my patient given these vs versus".split(
    " ",
  ),
);

export function splitSentences(text: string): string[] {
  return text
    .split(SENTENCE_BOUNDARY)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function contentTokens(text: string): Set<string> {
  const out = new Set<string>();
  for (const m of text.toLowerCase().matchAll(WORD)) {
    const t = m[0];
    if (t.length > 2 && !STOP.has(t)) out.add(t);
  }
  return out;
}

/** Jaccard-style overlap in [0, 1], on content words. */
export function overlap(a: string, b: string): number {
  const ta = contentTokens(a);
  const tb = contentTokens(b);
  if (ta.size === 0 || tb.size === 0) return 0;
  let shared = 0;
  for (const t of ta) if (tb.has(t)) shared += 1;
  return shared / Math.min(ta.size, tb.size);
}

/** The answer sentences that cite marker `n` (each may cite several). */
export function claimsCiting(answer: string, marker: number): string[] {
  return splitSentences(answer).filter((s) =>
    Array.from(s.matchAll(CITATION)).some((m) => Number(m[1]) === marker),
  );
}

/**
 * Which sentence of the passage supports the claim? The one with the most
 * shared vocabulary. Returns character offsets into the passage so the UI can
 * highlight exactly that span. Falls back to the first sentence when nothing
 * overlaps — there is always *a* highlighted span, so the reader knows where
 * to start.
 */
export function bestSupportingSpan(
  passage: string,
  claims: string[],
): { start: number; end: number; score: number } {
  const sentences = splitSentences(passage);
  if (sentences.length === 0) return { start: 0, end: passage.length, score: 0 };
  let best = { index: 0, score: -1 };
  sentences.forEach((sentence, index) => {
    const score = claims.reduce((max, claim) => Math.max(max, overlap(sentence, claim)), 0);
    if (score > best.score) best = { index, score };
  });
  const target = sentences[best.index] ?? sentences[0] ?? "";
  const start = Math.max(0, passage.indexOf(target));
  return { start, end: start + target.length, score: Math.max(best.score, 0) };
}

/** Strip `[n]` markers — for clipboard text, titles, and exports. */
export function stripCitations(text: string): string {
  return text.replace(CITATION, "").replace(/\s{2,}/g, " ").trim();
}

export function markersIn(text: string): number[] {
  const seen = new Set<number>();
  for (const m of text.matchAll(CITATION)) seen.add(Number(m[1]));
  return Array.from(seen).sort((a, b) => a - b);
}

export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value === 0) return "$0.00";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}

export function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms`;
}

export function formatDate(iso: string | null | undefined, opts?: Intl.DateTimeFormatOptions): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  // toLocaleString accepts date *and* time options; toLocaleDateString throws on timeStyle.
  return d.toLocaleString(undefined, opts ?? { year: "numeric", month: "short", day: "numeric" });
}

export function yearOf(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const y = Number(iso.slice(0, 4));
  return Number.isFinite(y) ? y : null;
}

export function humanStudyType(value: string | null | undefined): string {
  if (!value) return "Unclassified";
  return value.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}
