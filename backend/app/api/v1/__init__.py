"""Versioned API surface. Feature routers are mounted here phase by phase."""

from fastapi import APIRouter

from app.api.v1 import (
    analytics,
    answers,
    api_keys,
    assistant,
    auth,
    batches,
    binders,
    corpus,
    digest,
    documents,
    feedback,
    me,
    notifications,
    orgs,
    queries,
    sessions,
    sharing,
    suggest,
    voice,
    webhooks,
)

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(orgs.router)
router.include_router(queries.router)
router.include_router(answers.router)
router.include_router(batches.router)
router.include_router(sessions.router)
router.include_router(documents.router)
router.include_router(feedback.router)
router.include_router(binders.router)
router.include_router(sharing.router)
router.include_router(notifications.router)
router.include_router(digest.router)
router.include_router(analytics.router)
router.include_router(corpus.router)
router.include_router(suggest.router)
router.include_router(webhooks.router)
router.include_router(voice.router)
router.include_router(api_keys.router)
router.include_router(assistant.router)
router.include_router(me.router)
