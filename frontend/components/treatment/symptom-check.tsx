"use client";

import {
  AlertOctagon,
  ArrowLeft,
  ArrowRight,
  BookMarked,
  CheckCircle2,
  ClipboardList,
  ExternalLink,
  FlaskConical,
  HeartPulse,
  MessagesSquare,
  Pill,
  Printer,
  RotateCcw,
  ShieldAlert,
  Stethoscope,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  CONDITION_LABELS,
  listComplaints,
  splitList,
  summarise,
  treatmentStep,
  URGENCY_STYLE,
  type Assessment,
  type Complaint,
  type Condition,
  type Medicine,
  type Profile,
  type Question,
} from "@/lib/treatment";
import { cn } from "@/lib/utils";

type Stage = "profile" | "complaint" | "questions" | "result";

interface Draft {
  age: string;
  ageUnit: "years" | "months";
  sex: Profile["sex"];
  weight: string;
  pregnant: boolean;
  breastfeeding: boolean;
  conditions: Condition[];
  allergies: string;
  medicines: string;
}

const EMPTY_DRAFT: Draft = {
  age: "",
  ageUnit: "years",
  sex: null,
  weight: "",
  pregnant: false,
  breastfeeding: false,
  conditions: [],
  allergies: "",
  medicines: "",
};

function toProfile(draft: Draft): Profile | null {
  const age = Number(draft.age);
  if (!draft.age || Number.isNaN(age) || age < 0) return null;
  const years = draft.ageUnit === "months" ? age / 12 : age;
  if (years > 120) return null;
  const weight = draft.weight ? Number(draft.weight) : null;
  return {
    age_years: Math.round(years * 1000) / 1000,
    sex: draft.sex,
    weight_kg: weight && !Number.isNaN(weight) && weight > 0 ? weight : null,
    pregnant: draft.sex === "female" && draft.pregnant,
    breastfeeding: draft.sex === "female" && draft.breastfeeding,
    conditions: draft.conditions,
    allergies: splitList(draft.allergies),
    medicines: splitList(draft.medicines),
  };
}

/**
 * The symptom check, start to finish: who it is for, what is wrong, the
 * questions one at a time — danger signs first — and the result: how urgent
 * it is, what to do, and medicines with doses worked out for the person's
 * age, each with the reason it is or isn't suitable and the source it comes
 * from.
 */
