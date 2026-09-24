"""Shared NCBI E-utilities client: rate limiting, retries, paging.

NCBI allows 3 requests/second without an API key and 10 with one. The limiter
is a simple monotonic-clock spacing gate shared per client instance; 429/5xx
responses retry with exponential backoff.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx
import structlog

from app.config import get_settings

logger = structlog.stdlib.get_logger("app.ingestion.ncbi")

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_MAX_ATTEMPTS = 4
_RETRY_BASE_DELAY_S = 1.5
_EFETCH_BATCH = 200

# Characters illegal in XML 1.0 (control chars except tab/newline/CR). NCBI
# efetch responses occasionally contain raw control bytes inside abstract text,
# which make an otherwise-valid document unparseable — strip them before parse.
_ILLEGAL_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x84\x86-\x9f﷐-﷟￾￿]")


def sanitize_xml(xml_text: str) -> str:
    """Remove characters that are illegal in XML 1.0 but harmless to drop."""
    return _ILLEGAL_XML_CHARS.sub("", xml_text)


class NcbiClient:
    """Polite E-utilities access shared by the PubMed and PMC sources."""

    def __init__(self, *, timeout: float = 30.0, max_attempts: int = _MAX_ATTEMPTS) -> None:
        settings = get_settings()
        # Ingestion can wait out NCBI's hiccups; a person waiting on an
        # answer cannot, so the live path asks for fewer, quicker attempts.
        self._max_attempts = max(1, max_attempts)
        self._api_key = settings.ncbi_api_key.get_secret_value() if settings.ncbi_api_key else None
        self._email = settings.ncbi_email
        self._min_interval = 1.0 / (10.0 if self._api_key else 3.0)
        self._last_request = 0.0
        self._gate = asyncio.Lock()
        self._client = httpx.AsyncClient(timeout=timeout, base_url=EUTILS_BASE)

    async def close(self) -> None:
        await self._client.aclose()

    def _common_params(self) -> dict[str, str]:
        params = {"tool": "clinicalcontext"}
        if self._api_key:
            params["api_key"] = self._api_key
        if self._email:
            params["email"] = self._email
        return params

    async def _request(self, path: str, params: dict[str, str]) -> httpx.Response:
        merged = {**self._common_params(), **params}
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            async with self._gate:
                wait = self._min_interval - (time.monotonic() - self._last_request)
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last_request = time.monotonic()
            try:
                response = await self._client.get(path, params=merged)
                if response.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(
                        f"NCBI returned {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_error = exc
                if attempt == self._max_attempts:
                    break
                delay = _RETRY_BASE_DELAY_S * (2 ** (attempt - 1))
                logger.warning(
                    "ncbi_request_retry",
                    path=path,
                    attempt=attempt,
                    delay_s=delay,
                    error=str(exc),
                )
                await asyncio.sleep(delay)
        raise RuntimeError(f"NCBI request failed after {self._max_attempts} attempts: {last_error}")

    async def search_ids(
        self, *, db: str, term: str, limit: int, sort: str | None = "date"
    ) -> list[str]:
        """esearch: resolve a query to a list of record ids — by publication
        date, PubMed's Best Match with ``sort="relevance"``, or most recently
        added with ``sort=None``."""
        params = {"db": db, "term": term, "retmax": str(limit), "retmode": "json"}
        if sort:
            params["sort"] = sort
        response = await self._request("/esearch.fcgi", params)
        payload: dict[str, Any] = response.json()
        ids = payload.get("esearchresult", {}).get("idlist", [])
        return [str(record_id) for record_id in ids]

    async def summaries(self, *, db: str, ids: list[str]) -> list[dict[str, Any]]:
        """esummary: title, journal, date and publication types per id, in
        the order asked (cheaper than efetch when no abstract is needed)."""
        if not ids:
            return []
        response = await self._request(
            "/esummary.fcgi", {"db": db, "id": ",".join(ids), "retmode": "json"}
        )
        result: dict[str, Any] = response.json().get("result", {})
        return [result[i] for i in ids if isinstance(result.get(i), dict)]

    async def fetch_xml_batches(self, *, db: str, ids: list[str]) -> list[str]:
        """efetch in batches; returns one XML document string per batch."""
        batches: list[str] = []
        for start in range(0, len(ids), _EFETCH_BATCH):
            chunk = ids[start : start + _EFETCH_BATCH]
            response = await self._request(
                "/efetch.fcgi",
                {"db": db, "id": ",".join(chunk), "retmode": "xml"},
            )
            batches.append(response.text)
        return batches
