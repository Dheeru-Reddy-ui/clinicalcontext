# The 90-second demo

`clinicalcontext-demo-raw.webm` is **real footage of the real product**, not a
mock-up: it was recorded by Playwright driving the running app through the
four scenes, with a real microphone (a WAV fixture played into Chromium) for
the voice scene. Re-record it any time:

```bash
pnpm -C frontend exec playwright test demo --project=demo
```

The raw take runs a little long and has no narration or titles — those are the
two things a person still has to add. The cut below is the one to make.

## The cut

**Lead with the contradiction.** It is the thing no general assistant does,
and it is the whole argument for the product in one screen.

| # | Scene | ~Time | What is on screen | What to say |
|---|---|---|---|---|
| 1 | **The sources disagree** | 0:00–0:30 | Typing *"Should beta-blockers be used after myocardial infarction?"*, then the contradiction panel: two positions side by side, each with its own citations | "Ask a question where the literature disagrees, and most tools pick a side. This one shows both, with the sources for each, and says why they differ." |
| 2 | **A citation, opened** | 0:30–0:40 | Clicking `[1]`, the passage panel opening on the exact sentence | "Every claim is traceable. Click any marker and you get the passage it rests on — not a summary of a summary." |
| 3 | **PHI, stopped at the door** | 0:40–0:55 | Typing a question with a patient name and date of birth; the block screen | "Type patient data and it never leaves the browser's request — blocked before retrieval, before any model call. That boundary is enforced in code and tested on 131 adversarial cases." |
| 4 | **It says "I don't know"** | 0:55–1:10 | An uncovered topic; the abstention view | "When the corpus can't answer, it abstains and says what it would need. Silence is a feature; a confident guess is the failure mode we designed against." |
| 5 | **The same thing, spoken** | 1:10–1:25 | The voice page: speaking a question, the transcript appearing, the cited answer | "The same guardrails, the same graph, by voice — first audio in about a second, and you can interrupt it mid-sentence." |
| 6 | **The numbers, in public** | 1:25–1:30 | The methodology page scrolling past the eval tables | "And every number behind it is published, read live from the eval runs — including the ones that aren't flattering." |

## Editing notes

- The raw take is one continuous recording at 1280×720. Cut on the page
  transitions; each scene ends with a two-and-a-half second hold, which is the
  natural cut point.
- No audio is captured. The voice scene needs the spoken question dubbed (the
  fixture is `backend/evals/voice/fixtures/audio/golden-01.wav`) or a caption.
- Keep the mouse still during the holds; the recording already avoids
  scrollbars (`--hide-scrollbars`).
- Do not add a fake latency shot: the timings on screen are the real ones from
  the offline backend, and the methodology page publishes the same numbers.
