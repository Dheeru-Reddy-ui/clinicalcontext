import { Suspense } from "react";

import { Tutor } from "@/components/learn/tutor/tutor";

export const metadata = { title: "AI Tutor" };

export default function TutorPage() {
  return (
    <Suspense>
      <Tutor />
    </Suspense>
  );
}
