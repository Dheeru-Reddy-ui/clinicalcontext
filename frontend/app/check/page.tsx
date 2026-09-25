"use client";

import { Stethoscope } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";

import { Logo } from "@/components/brand/logo";
import { AssistantLauncher } from "@/components/chat/assistant-launcher";
import { SymptomCheck } from "@/components/treatment/symptom-check";
import { buttonVariants } from "@/components/ui/button";
import { warmApi } from "@/lib/signup";

function Check() {
  const params = useSearchParams();
  return <SymptomCheck token={null} initialComplaint={params.get("complaint")} chatHref={null} />;
}

/**
 * The symptom check without an account: the same engine as the Treatment
 * tab, reached from the website's chatbot. Nothing is stored.
 */
export default function PublicCheckPage() {
  useEffect(warmApi, []);
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-5 px-4 pt-8 pb-24 md:px-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link href="/" className="inline-flex rounded-md text-sm">
            <Logo markClassName="size-6" />
          </Link>
          <h1 className="mt-4 flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <Stethoscope className="size-6 text-primary" aria-hidden /> Symptom check
          </h1>
          <p className="text-sm text-muted-foreground">
            Warning signs first, then what to do and safe medicine doses — from NHS, NICE and WHO
            guidance. No account needed; nothing is stored.
          </p>
        </div>
        <Link href="/signup" className={buttonVariants({ variant: "outline", size: "sm" })}>
          Create a free account
        </Link>
      </header>
      <Suspense fallback={null}>
        <Check />
      </Suspense>
      <AssistantLauncher mode="public" checkHref="/check" />
    </main>
  );
}
