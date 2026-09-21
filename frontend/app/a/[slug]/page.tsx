import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { PublicAnswer } from "@/app/a/[slug]/public-answer";
import { apiUrl } from "@/lib/api";
import type { Schemas } from "@/lib/domain";

/**
 * Read-only public answer page (spec #20). Rendered on the server so the link
 * previews and loads without JavaScript; no session is involved. A revoked
 * link, an unknown slug, or an org that switched sharing off all come back
 * from the API as 404 — and land on the same clean not-found page here.
 */

async function load(slug: string): Promise<Schemas["PublicAnswerOut"] | null> {
  const response = await fetch(apiUrl(`/api/public/answers/${encodeURIComponent(slug)}`), {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`The answer could not be loaded (${response.status}).`);
  return (await response.json()) as Schemas["PublicAnswerOut"];
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  const page = await load(slug).catch(() => null);
  if (!page) return { title: "Answer not found", robots: { index: false } };
  return {
    title: page.query,
    description: page.content.replace(/\[\d+\]/g, "").slice(0, 160),
    robots: { index: false, follow: false },
  };
}

export default async function PublicAnswerPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const page = await load(slug);
  if (!page) notFound();
  return <PublicAnswer page={page} />;
}
