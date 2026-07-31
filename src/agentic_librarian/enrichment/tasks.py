"""Cloud Tasks enqueue for the deep-enrichment pass (Lift 2 Stage 3).

The fast /books pass enqueues a task that re-enters the service as POST
/internal/enrich/{work_id} with the queue's OIDC token. Cloud Run throttles CPU after a
response, so an in-process background thread is unreliable; a queued task runs as a fresh
request with full CPU + long timeout.

Config (wired in prod in Stage 4; absent in local dev → enqueue is a logged no-op):
  CLOUD_TASKS_QUEUE     full queue path projects/<p>/locations/<loc>/queues/<q>
  ENRICH_TARGET_BASE_URL  the Cloud Run base URL (no trailing slash)
  ENRICH_INVOKER_SA     service-account email the queue signs the OIDC token as
  ENRICH_OIDC_AUDIENCE  the OIDC audience. The enqueue side defaults it to the full task
                        URL, but the receiver (api/internal.py) hard-requires it and 403s
                        when unset — so in prod it must be set to match on both sides.

The same config env vars now also drive POST /internal/complete-edition/{work_id} (the
edition-completion pass enqueued by enqueue_edition_completion after a history format change)."""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from urllib.parse import quote

logger = logging.getLogger(__name__)


_client_cached = None
_client_lock = threading.Lock()


def _client():
    """Seam for tests. Cached (GH #93): CloudTasksClient opens a gRPC channel + auth —
    building one per enqueued row made a 2000-row commit open 2000 channels. Lazily
    imports google-cloud-tasks so the dependency is only needed where enqueue runs."""
    global _client_cached
    if _client_cached is None:
        with _client_lock:
            if _client_cached is None:
                from google.cloud import tasks_v2

                _client_cached = tasks_v2.CloudTasksClient()
    return _client_cached


def enqueue_enrichment(work_id: str, user_id: str | None = None, schedule_time: datetime | None = None) -> bool:
    """Enqueue the deep-enrichment task for work_id. Returns True if enqueued, False if
    Cloud Tasks is not configured (local dev) — the caller treats a False/raised result as
    non-fatal so the fast add still succeeds.

    user_id (#100) attributes the eventual /internal/enrich metering to the requesting user;
    omitted for pre-#100 callers, which run un-attributed exactly as before. schedule_time is
    added here (used starting Task 4) so tasks.py is touched once for the arc."""
    queue = os.environ.get("CLOUD_TASKS_QUEUE")
    base = os.environ.get("ENRICH_TARGET_BASE_URL")
    sa = os.environ.get("ENRICH_INVOKER_SA")
    if not (queue and base and sa):
        logger.info("enrichment enqueue skipped — Cloud Tasks not configured (work %s)", work_id)
        return False

    # Audience is bound to the bare PATH url on purpose — see enqueue_edition_completion's
    # comment: a per-call query string (user_id here, format there) must never leak into the
    # audience the receiver checks against a single fixed ENRICH_OIDC_AUDIENCE.
    audience_url = f"{base.rstrip('/')}/internal/enrich/{work_id}"
    url = audience_url
    if user_id:
        url += f"?user_id={quote(user_id)}"
    audience = os.environ.get("ENRICH_OIDC_AUDIENCE") or audience_url
    task = {
        "http_request": {
            # String, not tasks_v2.HttpMethod.POST, on purpose: proto-plus coerces it, and
            # keeping tasks_v2 out of here contains the google-cloud-tasks import to _client()
            # so enqueue_enrichment (and its unit tests) need no such dependency installed.
            "http_method": "POST",
            "url": url,
            "oidc_token": {"service_account_email": sa, "audience": audience},
        }
    }
    if schedule_time is not None:
        task["schedule_time"] = schedule_time  # proto-plus coerces datetime -> Timestamp
    _client().create_task(parent=queue, task=task)
    logger.info("enqueued deep enrichment for work %s", work_id)
    return True


def enqueue_edition_completion(
    work_id: str, fmt: str, user_id: str | None = None, schedule_time: datetime | None = None
) -> bool:
    """Enqueue the format-completion pass for (work_id, fmt) — history-format-edit spec.
    Returns True if enqueued, False if Cloud Tasks is not configured (local dev); the
    caller (PATCH /history) treats False/raised as non-fatal — the edit is already saved.

    user_id (#100) attributes the eventual /internal/complete-edition metering to the
    requesting user; omitted for pre-#100 callers. schedule_time added for Task 4."""
    queue = os.environ.get("CLOUD_TASKS_QUEUE")
    base = os.environ.get("ENRICH_TARGET_BASE_URL")
    sa = os.environ.get("ENRICH_INVOKER_SA")
    if not (queue and base and sa):
        logger.info("edition-completion enqueue skipped — Cloud Tasks not configured (work %s)", work_id)
        return False

    path_url = f"{base.rstrip('/')}/internal/complete-edition/{work_id}"
    url = f"{path_url}?format={quote(fmt)}"
    if user_id:
        url += f"&user_id={quote(user_id)}"
    # Audience deliberately excludes the query string: the receiver verifies against a
    # single fixed ENRICH_OIDC_AUDIENCE, so a per-format/per-user audience would never match.
    audience = os.environ.get("ENRICH_OIDC_AUDIENCE") or path_url
    task = {
        "http_request": {
            "http_method": "POST",
            "url": url,
            "oidc_token": {"service_account_email": sa, "audience": audience},
        }
    }
    if schedule_time is not None:
        task["schedule_time"] = schedule_time  # proto-plus coerces datetime -> Timestamp
    _client().create_task(parent=queue, task=task)
    logger.info("enqueued edition completion for work %s format %s", work_id, fmt)
    return True
