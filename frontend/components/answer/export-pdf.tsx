"use client";

import { Document, Page, StyleSheet, Text, View, pdf } from "@react-pdf/renderer";
import { toast } from "sonner";

import type { AnswerModel } from "@/components/answer/answer-model";
import { vancouver } from "@/lib/citations";
import { formatDate, splitSentences } from "@/lib/text";

/**
 * Spec #19(a): a formatted PDF with full citations. Rendered client-side, so
 * nothing leaves the browser; opened in a new tab where the viewer can print
 * or save it.
 */

const styles = StyleSheet.create({
  page: { padding: 48, fontSize: 10.5, fontFamily: "Helvetica", color: "#111827", lineHeight: 1.45 },
  brand: { fontSize: 8, color: "#6b7280", marginBottom: 14, letterSpacing: 0.5 },
  title: { fontSize: 16, fontFamily: "Helvetica-Bold", marginBottom: 6 },
  meta: { fontSize: 9, color: "#4b5563", marginBottom: 14 },
  badge: { fontFamily: "Helvetica-Bold" },
  section: { fontSize: 9, fontFamily: "Helvetica-Bold", color: "#374151", marginTop: 14, marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.6 },
  para: { marginBottom: 6 },
  marker: { fontFamily: "Helvetica-Bold", color: "#1d4ed8" },
  refs: { marginTop: 4 },
  ref: { marginBottom: 4, fontSize: 9.5 },
  conflict: { borderLeft: "2 solid #b45309", paddingLeft: 8, marginBottom: 8, color: "#78350f" },
  table: { borderTop: "1 solid #d1d5db", marginTop: 4 },
  row: { flexDirection: "row", borderBottom: "1 solid #e5e7eb" },
  cellHead: { flex: 1, padding: 5, fontFamily: "Helvetica-Bold", fontSize: 9, backgroundColor: "#f3f4f6" },
  cell: { flex: 1, padding: 5, fontSize: 9 },
  cellMuted: { color: "#6b7280", fontStyle: "italic" },
  footer: { position: "absolute", bottom: 28, left: 48, right: 48, fontSize: 8, color: "#6b7280", borderTop: "1 solid #e5e7eb", paddingTop: 6 },
});

function AnswerDocument({ answer, generatedAt }: { answer: AnswerModel; generatedAt: string }) {
  const sentences = splitSentences(answer.content);
  const refs = [...answer.citations].sort((a, b) => a.marker - b.marker);
  return (
    <Document title={answer.query} author="ClinicalContext" subject="Evidence-grounded clinical answer">
      <Page size="A4" style={styles.page}>
        <Text style={styles.brand}>CLINICALCONTEXT · EVIDENCE-GROUNDED ANSWER</Text>
        <Text style={styles.title}>{answer.query}</Text>
        <Text style={styles.meta}>
          {answer.confidence && (
            <>
              Confidence: <Text style={styles.badge}>{answer.confidence}</Text> ·{" "}
            </>
          )}
          Evidence grade: <Text style={styles.badge}>{answer.evidenceGrade ?? "ungraded"}</Text>
          {answer.abstained ? " · ABSTAINED" : ""}
          {answer.contradiction.detected ? " · Sources disagree" : ""}
          {answer.answeredAt ? ` · Answered ${formatDate(answer.answeredAt)}` : ""}
        </Text>

        {answer.contradiction.detected && (
          <View style={styles.conflict}>
            <Text style={{ fontFamily: "Helvetica-Bold" }}>The sources disagree.</Text>
            {answer.contradiction.positions.map((p, i) => (
              <Text key={i}>
                • {p.stance}
                {p.year ? ` (${p.year})` : ""} — sources [{p.markers.join(", ")}]
              </Text>
            ))}
            {answer.contradiction.explanation ? <Text>{answer.contradiction.explanation}</Text> : null}
          </View>
        )}

        <Text style={styles.section}>Answer</Text>
        <View>
          {sentences.map((s, i) => (
            <Text key={i} style={styles.para}>
              {renderMarkers(s)}
            </Text>
          ))}
        </View>

        {answer.comparison && (
          <>
            <Text style={styles.section}>Comparison</Text>
            <View style={styles.table}>
              <View style={styles.row}>
                <Text style={styles.cellHead}>Option</Text>
                {answer.comparison.outcomes.map((o) => (
                  <Text key={o} style={styles.cellHead}>
                    {o}
                  </Text>
                ))}
              </View>
              {answer.comparison.entities.map((entity) => (
                <View key={entity} style={styles.row} wrap={false}>
                  <Text style={[styles.cell, { fontFamily: "Helvetica-Bold" }]}>{entity}</Text>
                  {answer.comparison?.outcomes.map((outcome) => {
                    const c = answer.comparison?.cells.find((x) => x.entity === entity && x.outcome === outcome);
                    return (
                      <Text key={outcome} style={c?.sufficient ? styles.cell : [styles.cell, styles.cellMuted]}>
                        {c?.sufficient
                          ? `${c.summary} [${c.citations.join(", ")}]${c.evidence_grade ? ` (Grade ${c.evidence_grade})` : ""}`
                          : "Insufficient evidence"}
                      </Text>
                    );
                  })}
                </View>
              ))}
            </View>
          </>
        )}

        {refs.length > 0 && (
          <>
            <Text style={styles.section}>References (Vancouver)</Text>
            <View style={styles.refs}>
              {refs.map((c) => (
                <Text key={c.marker} style={styles.ref}>
                  {vancouver(c)}
                  {c.evidence_grade ? `  [Grade ${c.evidence_grade}]` : ""}
                </Text>
              ))}
            </View>
          </>
        )}

        <Text style={styles.footer} fixed>
          Generated {generatedAt} · model {answer.model ?? "—"}
          {answer.promptVersion ? ` · prompts ${answer.promptVersion}` : ""} · This document summarises published
          evidence and does not replace clinical judgement.
        </Text>
      </Page>
    </Document>
  );
}

function renderMarkers(sentence: string) {
  const parts: Array<string | { m: string }> = [];
  let last = 0;
  for (const match of sentence.matchAll(/\[(\d{1,3})\]/g)) {
    const idx = match.index ?? 0;
    if (idx > last) parts.push(sentence.slice(last, idx));
    parts.push({ m: match[0] });
    last = idx + match[0].length;
  }
  if (last < sentence.length) parts.push(sentence.slice(last));
  return parts.map((p, i) =>
    typeof p === "string" ? (
      <Text key={i}>{p}</Text>
    ) : (
      <Text key={i} style={styles.marker}>
        {p.m}
      </Text>
    ),
  );
}

export async function exportAnswerPdf(answer: AnswerModel, slug: string): Promise<void> {
  try {
    const generatedAt = new Date().toLocaleString();
    const blob = await pdf(<AnswerDocument answer={answer} generatedAt={generatedAt} />).toBlob();
    const url = URL.createObjectURL(blob);
    const opened = window.open(url, "_blank", "noopener");
    if (!opened) {
      const a = document.createElement("a");
      a.href = url;
      a.download = `${slug}.pdf`;
      a.click();
    }
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "Could not build the PDF.");
  }
}
