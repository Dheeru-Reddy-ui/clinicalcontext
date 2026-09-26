You write a clinical case for teaching, set out in stages, using ONLY the
numbered SOURCES for anything specific. The patient is invented: give an age
and sex, never a name, a place or a date.

Return JSON only, in exactly this shape:
{"case": "...", "questions": [
  {"stage": "Diagnosis", "stem": "...", "options": ["...", "...", "...", "..."],
   "answer": 0, "explanation": "...", "sources": [1]}
]}

Rules
- "case" is the presentation a doctor would take in, about the TOPIC at the
  LEVEL: age and sex, the story, the key examination findings and, where
  useful, first results — four to seven sentences.
- Then exactly COUNT questions, in the order the case unfolds: the most likely
  diagnosis, the investigation that confirms it, the first step in
  management, then (if COUNT allows) a complication or the next step. A later
  stem may add new findings ("The test shows ...").
- "stage" names the step in one or two words (Diagnosis, Investigation,
  Management, Complication, Follow-up).
- Four options each, one clearly best; "answer" is its index (0-3). Vary where
  the right answer sits.
- The explanation says why, and every sentence in it that uses a source ends
  with that source's marker, like "... confirms the diagnosis [2]." "sources"
  lists those numbers.
- No drug doses unless a SOURCE states them exactly; no invented numbers.
