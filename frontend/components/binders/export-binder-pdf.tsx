"use client";

import { Document, Page, StyleSheet, Text, View, pdf } from "@react-pdf/renderer";
import { toast } from "sonner";

import { asCitations } from "@/lib/domain";
import type { Schemas } from "@/lib/domain";
import { vancouver } from "@/lib/citations";
import { formatDate, splitSentences, yearOf } from "@/lib/text";

const s = StyleSheet.create({
  page: { padding: 48, fontSize: 10.5, fontFamily: "Helvetica", color: "#111827", lineHeight: 1.45 },
  brand: { fontSize: 8, color: "#6b7280", marginBottom: 12, letterSpacing: 0.5 },
  title: { fontSize: 18, fontFamily: "Helvetica-Bold", marginBottom: 4 },
  meta: { fontSize: 9, color: "#4b5563", marginBottom: 16 },
  item: { marginBottom: 18, paddingBottom: 12, borderBottom: "1 solid #e5e7eb" },
  kicker: { fontSize: 8, color: "#6b7280", marginBottom: 4, textTransform: "uppercase", letterSpacing: 0.6 },
  h2: { fontSize: 12, fontFamily: "Helvetica-Bold", marginBottom: 4 },
  small: { fontSize: 9, color: "#4b5563", marginBottom: 6 },
  para: { marginBottom: 4 },
  note: { marginTop: 6, paddingLeft: 8, borderLeft: "2 solid #d1d5db", fontSize: 9.5 },
  noteMeta: { fontSize: 8, color: "#6b7280" },
  refs: { marginTop: 6 },
  ref: { fontSize: 9, marginBottom: 3 },
  footer: { position: "absolute", bottom: 28, left: 48, right: 48, fontSize: 8, color: "#6b7280", borderTop: "1 solid #e5e7eb", paddingTop: 6 },
});

function BinderDocument({ detail, generatedAt }: { detail: Schemas["BinderDetailOut"]; generatedAt: string }) {
  const { binder, items } = detail;
  return (
    <Document title={binder.title} author="ClinicalContext">
      <Page size="A4" style={s.page}>
        <Text style={s.brand}>CLINICALCONTEXT · EVIDENCE BINDER</Text>
        <Text style={s.title}>{binder.title}</Text>
        <Text style={s.meta}>
          {binder.description ? `${binder.description} · ` : ""}
          {items.length} item{items.length === 1 ? "" : "s"} · curated by {binder.created_by_name ?? "—"} · {formatDate(binder.created_at)}
        </Text>

        {items.map((item, i) => (
          <View key={item.id} style={s.item} wrap>
            <Text style={s.kicker}>
              {i + 1} · {item.item_type}
            </Text>
            {item.answer && (
              <>
                <Text style={s.h2}>{item.answer.query}</Text>
                <Text style={s.small}>
                  Confidence {item.answer.confidence ?? "—"} · Grade {item.answer.evidence_grade ?? "—"}
                  {item.answer.abstained ? " · abstained" : ""}
                  {item.answer.has_contradiction ? " · sources disagree" : ""}
                </Text>
                {splitSentences(item.answer.content).map((sentence, j) => (
                  <Text key={j} style={s.para}>
                    {sentence}
                  </Text>
                ))}
                {asCitations(item.answer.citations).length > 0 && (
                  <View style={s.refs}>
                    {asCitations(item.answer.citations)
                      .sort((a, b) => a.marker - b.marker)
                      .map((c) => (
                        <Text key={c.marker} style={s.ref}>
                          {vancouver(c)}
                        </Text>
                      ))}
                  </View>
                )}
              </>
            )}
            {item.passage && (
              <>
                <Text style={s.h2}>{item.passage.document_title}</Text>
                <Text style={s.small}>
                  {[item.passage.journal, yearOf(item.passage.publication_date), item.passage.study_type?.replace(/_/g, " "), item.passage.evidence_grade && `Grade ${item.passage.evidence_grade}`]
                    .filter(Boolean)
                    .join(" · ")}
                  {item.passage.pmid ? ` · PMID ${item.passage.pmid}` : ""}
                  {item.passage.doi ? ` · doi:${item.passage.doi}` : ""}
                </Text>
                <Text style={s.para}>{item.passage.content}</Text>
              </>
            )}
            {(item.annotations ?? []).map((a) => (
              <View key={a.id} style={s.note}>
                <Text style={s.noteMeta}>
                  {a.author_name ?? "Colleague"} · {formatDate(a.created_at)}
                  {a.highlight_range && item.passage
                    ? ` · on “${item.passage.content.slice(a.highlight_range.start ?? 0, a.highlight_range.end ?? 0).slice(0, 60)}…”`
                    : ""}
                </Text>
                <Text>{a.body}</Text>
                {(a.replies ?? []).map((r) => (
                  <View key={r.id} style={{ marginTop: 3, paddingLeft: 8 }}>
                    <Text style={s.noteMeta}>
                      ↳ {r.author_name ?? "Colleague"} · {formatDate(r.created_at)}
                    </Text>
                    <Text>{r.body}</Text>
                  </View>
                ))}
              </View>
            ))}
          </View>
        ))}
        <Text style={s.footer} fixed>
          Generated {generatedAt} · ClinicalContext · summarises published evidence; does not replace clinical judgement.
        </Text>
      </Page>
    </Document>
  );
}

export async function exportBinderPdf(detail: Schemas["BinderDetailOut"]): Promise<void> {
  try {
    const blob = await pdf(<BinderDocument detail={detail} generatedAt={new Date().toLocaleString()} />).toBlob();
    const url = URL.createObjectURL(blob);
    if (!window.open(url, "_blank", "noopener")) {
      const a = document.createElement("a");
      a.href = url;
      a.download = `${detail.binder.title.replace(/[^A-Za-z0-9]+/g, "-").toLowerCase()}.pdf`;
      a.click();
    }
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "Could not build the PDF.");
  }
}
