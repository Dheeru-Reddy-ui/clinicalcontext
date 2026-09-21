"use client";

import type { ReactNode } from "react";

import { AuthProvider } from "@/components/providers/auth-provider";

export default function OnboardingLayout({ children }: { children: ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>;
}
