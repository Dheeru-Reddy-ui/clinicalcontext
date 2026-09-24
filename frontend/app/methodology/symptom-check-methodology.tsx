/**
 * How the symptom check decides, stated where its citations point.
 *
 * Every warning sign and dose in the Treatment tab cites an NHS, NICE or WHO
 * page. A few thresholds are stricter than any of those pages — chosen for
 * safety where tropical infections are common — and those cite
 * "ClinicalContext safety rule", which links here. This lists each one, so
 * a reader can see exactly what was added beyond the guidance and why.
 * Mirrors backend/app/treatment/protocols.py.
 */

const RULES: { rule: string; why: string }[] = [
  {
    rule: "An adult with a fever for 3 days or more is told to see a doctor in the next day or two.",
    why: "The NHS says to get help when a fever is not improving. In India dengue, malaria and typhoid are common, and only a blood test tells them apart, so the check asks for one earlier.",
  },
  {
    rule: "An adult with a temperature of 40°C or more is told to see a doctor in the next day or two.",
    why: "A very high fever in an adult is more often a serious infection; it deserves an examination even without other warning signs.",
  },
  {
    rule: "A fever in pregnancy is always “see a doctor today”.",
    why: "Infections in pregnancy can affect the baby, and the choice of medicine changes; this should not wait for a warning sign.",
  },
  {
    rule: "Ibuprofen is not suggested for a fever until a doctor has ruled out dengue.",
    why: "The WHO advises against ibuprofen and aspirin in dengue because of bleeding. Where dengue is common, the check assumes it is possible; paracetamol is offered instead.",
  },
  {
    rule: "A long-term heart, lung, kidney or liver disease, or diabetes, with a fever is “see a doctor soon”.",
    why: "People with these conditions are more likely to become seriously ill from an infection.",
  },
  {
    rule: "Age 50 or over with a new headache and jaw pain when eating, or a tender scalp, is “see a doctor today”.",
    why: "These are the classic signs of giant cell arteritis, which can cause sudden loss of sight if untreated.",
  },
  {
    rule: "A hot, red, swollen joint, or a painful swollen calf, is “see a doctor today”.",
    why: "They can be an infected joint or a blood clot (DVT), both of which need treatment the same day.",
  },
  {
    rule: "A rash with blisters, peeling skin, or sores in the mouth or eyes is “see a doctor today”; a rash after a new medicine is “see a doctor soon”.",
    why: "These can be severe drug reactions. The check never tells anyone to stop a prescribed medicine on their own — it sends them to the prescriber.",
  },
];

export function SymptomCheckMethodology() {
  return (
    <section className="space-y-3" aria-labelledby="symptom-check-heading" id="symptom-check">
      <h2 id="symptom-check-heading" className="text-xl font-semibold tracking-tight">
        Symptom check
      </h2>
      <p className="text-sm leading-6 text-muted-foreground">
        The Treatment tab’s symptom check asks the danger signs first — one of them ends the questions
        with emergency advice and no home remedies — then the questions that decide between a doctor and
        home care. Warning signs come from the NHS pages for each complaint, the thresholds for children
        from NICE NG143, dengue, diarrhoea, oral rehydration and zinc from the WHO, and the two-week cough
        rule from India’s TB programme. Doses are the NHS age bands for over-the-counter medicines, looked
        up rather than calculated, and checked against the person’s age, weight, pregnancy, conditions,
        allergies and current medicines before they are shown. Prescription medicines are named only as
        “a doctor may prescribe”, with the guideline that says so, never with a dose for the person.
      </p>
      <h3 className="pt-2 text-base font-semibold">ClinicalContext safety rules</h3>
      <p className="text-sm leading-6 text-muted-foreground">
        Where the check is stricter than the guidance it cites, the result says “ClinicalContext safety
        rule” and links here:
      </p>
      <ul className="space-y-3 text-sm">
        {RULES.map((r) => (
          <li key={r.rule} className="rounded-md border p-3">
            <p className="font-medium">{r.rule}</p>
            <p className="mt-1 text-muted-foreground">{r.why}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
