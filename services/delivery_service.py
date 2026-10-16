"""Claim delivery before network I/O and fence completion after acknowledgment."""

import asyncio
from datetime import timedelta

from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError, TimedOut


NETWORK_TIMEOUT_SECONDS = 30


def delivery_error(error):
    if isinstance(error, TimedOut):
        return "unknown", "timeout"
    if isinstance(error, RetryAfter):
        return "retryable_failed", "rate_limited"
    if isinstance(error, Forbidden):
        return "permanent_failed", "forbidden"
    if isinstance(error, BadRequest):
        return "permanent_failed", "bad_request"
    if isinstance(error, NetworkError):
        return "retryable_failed", "network"
    return "permanent_failed", "telegram"


async def recorded_send(deliveries, *, destination, purpose, send, request_id=None,
                        license_id=None, explicit_retry=False, expected_version=None):
    claim = deliveries.start(
        destination=destination, purpose=purpose, request_id=request_id,
        license_id=license_id, explicit_retry=explicit_retry, expected_version=expected_version,
    )

    async def owned_send():
        deliveries.ensure_owner(claim)
        return await send()

    try:
        # The 30-second I/O deadline is shorter than the 120-second claim lease.
        sent = await asyncio.wait_for(owned_send(), timeout=NETWORK_TIMEOUT_SECONDS)
    except (TelegramError, TimeoutError) as error:
        status, category = ("unknown", "timeout") if isinstance(error, TimeoutError) else delivery_error(error)
        retry_after = None
        if isinstance(error, RetryAfter):
            delay = error.retry_after
            retry_after = delay.total_seconds() if isinstance(delay, timedelta) else max(0, delay)
        deliveries.finish(claim, status=status, error_category=category, retry_after=retry_after)
        return status
    deliveries.finish(claim, status="succeeded", message_id=getattr(sent, "message_id", None))
    return "succeeded"
