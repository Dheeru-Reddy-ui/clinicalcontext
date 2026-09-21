"""Supabase Storage upload for source PDFs (best effort locally).

Uses the Storage REST API with the service-role key. When Supabase is not
provisioned (placeholder keys / unreachable), the failure is logged and the
document proceeds with storage_path = None — parsing never depends on it.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import structlog

from app.config import get_settings

logger = structlog.stdlib.get_logger("app.ingestion.storage")

BUCKET = "guidelines"


async def upload_guideline_pdf(pdf_path: Path) -> str | None:
    """Upload a PDF to the guidelines bucket; returns the storage path or None."""
    settings = get_settings()
    base = settings.supabase_url.rstrip("/")
    service_key = settings.supabase_service_role_key.get_secret_value()
    headers = {"Authorization": f"Bearer {service_key}", "apikey": service_key}
    object_path = f"{BUCKET}/{pdf_path.name}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Idempotent bucket creation (409 = already exists).
            bucket_response = await client.post(
                f"{base}/storage/v1/bucket",
                headers=headers,
                json={"id": BUCKET, "name": BUCKET, "public": False},
            )
            if bucket_response.status_code not in (200, 201, 409):
                bucket_response.raise_for_status()

            upload_response = await client.post(
                f"{base}/storage/v1/object/{object_path}",
                headers={**headers, "Content-Type": "application/pdf", "x-upsert": "true"},
                content=pdf_path.read_bytes(),
            )
            upload_response.raise_for_status()
            logger.info("guideline_uploaded", path=object_path)
            return object_path
    except (httpx.HTTPError, OSError) as exc:
        logger.warning(
            "guideline_upload_skipped",
            file=pdf_path.name,
            error=str(exc),
            hint="storage_path stays NULL; provision Supabase to enable uploads",
        )
        return None
