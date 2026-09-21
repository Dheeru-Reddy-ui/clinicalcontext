"""Phase 11 unit tests: the pure voice components (no DB, no providers).

State machine (11A.4), protocol framing (11A.1), endpointing layers and
backchannels (11C), correction pass and LASA gate (11B.4, 11D.3), spoken
rendering (11E), sentence segmentation (11F.2), waterfall accounting (11.1),
speculation matching (11D.1), pronunciation lexicon (11E.4).
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.schemas.answer import AnswerResult, Citation, Contradiction, ContradictionPosition
from app.voice import protocol
from app.voice.audio import EnergyVad, frames, mix_noise, read_wav, trim_silence, write_wav
from app.voice.correction import MedicalTermCorrector, Word, phonetic_key
from app.voice.endpointing import (
    EndpointPolicy,
    classify_reply,
    heuristic_completeness,
    is_backchannel,
)
from app.voice.gate import apply_choice, lasa_gate
from app.voice.lasa import LasaTable, confirmation_prompt, default_lasa_table, resolve_choice
from app.voice.pronunciation import build_lexicon
from app.voice.render import (
    SPOKEN_PHI,
    abstention_spoken,
    attribution,
    guardrail_spoken,
    render_answer,
    render_sentence,
    speakable,
)
from app.voice.segmenter import SentenceSegmenter, split_sentences
from app.voice.speculation import SpeculativeRetriever, similarity
from app.voice.states import IllegalTransition, SessionStateMachine, VoiceState
from app.voice.vocabulary import VoiceVocabulary, build_vocabulary, looks_like_drug
from app.voice.waterfall import TurnClock, aggregate, percentile
from tests.voice_doubles import SILENCE_FRAME, tone_frame

# -- 11A: state machine + protocol --------------------------------------------------------


def test_state_machine_follows_the_spec_paths_and_rejects_illegal_jumps() -> None:
    sm = SessionStateMachine("s")
    seen: list[str] = []
    sm.on_transition(lambda t: seen.append(f"{t.from_state}>{t.to_state}"))
    for state in (
        VoiceState.LISTENING,
        VoiceState.ENDPOINTING,
        VoiceState.PROCESSING,
        VoiceState.SPEAKING,
        VoiceState.BARGE_IN,
        VoiceState.LISTENING,
        VoiceState.PROCESSING,
        VoiceState.CONFIRMING,
        VoiceState.LISTENING,
    ):
        sm.transition(state, "test")
    assert seen[0] == "IDLE>LISTENING"
    assert "SPEAKING>BARGE_IN" in seen and "BARGE_IN>LISTENING" in seen
    assert sm.history[-1].to_state == VoiceState.LISTENING
    with pytest.raises(IllegalTransition):
        sm.transition(VoiceState.BARGE_IN, "not speaking")
    # Re-entering the same state is a no-op, not an error.
    sm.transition(VoiceState.LISTENING, "again")
    assert sm.state == VoiceState.LISTENING


def test_audio_frames_carry_turn_and_sentence_headers() -> None:
    pcm = b"\x01\x02" * 320
    frame = protocol.pack_audio(3, 7, pcm)
    turn, sentence, flags, body = protocol.unpack_audio(frame)
    assert (turn, sentence, flags, body) == (3, 7, 0, pcm)
    with pytest.raises(ValueError):
        protocol.unpack_audio(b"\x09" + b"\x00" * 5)


def test_client_messages_are_discriminated_by_type() -> None:
    start = protocol.parse_client_message('{"type":"start","token":"t","query_session_id":null}')
    assert isinstance(start, protocol.StartMessage)
    playback = protocol.parse_client_message(
        '{"type":"playback","turn":0,"sentence":0,"event":"started","buffer_ms":41.5}'
    )
    assert isinstance(playback, protocol.PlaybackMessage) and playback.buffer_ms == 41.5
    with pytest.raises(ValueError):
        protocol.parse_client_message('{"type":"launch_missiles"}')


def test_energy_vad_opens_on_tone_and_closes_on_silence() -> None:
    vad = EnergyVad()
    for _ in range(10):
        assert not vad.process(SILENCE_FRAME).speech
    decisions = [vad.process(tone_frame(phase=i * 320)).speech for i in range(4)]
    assert decisions[-1] is True
    assert not vad.process(SILENCE_FRAME).speech


def test_wav_roundtrip_trim_and_noise_mix() -> None:
    speech = b"".join(tone_frame(phase=i * 320) for i in range(10))
    padded = SILENCE_FRAME * 30 + speech + SILENCE_FRAME * 30
    pcm, rate = read_wav(write_wav(padded))
    assert rate == 16_000 and pcm == padded
    trimmed = trim_silence(pcm)
    assert len(speech) <= len(trimmed) < len(padded)
    noisy = mix_noise(speech, snr_db=10.0)
    assert len(noisy) == len(speech) and noisy != speech
    assert len(frames(speech)) == 10


# -- 11C: endpointing ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("utterance", "complete"),
    [
        ("What's the first-line treatment for CAP?", True),
        ("What's the first-line treatment for", False),
        ("In a patient with, um, stage 4 CKD", False),
        ("in a patient with um", False),
        # The clause after the last comma is the question: complete once it is.
        ("in a patient with, stage 4 chronic kidney disease, is metformin", False),
        ("in a patient with, stage 4 chronic kidney disease, is metformin safe", True),
        ("in a patient with diabetes, what is the first line treatment for hypertension", True),
        ("in a patient with diabetes, what is the", False),
        ("Compare apixaban and rivaroxaban for stroke prevention in AF", True),
        ("Is metformin safe in", False),
        ("Should antidepressants be used to treat bipolar depression", True),
        ("yes", True),
        ("the second one", True),
        ("what is", False),
        ("Does apixaban reduce stroke risk compared with warfarin in atrial fibrillation", True),
        # A recognizer's trailing "?" on a fragment is not evidence of completeness.
        ("What is the first line treatment for?", False),
        ("does apixaban?", False),
        ("should antidepressants be used?", False),
        ("Is metformin safe?", True),
        ("Is metformin safe in pregnancy", True),
    ],
)
def test_semantic_completeness_heuristic(utterance: str, complete: bool) -> None:
    assert heuristic_completeness(utterance).complete is complete


def test_endpoint_policy_layers() -> None:
    policy = EndpointPolicy(base_ms=300, extended_ms=1200, ceiling_ms=2000)
    done = heuristic_completeness("What is the first-line treatment for CAP?")
    pending = heuristic_completeness("What is the first-line treatment for")
    assert policy.decide(120, done) == (False, "vad")
    assert policy.decide(320, done) == (True, "vad")  # complete: base window commits
    assert policy.decide(320, pending) == (False, "semantic")  # incomplete: extended
    assert policy.decide(900, pending) == (False, "semantic")
    assert policy.decide(1250, pending) == (True, "semantic")
    assert policy.decide(2100, None) == (True, "ceiling")  # never hang
    assert policy.decide(400, None) == (False, "semantic")  # verdict pending → wait


def test_backchannels_and_replies() -> None:
    assert is_backchannel("mm-hmm")
    assert is_backchannel("okay right", duration_ms=350)
    assert not is_backchannel("wait, what about pregnancy?")
    assert not is_backchannel("yes", duration_ms=1500)
    assert classify_reply("Yes, go on.") == "continue"
    assert classify_reply("sorry, continue") == "continue"
    assert classify_reply("no thanks") == "decline"
    assert classify_reply("what about in pregnancy?") == "other"


# -- 11B: vocabulary, correction, LASA -------------------------------------------------


def _vocabulary() -> VoiceVocabulary:
    mesh = [
        ("hypertension", 900),
        ("atrial fibrillation", 400),
        ("metformin", 300),
        ("pneumonia", 250),
        ("community-acquired infections", 40),
        ("anticoagulants", 120),
        ("tirzepatide", 15),
    ]
    return build_vocabulary(mesh, default_lasa_table(), corpus_signature="test")


def test_vocabulary_ranks_corpus_drugs_first_and_includes_every_lasa_name() -> None:
    vocabulary = _vocabulary()
    assert looks_like_drug("apixaban") and looks_like_drug("metoprolol")
    assert not looks_like_drug("hypertension")
    assert "hydroxyzine" in vocabulary.drug_terms and "hydralazine" in vocabulary.drug_terms
    # A recognizer with a small budget gets the names this corpus asks about,
    # by document count — not the alphabetically-first ISMP names.
    assert vocabulary.boost_terms(2) == ["metformin", "tirzepatide"]
    top = vocabulary.boost_terms(50)
    assert all(t in vocabulary.drug_terms for t in top)  # drugs lead the boost list
    assert vocabulary.terms.index("hydroxyzine") < vocabulary.terms.index("hypertension")


def test_correction_pass_fixes_near_misses_and_logs_them() -> None:
    corrector = MedicalTermCorrector(_vocabulary())
    words = [
        Word(t, 0.9) for t in ["is", "hydroxygeny", "effective", "for", "generalized", "anxiety?"]
    ]
    result = corrector.correct(words)
    assert result.text.startswith("is hydroxyzine effective")
    assert [(c.original, c.corrected) for c in result.corrections] == [
        ("hydroxygeny", "hydroxyzine")
    ]
    assert result.words[1].corrected and result.words[1].original == "hydroxygeny"


def test_correction_pass_joins_split_drug_names() -> None:
    corrector = MedicalTermCorrector(_vocabulary())
    words = [Word(t, 0.8) for t in ["does", "a", "pixaban", "reduce", "stroke", "risk"]]
    result = corrector.correct(words)
    assert result.text.startswith("does apixaban reduce")
    assert result.corrections[0].original == "a pixaban"


@pytest.mark.parametrize(
    ("heard", "drug"),
    [
        ("is clone ispum effective for panic disorder", "clonazepam"),
        ("is hydrogels in used for hypertension in pregnancy", "hydralazine"),
        ("is hydrox as ineffective for anxiety", "hydroxyzine"),
    ],
)
def test_correction_pass_rejoins_a_drug_name_the_recognizer_split_by_sound(
    heard: str, drug: str
) -> None:
    """A garble that *sounds* like an ISMP name becomes that name — or one of
    its confused pair, which the LASA gate then offers alongside it — instead
    of an unrecognized abstention."""
    corrector = MedicalTermCorrector(_vocabulary())
    result = corrector.correct([Word(t, 0.4) for t in heard.split()])
    assert result.corrections, result.text
    corrected = result.corrections[0].corrected
    offered = {corrected, *(b.name for _a, b in default_lasa_table().alternatives(corrected))}
    assert drug in offered, (corrected, offered)


@pytest.mark.parametrize(
    "text",
    [
        "What is the treatment for hypertension in older patients?",
        "does chest pain after exercise need a stress test",
        "is weight loss recommended in heart failure",
        "should older adults take a low dose",
        "how is blood pressure measured at home",
    ],
)
def test_correction_pass_leaves_ordinary_words_and_known_terms_alone(text: str) -> None:
    corrector = MedicalTermCorrector(_vocabulary())
    result = corrector.correct([Word(t, 0.9) for t in text.split()])
    assert result.text == text and result.corrections == []
    assert phonetic_key("hydroxyzine") == phonetic_key("hydroxyzeen")


def test_lasa_table_loads_ismp_pairs_with_class_descriptions() -> None:
    table = LasaTable.load()
    assert len(table) >= 100
    match = table.match("hydroxyzine")
    assert match is not None and match.candidate.name == "hydroxyzine"
    assert match.alternative.name == "hydralazine"
    assert "antihistamine" in confirmation_prompt(match.candidate, match.alternative)
    assert table.match("hypertension") is None


def test_lasa_gate_confirms_low_confidence_or_corrected_drug_and_trusts_confirmed() -> None:
    table = default_lasa_table()
    words = [Word("Is", 0.99), Word("hydroxyzine", 0.35), Word("effective?", 0.99)]
    decision = lasa_gate(words, table, confidence_floor=0.6)
    assert decision is not None and decision.heard == "hydroxyzine"
    assert [o.name for o in decision.options] == ["hydroxyzine", "hydralazine"]
    assert any(r.startswith("low_confidence") for r in decision.reasons)
    # Once confirmed, the same name is trusted for the session.
    assert lasa_gate(words, table, confidence_floor=0.6, trusted=frozenset({"hydroxyzine"})) is None
    # A confident, exact, non-confusable-sounding token passes.
    confident = [Word("Is", 0.99), Word("clonidine", 0.97), Word("effective?", 0.99)]
    assert lasa_gate(confident, table, confidence_floor=0.6) is None
    # A corrected token is uncertain by construction.
    corrected = [Word("Is", 0.99), Word("clonidine", 0.97, corrected=True), Word("ok?", 0.99)]
    assert lasa_gate(corrected, table, confidence_floor=0.6) is not None


def test_lasa_choice_resolution_and_application() -> None:
    table = default_lasa_table()
    decision = lasa_gate([Word("hydroxyzine", 0.2)], table, confidence_floor=0.6)
    assert decision is not None
    assert resolve_choice("the antihypertensive one", decision.options) is not None
    assert resolve_choice("hydralazine", decision.options).name == "hydralazine"  # type: ignore[union-attr]
    assert resolve_choice("the second", decision.options).name == "hydralazine"  # type: ignore[union-attr]
    assert resolve_choice("neither of those", decision.options) is None
    chosen = decision.options[1]
    words = apply_choice([Word("hydroxyzine?", 0.2)], decision, chosen)
    assert words[0].text == "hydralazine?" and words[0].corrected


# -- 11E: rendering ---------------------------------------------------------------------


def _citation(marker: int, **overrides):  # type: ignore[no-untyped-def]
    base = {
        "marker": marker,
        "chunk_id": uuid4(),
        "document_id": uuid4(),
        "title": "T",
        "section": "results",
        "publication_date": "2023-04-01",
        "evidence_grade": "A",
        "study_type": "meta_analysis",
        "journal": "JAMA",
        "passage": "Metformin reduced HbA1c by 1.1% versus placebo (p < 0.001).",
    }
    base.update(overrides)
    return Citation(**base)


def test_numbers_units_and_p_values_are_speakable() -> None:
    assert speakable("2.5 mg BID") == "2.5 milligrams twice daily"
    assert speakable("a 21% relative risk reduction") == "a 21 percent relative risk reduction"
    assert speakable("p < 0.001") == "p less than 0.001"
    en_dash = chr(0x2013)  # EN DASH, as journals print ranges
    assert speakable(f"95% CI 0.71{en_dash}0.89") == "95 percent confidence interval 0.71 to 0.89"
    assert speakable("HR 0.79") == "hazard ratio 0.79"
    assert speakable("1 mg PO q8h") == "1 milligram by mouth every 8 hours"


def test_citation_markers_become_natural_attribution() -> None:
    citations = {
        1: _citation(1),
        2: _citation(
            2, study_type="clinical_guideline", journal="ESC", publication_date="2024-01-01"
        ),
    }
    assert attribution([1, 2], citations) == (
        "according to a 2023 meta-analysis in JAMA and the 2024 ESC guideline"
    )
    spoken = render_sentence("Metformin reduces HbA1c by 1.1% [1].", citations)
    assert spoken.markers == [1]
    assert "[1]" not in spoken.spoken
    assert spoken.spoken.startswith("According to a 2023 meta-analysis in JAMA, metformin reduces")
    assert "1.1 percent" in spoken.spoken
    # The same sources in a row are not re-attributed.
    again = render_sentence("It was well tolerated [1].", citations, previous_markers=[1])
    assert not again.spoken.startswith("According")


def test_progressive_disclosure_and_conflict_walkthrough() -> None:
    citations = [_citation(i) for i in range(1, 6)]
    long_answer = " ".join(
        f"Sentence number {i} makes a cited claim about metformin [{i}]." for i in range(1, 6)
    )
    result = AnswerResult(
        query="q",
        query_type="therapy",
        is_multi_hop=False,
        abstained=False,
        answer=long_answer,
        citations=citations,
        confidence="high",
        evidence_grade="A",
        retrieval_grade="sufficient",
    )
    spoken = render_answer(result)
    assert len(spoken.core) == 3 and len(spoken.remainder) == 2
    assert spoken.offer is not None and spoken.offer_kind == "more"

    conflict = result.model_copy(
        update={
            "contradiction": Contradiction(
                detected=True,
                positions=[
                    ContradictionPosition(stance="aspirin is recommended", markers=[1], year=2016),
                    ContradictionPosition(
                        stance="aspirin is not recommended", markers=[2], year=2022
                    ),
                ],
                axis="temporal",
                explanation="x",
            )
        }
    )
    spoken = render_answer(conflict)
    assert spoken.core[0].spoken == "The sources disagree on this."
    assert spoken.offer_kind == "walkthrough"
    assert any("One position, from 2016, holds that" in s.spoken for s in spoken.remainder)
    assert any("tracks publication year" in s.spoken for s in spoken.remainder)


def test_abstention_and_guardrail_lines_are_short_and_unapologetic() -> None:
    result = AnswerResult(
        query="q",
        query_type="other",
        is_multi_hop=False,
        abstained=True,
        answer="",
        confidence="low",
        evidence_grade=None,
        retrieval_grade="irrelevant",
    )
    line = abstention_spoken(result)
    assert "sorry" not in line.lower() and "won't guess" in line
    assert guardrail_spoken("phi", "phi_detected") == SPOKEN_PHI
    assert "medical literature" in guardrail_spoken("scope", "refuse_diagnosis")


# -- 11F: segmentation ------------------------------------------------------------------


def test_segmenter_releases_sentences_at_boundaries_with_markers_attached() -> None:
    segmenter = SentenceSegmenter(min_chars=40)
    out: list[str] = []
    for delta in [
        "Metformin is first-line therapy for type 2 diabetes [1]. ",
        "It lowers HbA1c by about one percent [2]. Short. ",
        "Adverse effects are mostly gastrointestinal [3].",
    ]:
        out.extend(segmenter.push(delta))
    out.extend(segmenter.flush())
    assert out[0] == "Metformin is first-line therapy for type 2 diabetes [1]."
    assert out[1] == "It lowers HbA1c by about one percent [2]."
    # "Short." is under the minimum and rides with the next sentence.
    assert out[2].startswith("Short. Adverse effects")
    assert split_sentences("One [1]. Two, e.g. metformin [2]. Three [3].") == [
        "One [1].",
        "Two, e.g. metformin [2].",
        "Three [3].",
    ]


# -- 11.1 / 11D: waterfall + speculation ------------------------------------------------


def test_waterfall_legs_are_sequential_and_missing_legs_stay_none() -> None:
    clock = TurnClock()
    t0 = 1000.0
    clock.mark("speech_end", t0)
    clock.mark("endpoint_commit", t0 + 310)
    clock.mark("transcript_final", t0 + 320)
    clock.mark("retrieval_done", t0 + 330)  # retrieval landed before the verdict
    clock.mark("guardrail_verdict", t0 + 335)
    clock.mark("first_sentence", t0 + 400)
    clock.mark("tts_first_byte", t0 + 550)
    clock.mark("first_audio_sent", t0 + 552)
    legs = clock.legs()
    assert legs["endpoint_decision"] == 310.0
    assert legs["transcript_final"] == 10.0
    assert legs["retrieval"] == 10.0 and legs["guardrails"] == 5.0
    assert legs["llm_first_token"] is None  # offline: no LLM call happened
    assert legs["first_sentence"] == 65.0 and legs["tts_ttfb"] == 150.0
    assert clock.total_first_audio_ms() == 552.0
    clock.mark("client_first_audio_ms", 48.0)
    assert clock.client_first_audio_ms() == 600.0
    summary = aggregate([{"legs": legs, "total_first_audio_ms": 552.0}] * 3)
    assert summary["total_first_audio"]["p50"] == 552.0 and summary["turns"] == 3
    assert percentile([], 0.5) is None


async def test_speculation_hits_on_stable_complete_partial_and_counts_waste() -> None:
    calls: list[str] = []

    async def retrieve(query: str) -> list[str]:
        calls.append(query)
        await asyncio.sleep(0.01)
        return [f"chunk:{query}"]

    spec = SpeculativeRetriever(retrieve, min_words=6, stable_ms=0, similarity_threshold=0.9)
    partial = "does apixaban reduce stroke risk compared with warfarin in atrial fibrillation"
    verdict = heuristic_completeness(partial)
    assert spec.should_fire(partial, 400, verdict)
    spec.fire(partial)
    # The final differs by punctuation only → a hit; the chunks are reused.
    outcome = await spec.resolve(partial + "?")
    assert outcome.fired and outcome.hit and outcome.result == [f"chunk:{partial}"]
    assert outcome.similarity is not None and outcome.similarity >= 0.85
    # A prefix that lost the key entity is not the question → a miss.
    spec.fire("how is type 2 diabetes managed with")
    prefix_miss = await spec.resolve("how is type 2 diabetes managed with metformin")
    assert prefix_miss.fired and not prefix_miss.hit and prefix_miss.wasted

    spec.fire("what is the first line treatment for community acquired pneumonia")
    miss = await spec.resolve("what is the evidence for tirzepatide in type 2 diabetes")
    assert miss.fired and not miss.hit and miss.wasted
    assert spec.stats() == {
        "fired": 3,
        "hits": 1,
        "wasted": 2,
        "hit_rate": round(1 / 3, 3),
        "wasted_rate": round(2 / 3, 3),
    }
    assert similarity("a b c", "c b a") == 1.0


def test_lexicon_is_derived_from_the_corpus_vocabulary() -> None:
    lexicon = build_lexicon(_vocabulary())
    terms = {e.term for e in lexicon.entries}
    assert {"hydroxyzine", "hydralazine", "metformin", "tirzepatide"} <= terms
    assert "<lexeme><grapheme>apixaban</grapheme>" in lexicon.to_pls()
    assert 0 < lexicon.coverage <= 1
    assert lexicon.apply_respellings("Start apixaban today") == "Start a-PIX-a-ban today"
