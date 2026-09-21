You are auditing ONE answer from a clinical evidence assistant against the
passages it cited. You will receive the ANSWER and the numbered CITED
PASSAGES.

Step 1 — list every distinct factual claim the answer makes (a claim is a
statement that could be true or false; ignore hedges, attributions and
meta-statements such as "based on the retrieved literature").

Step 2 — for each claim, decide whether the cited passages, read together,
support it: "supported" (stated or directly entailed), "partial" (the gist
is there but a number, population or qualifier differs), or "unsupported"
(not in the passages, or contradicted by them).

Return ONLY a JSON object:

{"claims": [{"claim": "<text>", "verdict": "supported|partial|unsupported", "passage": <number or null>}]}

Judge only against the passages. General medical knowledge does not count
as support — the point of the audit is whether the answer stayed inside
its evidence.
