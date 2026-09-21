"""The voice eval harness (11H): CI for a conversation.

* ``manifest.py`` — the fixture definitions (golden questions, hesitation
  patterns, LASA drug names, the spoken adversarial set, backchannels,
  interruptions, noise variants).
* ``build_fixtures.py`` — renders them to 16 kHz WAV with the installed
  OneCore voices (en-GB and en-IN) and writes ``fixtures/manifest.jsonl``.
* ``run.py`` — plays every fixture through the real WebSocket pipeline and
  writes ``evals/results/voice.json`` (medical-term WER, endpoint latency,
  first-audio latency, barge-in stop time, speculation hit rate, guardrail
  verdicts), then enforces the CI gate.
* ``wer.py`` — word error rate + medical-term error rate.
* ``judge.py`` — the turn-quality rubric (LLM-as-Judge; records "not run"
  when no key is configured rather than inventing a score).
"""
