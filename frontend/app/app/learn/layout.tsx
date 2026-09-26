import type { ReactNode } from "react";

import { LearnNav } from "@/components/learn/learn-nav";

/** Learn: subjects, the AI tutor, the note summarizer and Ask-this-Paper. */
export default function LearnLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <LearnNav />
      {children}
    </>
  );
}
