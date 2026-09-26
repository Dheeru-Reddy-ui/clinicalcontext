"use client";

import { useParams } from "next/navigation";

import { PaperView } from "@/components/learn/papers/paper-view";

export default function PaperPage() {
  const { id } = useParams<{ id: string }>();
  return <PaperView key={id} id={id} />;
}
