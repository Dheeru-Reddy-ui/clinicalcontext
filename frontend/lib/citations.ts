/**
 * Reference formatting for export (spec #19): Vancouver and AMA for the
 * clipboard, BibTeX and RIS for reference managers.
 *
 * Citations carry title, journal, year, PMID and DOI. Authors are not part of
 * the citation payload (they live on the document), so entries are formatted
 * without an author list rather than with a fabricated one — an honest
 * partial reference beats a plausible wrong one. The PubMed/DOI identifiers
 * let any reference manager complete the record.
 */

import type { Citation } from "@/lib/domain";
import { yearOf } from "@/lib/text";

function clean(s: string | null | undefined): string {
  return (s ?? "").replace(/\s+/g, " ").trim();
}

function title(c: Citation): string {
  return clean(c.title) || "Untitled";
}

function ids(c: Citation): string {
  return [c.doi && `doi:${c.doi}`, c.pmid && `PMID: ${c.pmid}`].filter(Boolean).join(". ");
}

/** Vancouver (ICMJE), numbered in marker order. */
export function vancouver(c: Citation): string {
  const y = yearOf(c.publication_date);
  const parts = [title(c) + ".", clean(c.journal) && `${clean(c.journal)}.`, y && `${y}.`, ids(c)];
  return `${c.marker}. ${parts.filter(Boolean).join(" ")}`.trim();
}

/** AMA (11th ed.): title. *Journal*. Year. doi */
export function ama(c: Citation): string {
  const y = yearOf(c.publication_date);
  const parts = [title(c) + ".", clean(c.journal) && `${clean(c.journal)}.`, y && `${y}.`, ids(c)];
  return `${c.marker}. ${parts.filter(Boolean).join(" ")}`.trim();
}

function bibKey(c: Citation): string {
  const word = title(c).split(/\s+/).find((w) => w.length > 3) ?? "ref";
  return `${word.replace(/[^A-Za-z0-9]/g, "").toLowerCase()}${yearOf(c.publication_date) ?? ""}_${c.marker}`;
}

export function bibtex(c: Citation): string {
  const y = yearOf(c.publication_date);
  const fields = [
    `  title = {${title(c)}}`,
    clean(c.journal) && `  journal = {${clean(c.journal)}}`,
    y && `  year = {${y}}`,
    c.doi && `  doi = {${c.doi}}`,
    c.pmid && `  pmid = {${c.pmid}}`,
    c.url && `  url = {${c.url}}`,
  ].filter(Boolean);
  return `@article{${bibKey(c)},\n${fields.join(",\n")}\n}`;
}

export function ris(c: Citation): string {
  const y = yearOf(c.publication_date);
  const lines = [
    "TY  - JOUR",
    `TI  - ${title(c)}`,
    clean(c.journal) && `JO  - ${clean(c.journal)}`,
    y && `PY  - ${y}`,
    c.doi && `DO  - ${c.doi}`,
    c.pmid && `AN  - ${c.pmid}`,
    c.url && `UR  - ${c.url}`,
    "ER  - ",
  ].filter(Boolean);
  return lines.join("\n");
}

export type CitationStyle = "vancouver" | "ama";

export function formatReferenceList(citations: Citation[], style: CitationStyle): string {
  const sorted = [...citations].sort((a, b) => a.marker - b.marker);
  return sorted.map(style === "ama" ? ama : vancouver).join("\n");
}

export function exportBibtex(citations: Citation[]): string {
  return [...citations].sort((a, b) => a.marker - b.marker).map(bibtex).join("\n\n") + "\n";
}

export function exportRis(citations: Citation[]): string {
  return [...citations].sort((a, b) => a.marker - b.marker).map(ris).join("\n") + "\n";
}

/** Trigger a client-side file download (a data URL, no server round-trip). */
export function downloadText(filename: string, content: string, mime = "text/plain"): void {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
