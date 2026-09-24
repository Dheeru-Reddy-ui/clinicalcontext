/**
 * The symptom check over the wire (backend/app/api/v1/assistant.py): the
 * complaints it covers and one step at a time. Signed in or not — the
 * public routes run the same engine, and neither stores anything.
 */

import { apiFetch } from "@/lib/api";
import type { Schemas } from "@/lib/domain";

export type Complaint = Schemas["ComplaintOut"];
export type Question = Schemas["QuestionOut"];
export type Assessment = Schemas["AssessmentOut"];
export type StepOut = Schemas["TreatmentStepOut"];
export type Medicine = Schemas["MedicineOut"];
export type Urgency = Assessment["urgency"];

export type Condition =
  | "asthma"
  | "kidney_disease"
  | "liver_disease"
  | "stomach_ulcer"
  | "heart_disease"
  | "diabetes"
  | "weak_immunity"
  | "bleeding_disorder"
  | "high_blood_pressure"
  | "lung_disease";

export interface Profile {
  age_years: number;
  sex: "female" | "male" | "other" | null;
  weight_kg: number | null;
  pregnant: boolean;
  breastfeeding: boolean;
  conditions: Condition[];
  allergies: string[];
  medicines: string[];
}

export const CONDITION_LABELS: Record<Condition, string> = {
  asthma: "Asthma",
  kidney_disease: "Kidney disease",
  liver_disease: "Liver disease",
  stomach_ulcer: "Stomach ulcer or bleeding",
  heart_disease: "Heart disease or heart failure",
  diabetes: "Diabetes",
  weak_immunity: "Weak immune system",
  bleeding_disorder: "Bleeding disorder",
  high_blood_pressure: "High blood pressure",
  lung_disease: "Long-term lung disease",
};

export const URGENCY_STYLE: Record<Urgency, { tone: string; label: string }> = {
  emergency: {
    tone: "border-red-700 bg-red-600 text-white",
    label: "Emergency",
  },
  urgent: {
    tone: "border-orange-600/50 bg-orange-50 text-orange-950 dark:bg-orange-950/40 dark:text-orange-100",
    label: "Today",
  },
  soon: {
    tone: "border-amber-500/50 bg-amber-50 text-amber-950 dark:bg-amber-950/40 dark:text-amber-100",
    label: "Within 1–2 days",
  },
  self_care: {
    tone: "border-emerald-600/40 bg-emerald-50 text-emerald-950 dark:bg-emerald-950/40 dark:text-emerald-100",
    label: "Home care",
  },
};

export function listComplaints(token: string | null): Promise<Complaint[]> {
  return token
    ? apiFetch<Complaint[]>("/api/v1/treatment/complaints", { accessToken: token })
    : apiFetch<Complaint[]>("/api/public/treatment/complaints");
}

export function treatmentStep(
  token: string | null,
  complaint: string,
  profile: Profile,
  answers: Record<string, string[]>,
): Promise<StepOut> {
  const body = { complaint, profile, answers };
  return token
    ? apiFetch<StepOut>("/api/v1/treatment/step", { method: "POST", body, accessToken: token })
    : apiFetch<StepOut>("/api/public/treatment/step", { method: "POST", body });
}

export function splitList(text: string): string[] {
  return text
    .split(/[,;\n]/)
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 20);
}

/** A short description of the person and the answers, to continue in chat. */
export function summarise(
  complaint: Complaint,
  profile: Profile,
  question: (id: string) => Question | undefined,
  answers: Record<string, string[]>,
): string {
  const age =
    profile.age_years < 1
      ? `${Math.round(profile.age_years * 12)}-month-old`
      : `${Math.round(profile.age_years)}-year-old`;
  const who = [age, profile.sex && profile.sex !== "other" ? profile.sex : "person"].join(" ");
  const parts = [`${who} with ${complaint.name.toLowerCase()}`];
  for (const [id, chosen] of Object.entries(answers)) {
    const q = question(id);
    if (!q) continue;
    const labels = chosen
      .filter((c) => c !== "none")
      .map((c) => q.options.find((o) => o.id === c)?.label)
      .filter(Boolean);
    if (labels.length) parts.push(labels.join(", ").toLowerCase());
  }
  if (profile.pregnant) parts.push("pregnant");
  if (profile.conditions.length) {
    parts.push(`has ${profile.conditions.map((c) => CONDITION_LABELS[c].toLowerCase()).join(", ")}`);
  }
  if (profile.medicines.length) parts.push(`takes ${profile.medicines.join(", ")}`);
  return parts.join("; ");
}
