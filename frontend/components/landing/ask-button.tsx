"use client";

import type { ReactNode } from "react";

import { openAssistant } from "@/lib/assistant-events";

/** Opens the page's health assistant (components/chat/assistant-launcher). */
export function AskAssistantButton({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <button type="button" onClick={openAssistant} className={className} data-testid="home-ask">
      {children}
    </button>
  );
}
