Decompose a multi-hop clinical question into the minimal set of standalone
sub-questions whose answers, combined, answer the original.

Example — "Is apixaban preferred over warfarin in AF with CKD stage 4?" →
- Apixaban in atrial fibrillation with stage 4 CKD
- Warfarin in atrial fibrillation with stage 4 CKD
- Head-to-head evidence: apixaban versus warfarin in AF with stage 4 CKD

Keep each sub-question self-contained (carry the shared population/context into
each). Output one sub-question per line, no numbering, no prose.
