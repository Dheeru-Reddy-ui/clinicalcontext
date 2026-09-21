The previous retrieval was insufficient. Rewrite the question to retrieve
better. You are given the ATTEMPT number (0-based).

- ATTEMPT 0 — broaden: remove narrow qualifiers, add common synonyms and
  clinical terminology (drug class names, MeSH-style terms), keep the core
  intent.
- ATTEMPT 1 — HyDE: write a single hypothetical *answer sentence* that an ideal
  source would contain, so it can be embedded and matched against the corpus.

Output ONLY the rewritten query / hypothetical sentence — one line, no
explanation.
