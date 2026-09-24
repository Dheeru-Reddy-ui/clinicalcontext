"use client";

import { Send } from "lucide-react";
import { useState } from "react";

import { AssistantBubble, UserBubble } from "@/components/chat/message";
import { Composer } from "@/components/chat/composer";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useChat } from "@/hooks/use-chat";

interface Case {
  condition: string;
  age: string;
  sex: string;
  weight: string;
  renal: string;
  hepatic: string;
  pregnancy: string;
  allergies: string;
  medicines: string;
  question: string;
}

const EMPTY: Case = {
  condition: "",
  age: "",
  sex: "",
  weight: "",
  renal: "",
  hepatic: "",
  pregnancy: "",
  allergies: "",
  medicines: "",
  question: "",
};

export function caseToQuestion(c: Case): string {
  const facts = [
    c.age && `age ${c.age}`,
    c.sex && c.sex,
    c.weight && `weight ${c.weight} kg`,
    c.renal && `renal function: ${c.renal}`,
    c.hepatic && `hepatic: ${c.hepatic}`,
    c.pregnancy && `pregnancy/lactation: ${c.pregnancy}`,
    c.allergies && `allergies: ${c.allergies}`,
    c.medicines && `current medicines: ${c.medicines}`,
  ].filter(Boolean);
  const ask =
    c.question.trim() ||
    "What is the guideline-recommended first-line treatment, with dose, route, frequency and duration, alternatives, adjustments for these factors, interactions to check, monitoring and red flags?";
  return `Prescribing support for ${c.condition.trim()}${facts.length ? ` (${facts.join("; ")})` : ""}. ${ask}`;
}

/**
 * For doctors: a case in structured fields, turned into one precise question
 * for the assistant in its clinician voice — guideline regimens with doses
 * as the sources state them, adjustments, interactions and monitoring, all
 * cited. The prescribing decision stays with the clinician; nothing here
 * identifies the patient.
 */
export function PrescribingSupport() {
  const chat = useChat({ kind: "treatment", audience: "clinician" });
  const [draft, setDraft] = useState<Case>(EMPTY);
  const set = (key: keyof Case, value: string) => setDraft((d) => ({ ...d, [key]: value }));

  return (
    <div className="flex flex-col gap-5">
      <form
        className="flex flex-col gap-4 rounded-xl border bg-card p-4 sm:p-5"
        onSubmit={(e) => {
          e.preventDefault();
          if (!draft.condition.trim()) return;
          void chat.send(caseToQuestion(draft));
        }}
        data-testid="prescribing-form"
      >
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Prescribing support</h2>
          <p className="text-sm text-muted-foreground">
            Guideline regimens and doses as the sources state them, adjusted for the patient’s
            factors, with interactions and monitoring — every recommendation cited. Don’t enter
            names or identifiers.
          </p>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="rx-condition">Condition or diagnosis *</Label>
          <Input
            id="rx-condition"
            required
            value={draft.condition}
            onChange={(e) => set("condition", e.target.value)}
            placeholder="e.g. community-acquired pneumonia, CURB-65 score 1"
            data-testid="rx-condition"
          />
        </div>
        <div className="grid gap-3 sm:grid-cols-4">
          <Field id="rx-age" label="Age" value={draft.age} onChange={(v) => set("age", v)} placeholder="e.g. 67 years" />
          <Field id="rx-sex" label="Sex" value={draft.sex} onChange={(v) => set("sex", v)} placeholder="female / male" />
          <Field id="rx-weight" label="Weight (kg)" value={draft.weight} onChange={(v) => set("weight", v)} placeholder="e.g. 58" />
          <Field id="rx-renal" label="Renal (eGFR / CrCl)" value={draft.renal} onChange={(v) => set("renal", v)} placeholder="e.g. eGFR 38" />
          <Field id="rx-hepatic" label="Hepatic" value={draft.hepatic} onChange={(v) => set("hepatic", v)} placeholder="e.g. Child-Pugh A" />
          <Field id="rx-preg" label="Pregnancy / lactation" value={draft.pregnancy} onChange={(v) => set("pregnancy", v)} placeholder="e.g. 20 weeks" />
          <Field id="rx-allergy" label="Allergies" value={draft.allergies} onChange={(v) => set("allergies", v)} placeholder="e.g. penicillin (rash)" />
          <Field id="rx-meds" label="Current medicines" value={draft.medicines} onChange={(v) => set("medicines", v)} placeholder="e.g. warfarin, amiodarone" />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="rx-question">Specific question (optional)</Label>
          <Textarea
            id="rx-question"
            rows={2}
            value={draft.question}
            onChange={(e) => set("question", e.target.value)}
            placeholder="e.g. Is doxycycline an option given the penicillin allergy? What duration?"
          />
        </div>
        <div className="flex justify-end gap-2">
          {chat.messages.length > 0 && (
            <Button type="button" variant="ghost" onClick={chat.reset}>
              Clear
            </Button>
          )}
          <Button type="submit" disabled={!draft.condition.trim() || chat.busy} data-testid="rx-submit">
            <Send /> Get recommendations
          </Button>
        </div>
      </form>

      {chat.messages.length > 0 && (
        <div className="flex flex-col gap-6" data-testid="prescribing-answer">
          {chat.messages.map((m) =>
            m.role === "user" ? <UserBubble key={m.id} message={m} /> : <AssistantBubble key={m.id} message={m} />,
          )}
          <Composer
            onSend={(t) => void chat.send(t)}
            onStop={chat.stop}
            busy={chat.busy}
            placeholder="Ask a follow-up about this case…"
          />
        </div>
      )}
    </div>
  );
}

function Field({
  id,
  label,
  value,
  onChange,
  placeholder,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} />
    </div>
  );
}
