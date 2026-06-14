"""
Outbound notification seam (Phase 7).

Alerts and digests reach operators through `send_email`. This is deliberately a
single module-level coroutine so it is the one place email delivery is wired up
— and the one place tests monkeypatch to capture what would have been sent
(call it as `notifier.send_email(...)`, never `from notifier import send_email`,
so the patch takes effect).

The default implementation logs the message. Real SMTP / a provider API is a
deployment concern; swap the body here without touching alert_service or
digest_service. Never log message bodies that could contain tenant PII at
INFO — keep it to recipients + subject.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def send_email(
    to: list[str], subject: str, html: str, text: str | None = None
) -> None:
    """Deliver one email to `to`. Default impl logs; production wires SMTP here."""
    if not to:
        logger.info("notifier: no recipients for %r — skipping", subject)
        return
    logger.info("notifier: sending %r to %d recipient(s)", subject, len(to))
