#!/usr/bin/env sh
# The scheduled-jobs process (Phase 14.5).
#
# The same image as the API, run as a second service so a long ingest or a
# nightly Living Answers sweep cannot compete with a clinician's request for
# the API's event loop. Each job is idempotent and safe to re-run, so a
# restart mid-sweep costs a repeat, never a corruption.
#
# The loop is deliberately plain: platforms differ in how they schedule, and
# a `sleep` loop that logs what it did is easier to reason about at 3am than a
# cron daemon inside a container. WORKER_INTERVAL_SECONDS sets the cadence.

set -eu

INTERVAL="${WORKER_INTERVAL_SECONDS:-60}"
DAY_SECONDS=86400
last_nightly=0
last_weekly=0

echo "worker starting: webhook drain every ${INTERVAL}s, living answers nightly, digest weekly"

while true; do
    now=$(date +%s)

    # Every tick: deliver whatever the webhook queue holds.
    python -m scripts.jobs deliver-webhooks --limit 100 || echo "deliver-webhooks failed; continuing"

    # Nightly: re-check followed answers against today's corpus.
    if [ $((now - last_nightly)) -ge "$DAY_SECONDS" ]; then
        python -m scripts.jobs living-answers --limit 500 || echo "living-answers failed; continuing"
        last_nightly=$now
    fi

    # Weekly: the opt-in evidence digest.
    if [ $((now - last_weekly)) -ge $((DAY_SECONDS * 7)) ]; then
        python -m scripts.jobs weekly-digest || echo "weekly-digest failed; continuing"
        last_weekly=$now
    fi

    sleep "$INTERVAL"
done
