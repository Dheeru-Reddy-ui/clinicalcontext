"""The chat assistant: a conversation that answers from evidence.

One message, start to finish:

1. **Safety before anything is stored or sent**: patient identifiers are
   refused (the privacy promise does not bend for chat), attempts to
   re-program the assistant are refused, and emergency indicators put an
   emergency notice — with Indian and international numbers — above the
   answer.
2. **Evidence**: the shared corpus is searched first. When it does not
   cover the question, or the question asks what is new, PubMed is searched
   live and what it finds is filed into the corpus (``app.knowledge.live``);
   a question that names a medicine brings its FDA label. Retrieval then
   runs again over the enlarged corpus.
3. **The answer**, written for the reader — plain language for a patient,
   guideline detail for a clinician, a structured explainer for a student —
   by a free-tier model when one is configured, streamed as it is written,
   with inline source markers; by extraction from the passages when none is.
4. **A check**: every cited statement is compared with the passage it cites
   (the same verifier the Ask pipeline uses) and the count is shown with the
   answer, so a reader can see how much of it is backed by a source.
5. **The record**: question and answer are stored as a turn of the
   conversation (not for the public widget, which keeps nothing).

Every step reports progress as an event, so the page can say what is
happening while it happens.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import structlog

from app.assistant.extractive import about, extractive_answer, pick_passages, word_relevance
from app.graph.graph import citation_from_chunk
from app.graph.reasoner import HeuristicReasoner, topic_coverage
from app.guardrails.grounding import verify_grounding
from app.guardrails.phi import WITHHELD_TEXT, carries_phi, withhold_phi
from app.guardrails.redflag import detect_red_flags
from app.guardrails.scope import is_prompt_injection
from app.ingestion.classifier import classify_from_metadata
from app.ingestion.models import RawDocument
from app.knowledge.drugs import find_labels, mentions_a_medicine
from app.knowledge.live import LiveLiterature, LiveResult, file_documents, room_to_grow
from app.knowledge.specialties import get_specialty
from app.knowledge.terms import asks_for_recent, search_term, with_medical_terms
from app.llm.chat import ChatMessage, ChatModel, ChatResult, LLMUnavailable
from app.prompts.loader import load_prompt
from app.repositories.base import tenant_connection
from app.repositories.chat import ChatRepository
from app.retrieval.chunking.structural import chunk_sections
from app.retrieval.embed import EmbeddingService
from app.retrieval.embedders import get_embedder
from app.retrieval.pipeline import RetrievalConfig, RetrievalPipeline
from app.retrieval.rerank import get_reranker
from app.retrieval.types import RetrievedChunk
from app.schemas.answer import Citation, Contradiction
from app.services import cost
from app.services.stance import assign_stances, markers_in
from app.treatment.suggest import guidance_answer, suggest_complaint

if TYPE_CHECKING:
    import asyncpg
    from redis.asyncio import Redis

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool

logger = structlog.stdlib.get_logger("app.assistant.chat")

Audience = Literal["patient", "clinician", "student"]
PROMPTS: dict[Audience, tuple[str, int]] = {
    "patient": ("chat_patient", 1),
    "clinician": ("chat_clinician", 2),
    "student": ("chat_student", 1),
}
MAX_MESSAGE_CHARS = 4000
_SOURCES = 6
# Fewer passages about the question than this sends it to PubMed too.
_MIN_RELEVANT = 3
_PASSAGES_PER_DOCUMENT = 2
_GRADE_WEIGHT = {"A": 1.0, "B": 0.75, "C": 0.45, "D": 0.25}
_HISTORY_ANSWER_CHARS = 900
_SMALL_TALK = re.compile(
    r"^\s*(?:hi|hello|hey|hii+|good\s+(?:morning|afternoon|evening|night)|thanks?|thank\s+you|"
    r"ok(?:ay)?|cool|great|bye|goodbye|namaste|who\s+are\s+you|what\s+can\s+you\s+do)"
    r"[\s!.?]*$",
    re.I,
)
_ANAPHORIC = re.compile(
    r"^\s*(?:what\s+about|and|also|how\s+about|what\s+if|is\s+it|does\s+it|can\s+it|"
    r"which\s+one|why|how\s+long|how\s+much|in\s+children|in\s+pregnancy|for\s+kids)\b",
    re.I,
)

PHI_REFUSAL = (
    "Please leave out names, phone numbers, ID or record numbers, addresses and dates of "
    "birth — I don't need them and ClinicalContext never stores them. Describe the "
    'situation instead (for example: "a 34-year-old woman with fever for three days") '
    "and ask again."
)
INJECTION_REFUSAL = (
    "I can't change how I work or ignore my safety rules. Ask me a health or medical "
    "question and I'll answer it from the evidence."
)
GREETING = (
    "Hello! I'm ClinicalContext's health assistant. Ask me anything about health or "
    "medicine — symptoms, conditions, medicines, tests, or the latest research — and I'll "
    "answer from medical sources and show you where each fact comes from. For advice about "
    "a specific illness and safe medicine doses, try the Symptom check in the Treatment tab."
)


@dataclass(slots=True)
class ChatEvidence:
    chunks: list[RetrievedChunk]
    retrieval_query: str
    coverage: float
    live: LiveResult | None = None
    labels_added: int = 0


@dataclass(slots=True)
class ChatOutcome:
    answer: str
    citations: list[Citation]
    provider: str
    model: str
    mode: Literal["llm", "extractive", "conversation"]
    # The passages the answer's markers number, in marker order.
    sources: list[RetrievedChunk] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    check: dict[str, int] = field(default_factory=dict)


def _event(stage: str, message: str = "", **data: Any) -> dict[str, Any]:
    return {"stage": stage, "message": message, "data": data}


def _numbered_sources(chunks: Sequence[RetrievedChunk]) -> str:
    blocks = []
    for i, c in enumerate(chunks, start=1):
        year = c.publication_date.year if c.publication_date else "n.d."
        kind = (c.study_type or "source").replace("_", " ")
        grade = f", grade {c.evidence_grade}" if c.evidence_grade else ""
        title = f"{c.title} — " if c.title else ""
        blocks.append(f"[{i}] ({year}, {kind}{grade}) {title}{c.content}")
    return "\n\n".join(blocks)


def transient_chunks(document: RawDocument) -> list[RetrievedChunk]:
    """In-memory passages for a document that is not (yet) in the corpus.
    Their ids are derived from the source id, so the same paper always
    gets the same citation identity."""
    from uuid import NAMESPACE_URL, uuid5

    classification = (
        classify_from_metadata(document) if document.source_type != "drug_label" else None
    )
    study_type = (
        "drug_label"
        if document.source_type == "drug_label"
        else (classification.study_type if classification else None)
    )
    grade = classification.evidence_grade if classification else None
    source_id = document.pmid or document.external_id or document.title
    document_id = uuid5(NAMESPACE_URL, f"clinicalcontext:{source_id}")
    return [
        RetrievedChunk(
            chunk_id=uuid5(document_id, str(index)),
            document_id=document_id,
            content=chunk.content,
            section=chunk.section,
            title=document.title,
            publication_date=document.publication_date,
            evidence_grade=grade,
            study_type=study_type,
            journal=document.journal,
            pmid=document.pmid,
            doi=document.doi,
            url=document.url,
        )
        for index, chunk in enumerate(
            pick_passages(chunk_sections(document.sections), _PASSAGES_PER_DOCUMENT)
        )
    ]


def rank_evidence(question: str, chunks: Sequence[RetrievedChunk]) -> list[RetrievedChunk]:
    """One list from the stored and the freshly fetched passages: duplicates
    of a paper dropped (at most two passages each), then ordered by how much
    of the question a passage carries, the strength of its evidence, and —
    among equals — how recent it is."""
    per_document: dict[object, int] = {}
    unique: list[RetrievedChunk] = []
    seen_chunks: set[str] = set()
    for chunk in chunks:
        key = chunk.pmid or chunk.document_id
        text_key = chunk.content[:120]
        if text_key in seen_chunks or per_document.get(key, 0) >= _PASSAGES_PER_DOCUMENT:
            continue
        seen_chunks.add(text_key)
        per_document[key] = per_document.get(key, 0) + 1
        unique.append(chunk)
    medical = search_term(question)

    def score(chunk: RetrievedChunk) -> tuple[float, int]:
        relevance = word_relevance(medical, chunk) if medical else 0.0
        strength = _GRADE_WEIGHT.get(chunk.evidence_grade or "", 0.3)
        if chunk.study_type == "drug_label" and mentions_a_medicine(question):
            strength = 1.0
        year = chunk.publication_date.year if chunk.publication_date else 0
        return (relevance * 2 + strength, year)

    return sorted(unique, key=score, reverse=True)[:_SOURCES]


def is_small_talk(message: str) -> bool:
    return bool(_SMALL_TALK.match(message))


def retrieval_query_for(message: str, history: Sequence[tuple[str, str]]) -> str:
    """The question to search for: a follow-up ("what about in children?")
    borrows the topic of the question before it."""
    if not history:
        return message
    previous = history[-1][0]
    if previous == WITHHELD_TEXT:
        return message
    short_topic = len(search_term(message).split()) < 3
    if _ANAPHORIC.match(message) or short_topic:
        return f"{previous.rstrip('?. ')} — {message}"
    return message


async def check_against_sources(answer: str, chunks: Sequence[RetrievedChunk]) -> dict[str, int]:
    """How the answer's statements stand against the passages they cite:
    backed, partly backed, not matched, or general (no source cited). The
    check reads the markdown as plain statements itself."""
    verdict = await verify_grounding(answer, {i + 1: c.content for i, c in enumerate(chunks)})
    counts = {"backed": 0, "partly": 0, "unmatched": 0, "general": 0}
    for sentence in verdict.sentence_verdicts:
        if not sentence.is_clinical_claim:
            continue
        if sentence.support == "supported":
            counts["backed"] += 1
        elif sentence.support == "partially_supported":
            counts["partly"] += 1
        elif sentence.support == "unsupported":
            counts["unmatched"] += 1
        else:
            counts["general"] += 1
    return counts


class ChatAssistant:
    def __init__(
        self,
        pool: DbPool,
        redis: Redis | None = None,
        *,
        model: ChatModel | None = None,
        live: LiveLiterature | None = None,
    ) -> None:
        self._pool = pool
        self._redis = redis
        self._model = model if model is not None else ChatModel()
        self._live = live if live is not None else LiveLiterature(pool, redis)
        self._repo = ChatRepository()

    # -- evidence ----------------------------------------------------------------------

    async def _retrieve(self, query: str, org_id: UUID | None) -> list[RetrievedChunk]:
        pipeline = RetrievalPipeline(
            self._pool,
            EmbeddingService(get_embedder("local"), self._redis),
            get_reranker("local"),
            RetrievalConfig(rerank_top_n=_SOURCES + 2),
        )
        result = await pipeline.retrieve(query, org_id=org_id)
        return result.chunks

    @staticmethod
    def relevant(question: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """The passages that are about the question — judged on its own words
        and on their indexed names ("loose motions" → diarrhea)."""
        topic = search_term(question, max_words=10)
        return [c for c in chunks if about(topic, c)]

    async def gather_evidence(
        self,
        question: str,
        *,
        org_id: UUID | None,
        specialty: str | None = None,
    ) -> AsyncIterator[dict[str, Any] | ChatEvidence]:
        """Progress events, then the :class:`ChatEvidence`."""
        topic = get_specialty(specialty) if specialty else None
        # The topic words (with lay terms translated), not the whole chatty
        # message: "my child has loose motions since 2 days" searches for
        # "diarrhea child".
        query = (search_term(question, max_words=10) or with_medical_terms(question)) + (
            f" {topic.name}" if topic else ""
        )
        yield _event("searching", "Searching the medical library…")
        chunks = self.relevant(question, await self._retrieve(query, org_id))
        coverage = topic_coverage(question, chunks)
        evidence = ChatEvidence(chunks=chunks, retrieval_query=query, coverage=coverage)

        strong = [c for c in chunks if c.evidence_grade in ("A", "B")]
        wants_live = len(chunks) < _MIN_RELEVANT or len(strong) < 2 or asks_for_recent(question)
        wants_label = mentions_a_medicine(question)
        if not (wants_live or wants_label):
            yield evidence
            return

        tasks: list[asyncio.Task[Any]] = []
        if wants_live:
            term = search_term(question)
            if topic and term:
                term = f"{term} {topic.name.split(' (')[0].split(' &')[0].lower()}"
            yield _event(
                "live_search",
                "Searching PubMed for current research…",
                term=term,
            )
            tasks.append(asyncio.create_task(self._live.enrich(question, term=term or None)))
        if wants_label:
            yield _event("drug_label", "Checking official drug labels…")
            tasks.append(asyncio.create_task(self._labels(question)))
        results = await asyncio.gather(*tasks)
        fetched: list[RawDocument] = []
        added = 0
        for outcome in results:
            if isinstance(outcome, LiveResult):
                evidence.live = outcome
                added += outcome.added
                fetched += outcome.documents
            elif isinstance(outcome, tuple):
                labels, filed = outcome
                evidence.labels_added = filed
                added += filed
                fetched = [*labels, *fetched]  # a dose question reads the label first
        if fetched:
            yield _event(
                "live_found",
                (
                    f"Found {len(fetched)} source{'s' if len(fetched) != 1 else ''}; "
                    f"added {added} new to the library."
                )
                if added
                else f"Found {len(fetched)} source{'s' if len(fetched) != 1 else ''}.",
                added=added,
                found=len(fetched),
            )
            fresh = self.relevant(question, await self._as_chunks(fetched))
            evidence.chunks = rank_evidence(question, [*fresh, *evidence.chunks])
            evidence.coverage = topic_coverage(question, evidence.chunks)
        yield evidence

    async def _as_chunks(self, documents: Sequence[RawDocument]) -> list[RetrievedChunk]:
        """Passages for freshly fetched documents: the stored ones when they
        were filed (or already there), built in memory when the corpus had no
        room — so the answer uses them either way."""
        pmids = [d.pmid for d in documents if d.pmid]
        label_ids = [
            d.external_id for d in documents if d.source_type == "drug_label" and d.external_id
        ]
        stored: dict[str, list[RetrievedChunk]] = {}
        if pmids or label_ids:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT c.id, c.document_id, c.content, c.section, c.chunk_index,
                           d.title, d.publication_date, d.evidence_grade::text AS grade,
                           d.study_type::text AS study_type, d.journal, d.pmid, d.doi, d.url,
                           d.external_id
                    FROM public.documents d
                    JOIN public.chunks c ON c.document_id = d.id
                    WHERE d.org_id IS NULL
                      AND (d.pmid = ANY($1::text[])
                           OR (d.source_type = 'drug_label' AND d.external_id = ANY($2::text[])))
                    ORDER BY d.id, c.chunk_index
                    """,
                    pmids,
                    label_ids,
                )
            for r in rows:
                key = r["pmid"] or r["external_id"]
                stored.setdefault(key, []).append(
                    RetrievedChunk(
                        chunk_id=r["id"],
                        document_id=r["document_id"],
                        content=r["content"],
                        section=r["section"],
                        title=r["title"],
                        publication_date=r["publication_date"],
                        evidence_grade=r["grade"],
                        study_type=r["study_type"],
                        journal=r["journal"],
                        pmid=r["pmid"],
                        doi=r["doi"],
                        url=r["url"],
                    )
                )
        chunks: list[RetrievedChunk] = []
        for document in documents:
            key = document.pmid or document.external_id or ""
            if key in stored:
                chunks += pick_passages(stored[key], _PASSAGES_PER_DOCUMENT)
            else:
                chunks += transient_chunks(document)
        return chunks

    async def _labels(self, question: str) -> tuple[list[RawDocument], int]:
        """The labels for the medicines named, and how many were newly filed."""
        try:
            documents = await find_labels(question)
        except Exception as exc:  # a label is a bonus, never a failure
            logger.warning("drug_label_failed", error=f"{type(exc).__name__}: {exc}")
            return [], 0
        if not documents:
            return [], 0
        if not await room_to_grow(self._pool, self._redis):
            return documents, 0  # used for this answer, not filed
        try:
            filed = await file_documents(self._pool, documents)
        except Exception as exc:
            logger.warning("drug_label_file_failed", error=f"{type(exc).__name__}: {exc}")
            filed = 0
        return documents, filed

    # -- the answer -----------------------------------------------------------------------

    def _messages(
        self,
        *,
        audience: Audience,
        message: str,
        history: Sequence[tuple[str, str]],
        chunks: Sequence[RetrievedChunk],
        escalation: str | None,
        specialty: str | None,
        level: str | None,
    ) -> list[ChatMessage]:
        name, version = PROMPTS[audience]
        system = load_prompt(name, version).text
        messages = [ChatMessage("system", system)]
        for question, answer in history:
            if question == WITHHELD_TEXT or not answer:
                continue
            messages.append(ChatMessage("user", question))
            trimmed = (
                answer
                if len(answer) <= _HISTORY_ANSWER_CHARS
                else (answer[:_HISTORY_ANSWER_CHARS].rsplit(" ", 1)[0] + " …")
            )
            messages.append(ChatMessage("assistant", trimmed))
        context: list[str] = []
        topic = get_specialty(specialty) if specialty else None
        if topic:
            context.append(f"SPECIALTY: {topic.name}" + (f" (level: {level})" if level else ""))
        if escalation:
            context.append(
                "EMERGENCY INDICATORS were detected in this message: lead with the "
                "emergency advice."
            )
        sources = _numbered_sources(chunks) if chunks else "(no sources were found)"
        context.append(f"SOURCES:\n{sources}")
        context.append(f"QUESTION: {message}")
        messages.append(ChatMessage("user", "\n\n".join(context)))
        return messages

    async def _write(
        self,
        *,
        audience: Audience,
        message: str,
        history: Sequence[tuple[str, str]],
        evidence: ChatEvidence | None,
        escalation: str | None,
        specialty: str | None,
        level: str | None,
    ) -> AsyncIterator[str | dict[str, Any] | ChatOutcome]:
        """Progress events, answer text as it is written (``str``), then the
        :class:`ChatOutcome`."""
        chunks = evidence.chunks[:_SOURCES] if evidence else []
        if self._model.available:
            messages = self._messages(
                audience=audience,
                message=message,
                history=history,
                chunks=chunks,
                escalation=escalation,
                specialty=specialty,
                level=level,
            )
            yield _event("writing", "Writing the answer…")
            streamed = False
            try:
                async for item in self._model.stream(
                    messages,
                    max_tokens=1600 if audience != "patient" else 1100,
                    effort="medium" if audience == "clinician" else "low",
                ):
                    if isinstance(item, ChatResult):
                        yield ChatOutcome(
                            answer=item.text,
                            citations=_cited(item.text, chunks),
                            provider=item.provider,
                            model=item.model,
                            mode="llm" if evidence else "conversation",
                            sources=list(chunks),
                            input_tokens=item.input_tokens,
                            output_tokens=item.output_tokens,
                        )
                        return
                    streamed = True
                    yield item
            except LLMUnavailable as exc:
                if streamed:
                    raise
                logger.warning("chat_llm_unavailable", error=str(exc))
                yield _event(
                    "fallback",
                    "The AI writer is busy right now — answering straight from the sources.",
                )
        # Offline, and someone asking what to do about their own symptom: the
        # symptom check's guidance (warning signs, home care, sources), not
        # quotes from research abstracts.
        complaint = suggest_complaint(message) if audience == "patient" else None
        if complaint is not None:
            text = guidance_answer(complaint)
            for piece in re.findall(r"\S+\s*", text):
                yield piece
            yield ChatOutcome(
                answer=text, citations=[], provider="local", model="guidance", mode="conversation"
            )
            return
        # Offline: the answer is assembled from the passages themselves.
        if not chunks:
            text = (
                GREETING
                if evidence is None
                else "I couldn't find medical sources that answer this question. Try "
                "rephrasing it with the condition or medicine name, or ask a doctor."
            )
            yield ChatOutcome(
                answer=text, citations=[], provider="local", model="offline", mode="conversation"
            )
            return
        bullets, used = extractive_answer(message, chunks)
        if not used:
            # Nothing quotable: the heuristic engine's own extraction.
            output = await HeuristicReasoner().generate(
                evidence.retrieval_query if evidence else message,
                list(chunks),
                Contradiction(detected=False),
            )
            bullets, chunks = output.text, list(output.ordered_chunks)
        lead = {
            "patient": "Here is what the medical sources say, in their own words (the AI "
            "writer isn't switched on on this server, so nothing is paraphrased):",
            "clinician": "Key findings from the retrieved evidence (quoted — no language model "
            "is configured):",
            "student": "What the sources say (quoted):",
        }[audience]
        text = f"{lead}\n\n{bullets}"
        for piece in re.findall(r"\S+\s*", text):
            yield piece
        yield ChatOutcome(
            answer=text,
            citations=_cited(text, list(chunks)),
            provider="local",
            model="extractive",
            mode="extractive",
            sources=list(chunks),
        )

    # -- one message ---------------------------------------------------------------------

    async def reply(
        self,
        *,
        message: str,
        audience: Audience,
        org_id: UUID | None,
        user_id: UUID | None,
        session_id: UUID | None = None,
        kind: str = "chat",
        specialty: str | None = None,
        level: str | None = None,
        history: Sequence[tuple[str, str]] = (),
        persist: bool = True,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream one assistant turn as SSE-shaped events. ``persist=False``
        (the public widget) stores nothing and takes history from the caller."""
        started = time.perf_counter()
        message = message.strip()[:MAX_MESSAGE_CHARS]
        with cost.collecting() as collector:
            try:
                async for event in self._reply(
                    message=message,
                    audience=audience,
                    org_id=org_id,
                    user_id=user_id,
                    session_id=session_id,
                    kind=kind,
                    specialty=specialty,
                    level=level,
                    history=list(history),
                    persist=persist,
                    started=started,
                ):
                    yield event
            finally:
                if persist and org_id is not None:
                    await cost.flush(self._pool, collector, org_id=org_id)

    async def _reply(
        self,
        *,
        message: str,
        audience: Audience,
        org_id: UUID | None,
        user_id: UUID | None,
        session_id: UUID | None,
        kind: str,
        specialty: str | None,
        level: str | None,
        history: list[tuple[str, str]],
        persist: bool,
        started: float,
    ) -> AsyncIterator[dict[str, Any]]:
        blocked: tuple[str, str] | None = None
        if carries_phi(message):
            blocked = ("phi", PHI_REFUSAL)
        elif is_prompt_injection(message):
            blocked = ("prompt_injection", INJECTION_REFUSAL)

        # The record first, so the turn exists even if the answer fails —
        # identifiers are never written (the placeholder is).
        query_id: UUID | None = None
        if persist and org_id is not None and user_id is not None:
            async with tenant_connection(self._pool, org_id, user_id) as conn:
                if session_id is not None and not await self._repo.owns_session(
                    conn, session_id=session_id, user_id=user_id
                ):
                    session_id = None
                if session_id is None:
                    session_id = await self._repo.create_session(
                        conn,
                        org_id=org_id,
                        user_id=user_id,
                        title=withhold_phi(message)[:120],
                        kind=kind,
                    )
                else:
                    history = await self._repo.history(conn, session_id=session_id)
                query_id = await self._repo.create_query(
                    conn,
                    session_id=session_id,
                    org_id=org_id,
                    user_id=user_id,
                    raw_query=withhold_phi(message),
                    audience=audience,
                    contextualized_query=None,
                )
        yield _event(
            "accepted",
            "Message received.",
            session_id=str(session_id) if session_id else None,
            query_id=str(query_id) if query_id else None,
        )

        if blocked is not None:
            code, text = blocked
            if query_id is not None and org_id is not None and user_id is not None:
                async with tenant_connection(self._pool, org_id, user_id) as conn:
                    await conn.execute(
                        "UPDATE public.queries SET status = 'blocked', "
                        "guardrail_verdict = $2::jsonb WHERE id = $1",
                        query_id,
                        f'{{"blocked_by": "{code}"}}',
                    )
            yield _event("blocked", text, code=code)
            return

        flags = detect_red_flags(message)
        escalation = flags.banner if flags.triggered else None
        if escalation:
            yield _event("escalation", escalation, categories=flags.categories)

        evidence: ChatEvidence | None = None
        # Offline guidance for a personal complaint is the symptom check's
        # own advice: there is nothing for a literature search to add.
        offline_guidance = (
            not self._model.available
            and audience == "patient"
            and suggest_complaint(message) is not None
        )
        if not is_small_talk(message) and not offline_guidance:
            question = retrieval_query_for(message, history)
            async for found in self.gather_evidence(question, org_id=org_id, specialty=specialty):
                if isinstance(found, ChatEvidence):
                    evidence = found
                else:
                    yield found

        outcome: ChatOutcome | None = None
        async for item in self._write(
            audience=audience,
            message=message,
            history=history,
            evidence=evidence,
            escalation=escalation,
            specialty=specialty,
            level=level,
        ):
            if isinstance(item, ChatOutcome):
                outcome = item
            elif isinstance(item, str):
                yield {"stage": "token", "message": "", "data": {"text": item}}
            else:
                yield item
        assert outcome is not None

        if outcome.citations and outcome.mode != "conversation":
            yield _event("checking", "Checking each statement against its source…")
            outcome.check = await check_against_sources(outcome.answer, outcome.sources)
        citations = assign_stances(outcome.answer, outcome.citations, None)
        latency_ms = int((time.perf_counter() - started) * 1000)
        details = {
            "provider": outcome.provider,
            "model": outcome.model,
            "mode": outcome.mode,
            "audience": audience,
            "specialty": specialty,
            "check": outcome.check,
            "escalation": escalation,
            "live": (
                {
                    "term": evidence.live.term,
                    "searched": evidence.live.searched,
                    "found": evidence.live.found,
                    "added": evidence.live.added,
                    "source": evidence.live.source,
                }
                if evidence and evidence.live
                else None
            ),
            "labels_added": evidence.labels_added if evidence else 0,
            "coverage": round(evidence.coverage, 2) if evidence else None,
        }

        answer_id: UUID | None = None
        if query_id is not None and org_id is not None and user_id is not None:
            answer_id = await self._save(
                org_id=org_id,
                user_id=user_id,
                query_id=query_id,
                outcome=outcome,
                citations=citations,
                details=details,
                latency_ms=latency_ms,
                prompt=PROMPTS[audience],
            )
        yield _event(
            "result",
            "Complete.",
            suggest_check=suggest_complaint(message) if audience == "patient" else None,
            answer_id=str(answer_id) if answer_id else None,
            query_id=str(query_id) if query_id else None,
            session_id=str(session_id) if session_id else None,
            answer=outcome.answer,
            citations=[c.model_dump(mode="json") for c in citations],
            latency_ms=latency_ms,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            **details,
        )

    async def _save(
        self,
        *,
        org_id: UUID,
        user_id: UUID,
        query_id: UUID,
        outcome: ChatOutcome,
        citations: list[Citation],
        details: dict[str, Any],
        latency_ms: int,
        prompt: tuple[str, int],
    ) -> UUID:
        import json

        grades = [c.evidence_grade for c in citations if c.evidence_grade]
        best = min(grades) if grades else None  # "A" < "B" alphabetically = strongest
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            answer_id = await conn.fetchval(
                """
                INSERT INTO public.answers (
                    query_id, org_id, content, citations, confidence, evidence_grade,
                    has_contradiction, abstained, model, prompt_version, latency_ms,
                    input_tokens, output_tokens, cost_usd, reasoning
                ) VALUES ($1, $2, $3, $4::jsonb, $5, $6, false, false, $7, $8, $9,
                          $10, $11, 0, $12::jsonb)
                RETURNING id
                """,
                query_id,
                org_id,
                outcome.answer,
                json.dumps([c.model_dump(mode="json") for c in citations]),
                "moderate" if citations else "low",
                best,
                f"{outcome.provider}:{outcome.model}",
                f"{prompt[0]}.v{prompt[1]}",
                latency_ms,
                outcome.input_tokens,
                outcome.output_tokens,
                json.dumps({"chat": details}),
            )
            await conn.execute(
                "UPDATE public.queries SET status = 'completed' WHERE id = $1", query_id
            )
        return UUID(str(answer_id))


def _cited(answer: str, chunks: Sequence[RetrievedChunk]) -> list[Citation]:
    """Citations for the markers the answer uses (all passages when it uses
    none, so a reader can still open what the answer was written from)."""
    used = sorted(markers_in(answer))
    chosen = [m for m in used if 1 <= m <= len(chunks)]
    if not chosen:
        chosen = list(range(1, min(len(chunks), 3) + 1))
    return [citation_from_chunk(m, chunks[m - 1]) for m in chosen]
