Given a clinical QUESTION and NUMBERED PASSAGES, determine whether the passages
*disagree* on the answer. Disagreement is a first-class finding, not an edge
case — surface it, never smooth it over.

Cluster conflicting passages into positions and identify why they differ:
- `temporal` — different publication years reach different conclusions.
- `population` — different patient populations.
- `endpoint` — different outcomes/endpoints measured.
- `unclear` — they conflict but the reason is not evident.

Respond with ONLY a JSON object:
{"detected": true|false,
 "axis": "temporal|population|endpoint|unclear|none",
 "positions": [{"stance": "<short stance>", "markers": [1,2], "year": 2019}],
 "explanation": "<one or two sentences on the disagreement and why>"}

If the passages substantially agree, return {"detected": false, "axis": "none",
"positions": [], "explanation": ""}.