export function SymptomCheck({
  token,
  initialComplaint,
  chatHref,
}: {
  /** null runs the public (no account) version. */
  token: string | null;
  initialComplaint?: string | null;
  /** Where "continue in chat" goes; null hides it. */
  chatHref?: string | null;
}) {
  const [stage, setStage] = useState<Stage>("profile");
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [complaints, setComplaints] = useState<Complaint[]>([]);
  const [complaint, setComplaint] = useState<string | null>(initialComplaint ?? null);
  const [answers, setAnswers] = useState<Record<string, string[]>>({});
  const [question, setQuestion] = useState<Question | null>(null);
  const [asked, setAsked] = useState<Record<string, Question>>({});
  const [progress, setProgress] = useState({ answered: 0, total: 1 });
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const profile = useMemo(() => toProfile(draft), [draft]);

  useEffect(() => {
    listComplaints(token)
      .then(setComplaints)
      .catch(() => setError("Couldn't load the symptom check — the server may be waking up. Try again in a minute."));
  }, [token]);

  useEffect(() => {
    if (initialComplaint) setComplaint(initialComplaint);
  }, [initialComplaint]);

  const run = async (nextAnswers: Record<string, string[]>, which = complaint) => {
    if (!profile || !which) return;
    setPending(true);
    setError(null);
    try {
      const out = await treatmentStep(token, which, profile, nextAnswers);
      setAnswers(nextAnswers);
      setProgress({ answered: out.answered, total: Math.max(out.total, 1) });
      if (out.assessment) {
        setAssessment(out.assessment);
        setQuestion(null);
        setStage("result");
        window.scrollTo({ top: 0, behavior: "smooth" });
      } else if (out.question) {
        setQuestion(out.question);
        setAsked((all) => ({ ...all, [out.question!.id]: out.question! }));
        setStage("questions");
      }
    } catch {
      setError("Something went wrong. Check your connection and try again.");
    } finally {
      setPending(false);
    }
  };

  const restart = () => {
    setAnswers({});
    setAssessment(null);
    setQuestion(null);
    setAsked({});
    setStage("complaint");
  };

  const back = () => {
    const ids = Object.keys(answers);
    if (!ids.length) {
      setStage("complaint");
      return;
    }
    const previous = { ...answers };
    delete previous[ids[ids.length - 1]!];
    void run(previous);
  };

  const chosen = complaints.find((c) => c.id === complaint) ?? null;

  return (
    <div className="flex flex-col gap-4" data-testid="symptom-check">
      {error && (
        <p role="alert" className="rounded-md border border-destructive/40 px-3 py-2 text-sm text-destructive">
          {error}
        </p>
      )}

      {stage === "profile" && (
        <ProfileForm
          draft={draft}
          onChange={setDraft}
          valid={Boolean(profile)}
          onNext={() => (complaint ? void run({}) : setStage("complaint"))}
          nextLabel={complaint && chosen ? `Continue: ${chosen.name}` : "Continue"}
          pending={pending}
        />
      )}

      {stage === "complaint" && (
        <section aria-labelledby="complaint-heading" className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h2 id="complaint-heading" className="text-lg font-semibold tracking-tight">
              What’s the main problem?
            </h2>
            <Button variant="ghost" size="sm" onClick={() => setStage("profile")}>
              <ArrowLeft /> Details
            </Button>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {complaints.map((c) => (
              <button
                key={c.id}
                type="button"
                disabled={pending}
                onClick={() => {
                  setComplaint(c.id);
                  void run({}, c.id);
                }}
                className="rounded-lg border bg-card p-3 text-left transition-colors hover:border-primary/50 hover:bg-muted/40"
                data-testid={`complaint-${c.id}`}
              >
                <span className="block font-medium">{c.name}</span>
                <span className="text-sm text-muted-foreground">{c.summary}</span>
              </button>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            Something else? Ask the Chat — or see a doctor if you’re worried.
          </p>
        </section>
      )}

      {stage === "questions" && question && (
        <QuestionCard
          key={question.id}
          question={question}
          complaint={chosen?.name ?? ""}
          progress={progress}
          pending={pending}
          initial={answers[question.id] ?? []}
          onAnswer={(ids) => void run({ ...answers, [question.id]: ids })}
          onBack={back}
        />
      )}

      {stage === "result" && assessment && profile && chosen && (
        <Result
          assessment={assessment}
          complaint={chosen}
          onRestart={restart}
          followUp={
            chatHref
              ? `${chatHref}?audience=patient&q=${encodeURIComponent(
                  `About my symptom check (${summarise(chosen, profile, (id) => asked[id], answers)}): what else should I know?`,
                )}`
              : null
          }
        />
      )}
    </div>
  );
}

// -- who it is for -------------------------------------------------------------------

function ProfileForm({
  draft,
  onChange,
  valid,
  onNext,
  nextLabel,
  pending,
}: {
  draft: Draft;
  onChange: (draft: Draft) => void;
  valid: boolean;
  onNext: () => void;
  nextLabel: string;
  pending: boolean;
}) {
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => onChange({ ...draft, [key]: value });
  const months = draft.ageUnit === "months" ? Number(draft.age) : Number(draft.age) * 12;
  const young = draft.age !== "" && months < 12 * 12;
  return (
    <section aria-labelledby="profile-heading" className="flex flex-col gap-4 rounded-xl border bg-card p-4 sm:p-5">
      <div>
        <h2 id="profile-heading" className="text-lg font-semibold tracking-tight">
          Who is this for?
        </h2>
        <p className="text-sm text-muted-foreground">
          Age decides which medicines are safe and the dose. No name is needed — nothing here is
          stored.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="age">Age</Label>
          <div className="flex gap-1.5">
            <Input
              id="age"
              inputMode="decimal"
              value={draft.age}
              onChange={(e) => set("age", e.target.value.replace(/[^\d.]/g, ""))}
              placeholder="e.g. 34"
              data-testid="profile-age"
              aria-describedby="age-help"
            />
            <select
              aria-label="Age unit"
              value={draft.ageUnit}
              onChange={(e) => set("ageUnit", e.target.value as Draft["ageUnit"])}
              className="h-8 rounded-md border bg-background px-2 text-sm"
              data-testid="profile-age-unit"
            >
              <option value="years">years</option>
              <option value="months">months</option>
            </select>
          </div>
          <span id="age-help" className="text-[11px] text-muted-foreground">
            For babies, choose months.
          </span>
        </div>
        <div className="flex flex-col gap-1.5">
          <span className="text-sm font-medium">Sex</span>
          <div className="flex gap-1.5" role="radiogroup" aria-label="Sex">
            {(["female", "male", "other"] as const).map((s) => (
              <button
                key={s}
                type="button"
                role="radio"
                aria-checked={draft.sex === s}
                onClick={() => set("sex", draft.sex === s ? null : s)}
                className={cn(
                  "h-8 flex-1 rounded-md border px-2 text-sm capitalize transition-colors",
                  draft.sex === s ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted",
                )}
                data-testid={`profile-sex-${s}`}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="weight">Weight (kg){young ? "" : " — optional"}</Label>
          <Input
            id="weight"
            inputMode="decimal"
            value={draft.weight}
            onChange={(e) => set("weight", e.target.value.replace(/[^\d.]/g, ""))}
            placeholder={young ? "Helps with children's doses" : "optional"}
            data-testid="profile-weight"
          />
        </div>
      </div>

      {draft.sex === "female" && Number(draft.age) >= 10 && draft.ageUnit === "years" && (
        <div className="flex flex-wrap gap-4 text-sm">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={draft.pregnant}
              onChange={(e) => set("pregnant", e.target.checked)}
              data-testid="profile-pregnant"
            />
            Pregnant
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={draft.breastfeeding}
              onChange={(e) => set("breastfeeding", e.target.checked)}
            />
            Breastfeeding
          </label>
        </div>
      )}

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-medium">Long-term conditions</legend>
        <div className="flex flex-wrap gap-1.5">
          {(Object.keys(CONDITION_LABELS) as Condition[]).map((c) => {
            const on = draft.conditions.includes(c);
            return (
              <button
                key={c}
                type="button"
                aria-pressed={on}
                onClick={() =>
                  set("conditions", on ? draft.conditions.filter((x) => x !== c) : [...draft.conditions, c])
                }
                className={cn(
                  "rounded-full border px-2.5 py-1 text-xs transition-colors",
                  on ? "border-primary bg-primary/10 font-medium text-primary" : "text-muted-foreground hover:bg-muted",
                )}
              >
                {CONDITION_LABELS[c]}
              </button>
            );
          })}
        </div>
      </fieldset>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="allergies">Medicine allergies</Label>
          <Input
            id="allergies"
            value={draft.allergies}
            onChange={(e) => set("allergies", e.target.value)}
            placeholder="e.g. penicillin, aspirin"
            data-testid="profile-allergies"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="medicines">Medicines taken now</Label>
          <Input
            id="medicines"
            value={draft.medicines}
            onChange={(e) => set("medicines", e.target.value)}
            placeholder="e.g. metformin, warfarin, Dolo 650"
            data-testid="profile-medicines"
          />
        </div>
      </div>

      <div className="flex justify-end">
        <Button onClick={onNext} disabled={!valid || pending} data-testid="profile-next">
          {nextLabel} <ArrowRight />
        </Button>
      </div>
    </section>
  );
}

// -- one question ----------------------------------------------------------------------

function QuestionCard({
  question,
  complaint,
  progress,
  pending,
  initial,
  onAnswer,
  onBack,
}: {
  question: Question;
  complaint: string;
  progress: { answered: number; total: number };
  pending: boolean;
  initial: string[];
  onAnswer: (ids: string[]) => void;
  onBack: () => void;
}) {
  const [picked, setPicked] = useState<string[]>(initial);
  const toggle = (id: string) =>
    setPicked((all) => (all.includes(id) ? all.filter((x) => x !== id) : [...all.filter((x) => x !== "none"), id]));
  const percent = Math.round((progress.answered / progress.total) * 100);

  return (
    <section aria-labelledby="question-heading" className="flex flex-col gap-4 rounded-xl border bg-card p-4 sm:p-5">
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">{complaint}</span>
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted" aria-hidden>
          <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${percent}%` }} />
        </div>
        <span>
          {progress.answered + 1} of {progress.total}
        </span>
      </div>
      <div>
        <h2 id="question-heading" className="text-lg font-semibold tracking-tight" data-testid="question-text">
          {question.text}
        </h2>
        {question.help && <p className="mt-1 text-sm text-muted-foreground">{question.help}</p>}
      </div>

      {question.kind === "single" ? (
        <div className="flex flex-col gap-2" role="radiogroup" aria-labelledby="question-heading">
          {question.options.map((o) => (
            <button
              key={o.id}
              type="button"
              role="radio"
              aria-checked={initial.includes(o.id)}
              disabled={pending}
              onClick={() => onAnswer([o.id])}
              className="rounded-lg border px-3 py-2.5 text-left text-sm transition-colors hover:border-primary/50 hover:bg-muted/40"
              data-testid={`option-${o.id}`}
            >
              {o.label}
            </button>
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {question.options.map((o) => {
            const on = picked.includes(o.id);
            return (
              <label
                key={o.id}
                className={cn(
                  "flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 text-sm transition-colors",
                  on ? "border-primary bg-primary/5" : "hover:bg-muted/40",
                )}
              >
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={on}
                  onChange={() => toggle(o.id)}
                  data-testid={`option-${o.id}`}
                />
                {o.label}
              </label>
            );
          })}
          <label
            className={cn(
              "flex cursor-pointer items-center gap-3 rounded-lg border border-dashed px-3 py-2.5 text-sm",
              picked.includes("none") && "border-primary bg-primary/5",
            )}
          >
            <input
              type="checkbox"
              checked={picked.includes("none")}
              onChange={() => setPicked(picked.includes("none") ? [] : ["none"])}
              data-testid="option-none"
            />
            None of these
          </label>
        </div>
      )}

      <div className="flex items-center justify-between">
        <Button variant="ghost" size="sm" onClick={onBack} disabled={pending}>
          <ArrowLeft /> Back
        </Button>
        {question.kind === "multi" && (
          <Button onClick={() => onAnswer(picked)} disabled={!picked.length || pending} data-testid="question-next">
            Continue <ArrowRight />
          </Button>
        )}
      </div>
    </section>
  );
}

// -- the result ----------------------------------------------------------------------

function SourceRef({ assessment, keyName }: { assessment: Assessment; keyName: string | null | undefined }) {
  const source = assessment.sources.find((s) => s.key === keyName);
  if (!source) return null;
  return (
    <a
      href={source.url}
      target="_blank"
      rel="noopener noreferrer"
      className="ml-1 inline-flex items-center gap-0.5 text-xs text-muted-foreground underline-offset-2 hover:underline"
    >
      {source.publisher}
      <ExternalLink className="size-3" aria-hidden />
    </a>
  );
}

function MedicineCard({ medicine, assessment }: { medicine: Medicine; assessment: Assessment }) {
  return (
    <li
      className={cn(
        "rounded-lg border p-3",
        medicine.suitable ? "bg-card" : "border-dashed bg-muted/30",
      )}
      data-testid={`medicine-${medicine.key}`}
      data-suitable={medicine.suitable}
    >
      <div className="flex items-start gap-2">
        {medicine.suitable ? (
          <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" aria-hidden />
        ) : (
          <XCircle className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
        )}
        <div className="min-w-0 flex-1">
          <p className="font-medium">
            {medicine.name}
            {!medicine.suitable && <span className="ml-2 text-xs font-normal text-muted-foreground">not suggested</span>}
          </p>
          <p className="text-sm text-muted-foreground">{medicine.purpose}</p>
          {medicine.suitable ? (
            <dl className="mt-2 grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
              <dt className="text-muted-foreground">Dose</dt>
              <dd className="font-medium" data-testid="medicine-dose">{medicine.dose}</dd>
              <dt className="text-muted-foreground">How often</dt>
              <dd>{medicine.how_often}</dd>
              <dt className="text-muted-foreground">Maximum</dt>
              <dd>{medicine.maximum}</dd>
            </dl>
          ) : (
            <p className="mt-1.5 text-sm">{medicine.reason_not_suitable}</p>
          )}
          {medicine.notes.length > 0 && (
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-foreground/85">
              {medicine.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
          <p className="mt-2 text-xs text-muted-foreground">
            Source:
            {medicine.sources.map((k) => (
              <SourceRef key={k} assessment={assessment} keyName={k} />
            ))}
          </p>
        </div>
      </div>
    </li>
  );
}

function Section({
  icon: Icon,
  title,
  children,
  testId,
}: {
  icon: typeof Pill;
  title: string;
  children: React.ReactNode;
  testId?: string;
}) {
  return (
    <section className="flex flex-col gap-2" data-testid={testId}>
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <Icon className="size-4 text-muted-foreground" aria-hidden /> {title}
      </h3>
      {children}
    </section>
  );
}

function Result({
  assessment,
  complaint,
  onRestart,
  followUp,
}: {
  assessment: Assessment;
  complaint: Complaint;
  onRestart: () => void;
  followUp: string | null;
}) {
  const style = URGENCY_STYLE[assessment.urgency];
  const emergency = assessment.urgency === "emergency";
  const suitable = assessment.medicines.filter((m) => m.suitable);
  const unsuitable = assessment.medicines.filter((m) => !m.suitable);

  return (
    <article className="flex flex-col gap-5" data-testid="assessment" data-urgency={assessment.urgency}>
      <header className={cn("rounded-xl border p-4 sm:p-5", style.tone)}>
        <p className="text-xs font-semibold uppercase tracking-wide opacity-80">
          {complaint.name} · {style.label}
        </p>
        <h2 className="mt-1 flex items-center gap-2 text-xl font-semibold tracking-tight" data-testid="assessment-headline">
          {emergency ? <AlertOctagon className="size-6" aria-hidden /> : <HeartPulse className="size-5" aria-hidden />}
          {assessment.headline}
        </h2>
        <p className="mt-2 text-sm leading-6">{assessment.action}</p>
        {assessment.reasons.length > 0 && (
          <ul className="mt-3 space-y-1.5 text-sm">
            {assessment.reasons.map((r) => (
              <li key={r.text} className="flex gap-2">
                <ShieldAlert className="mt-0.5 size-4 shrink-0 opacity-80" aria-hidden />
                <span>
                  {r.text}
                  {!emergency && <SourceRef assessment={assessment} keyName={r.source} />}
                </span>
              </li>
            ))}
          </ul>
        )}
      </header>

      {!emergency && (
        <>
          {assessment.medicines.length > 0 && (
            <Section icon={Pill} title="Medicines you can use" testId="assessment-medicines">
              <ul className="flex flex-col gap-2">
                {suitable.map((m) => (
                  <MedicineCard key={m.key} medicine={m} assessment={assessment} />
                ))}
                {unsuitable.map((m) => (
                  <MedicineCard key={m.key} medicine={m} assessment={assessment} />
                ))}
              </ul>
              <p className="text-xs text-muted-foreground">
                Doses are the standard ones printed on packs for this age. Check the strength on
                your pack, and ask a pharmacist if unsure.
              </p>
            </Section>
          )}

          {assessment.self_care.length > 0 && (
            <Section icon={ClipboardList} title="What you can do now">
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {assessment.self_care.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
            </Section>
          )}

          {assessment.possible_causes.length > 0 && (
            <Section icon={Stethoscope} title="What it might be">
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {assessment.possible_causes.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
            </Section>
          )}

          {assessment.doctor_may.length > 0 && (
            <Section icon={BookMarked} title="What a doctor may prescribe" testId="assessment-doctor-may">
              <ul className="flex flex-col gap-2 text-sm">
                {assessment.doctor_may.map((d) => (
                  <li key={d.text} className="rounded-lg border border-dashed p-3">
                    <span className="mb-1 inline-block rounded bg-muted px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                      Prescription only
                    </span>
                    <p>
                      {d.text}
                      <SourceRef assessment={assessment} keyName={d.source} />
                    </p>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {assessment.tests.length > 0 && (
            <Section icon={FlaskConical} title="Tests a doctor may order">
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {assessment.tests.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            </Section>
          )}

          {assessment.see_doctor_if.length > 0 && (
            <Section icon={ShieldAlert} title="See a doctor if">
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {assessment.see_doctor_if.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            </Section>
          )}
        </>
      )}

      <Section icon={BookMarked} title="Sources">
        <ul className="flex flex-col gap-1 text-sm">
          {assessment.sources.map((s) => (
            <li key={s.key}>
              <a href={s.url} target="_blank" rel="noopener noreferrer" className="hover:underline">
                {s.title}
              </a>{" "}
              <span className="text-muted-foreground">— {s.publisher}</span>
            </li>
          ))}
        </ul>
      </Section>

      <p className="rounded-md bg-muted/60 px-3 py-2 text-xs text-muted-foreground">
        This check gives general guidance from NHS, NICE and WHO advice — it is not a diagnosis and
        does not replace a doctor. If you are worried, or things get worse, get medical help.
      </p>

      <div className="flex flex-wrap gap-2 print:hidden">
        {followUp && (
          <Link
            href={followUp}
            className="inline-flex h-8 items-center gap-1.5 rounded-md bg-primary px-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/80"
          >
            <MessagesSquare className="size-4" /> Ask a follow-up question
          </Link>
        )}
        <Button variant="outline" onClick={onRestart} data-testid="check-restart">
          <RotateCcw /> Check something else
        </Button>
        <Button variant="ghost" onClick={() => window.print()}>
          <Printer /> Print
        </Button>
      </div>
    </article>
  );
}
