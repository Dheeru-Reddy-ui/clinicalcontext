"use client";

import { Stethoscope } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { PrescribingSupport } from "@/components/treatment/prescribing-support";
import { SymptomCheck } from "@/components/treatment/symptom-check";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToken } from "@/hooks/use-api";

function Check() {
  const token = useToken();
  const params = useSearchParams();
  if (!token) return null;
  return <SymptomCheck token={token} initialComplaint={params.get("complaint")} chatHref="/app/chat" />;
}

/**
 * Treatment: the symptom check for patients (danger signs, what to do, safe
 * over-the-counter doses for the person's age) and prescribing support for
 * doctors (guideline regimens, adjustments, interactions — cited).
 */
export default function TreatmentPage() {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5 p-4 md:p-6">
      <header>
        <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
          <Stethoscope className="size-5 text-primary" aria-hidden /> Treatment
        </h1>
        <p className="text-sm text-muted-foreground">
          For patients: a guided check of symptoms, warning signs and safe medicine doses. For
          doctors: evidence-based prescribing support.
        </p>
      </header>
      <Tabs defaultValue="check">
        <TabsList aria-label="Treatment tools">
          <TabsTrigger value="check" data-testid="tab-check">
            Symptom check
          </TabsTrigger>
          <TabsTrigger value="prescribing" data-testid="tab-prescribing">
            Prescribing support (doctors)
          </TabsTrigger>
        </TabsList>
        <TabsContent value="check" className="pt-4">
          <Suspense fallback={null}>
            <Check />
          </Suspense>
        </TabsContent>
        <TabsContent value="prescribing" className="pt-4">
          <PrescribingSupport />
        </TabsContent>
      </Tabs>
    </div>
  );
}
