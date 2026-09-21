"""The golden set and the harness that runs it (Phase 12).

``build`` derives the set from the corpus, ``metrics`` scores retrieval and
calibration, ``run`` executes the suite, ``judge`` adds the LLM-scored
generation metrics when a key is present, ``calibrate`` turns stated
confidence into a reliability curve, ``promote`` grows the set from
reviewed feedback.
"""
