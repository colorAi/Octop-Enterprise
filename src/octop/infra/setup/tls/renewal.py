"""Automatic TLS certificate renewal scheduling."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from octop.config import load_config
from octop.infra.setup.tls.manager import get_tls_manager
from octop.infra.setup.tls.modes import is_renewal_mode
from octop.infra.utils.paths import PathLayout

if TYPE_CHECKING:
    from octop.infra.cron.manager import CronManager

logger = logging.getLogger(__name__)

RENEWAL_WINDOW_DAYS = 30
_AUTO_RENEW_JOB_ID = "octop_tls_auto_renew"


def _parse_expiry(expires_at: str) -> datetime | None:
    if not expires_at:
        return None
    try:
        dt = datetime.fromisoformat(expires_at)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def cert_expires_within_days(expires_at: str, *, days: int = RENEWAL_WINDOW_DAYS) -> bool:
    expiry = _parse_expiry(expires_at)
    if expiry is None:
        return False
    remaining = expiry - datetime.now(UTC)
    return remaining.total_seconds() <= days * 86400


async def _auto_renew_job(paths: PathLayout) -> None:
    config = load_config(paths.config)
    if not is_renewal_mode(config):
        return
    if not config.tls.domains:
        return
    if not cert_expires_within_days(config.tls.expires_at):
        return

    mgr = get_tls_manager()
    if mgr.is_busy():
        logger.info("TLS auto-renew skipped: issuance already in progress")
        return

    domain = config.tls.domains[0]
    logger.info("TLS auto-renew starting for %s", domain)
    try:
        result = await mgr.start_issue(
            domain=domain,
            config=config,
            paths=paths,
            staging=config.tls.acme_staging,
            locale="en",
        )
    except RuntimeError as exc:
        logger.warning("TLS auto-renew could not start: %s", exc)
        return
    if not result.ok:
        logger.warning("TLS auto-renew preflight failed")


def install_auto_renewal_job(cron_manager: CronManager, *, paths: PathLayout) -> None:
    """Schedule a daily job to renew Let's Encrypt certs within the renewal window."""
    config = load_config(paths.config)
    if not is_renewal_mode(config):
        return

    async def _run() -> None:
        await _auto_renew_job(paths)

    cron_manager.schedule_system_job(
        _AUTO_RENEW_JOB_ID,
        trigger="cron:0 3 * * *",
        func=_run,
    )
    logger.info("TLS auto-renewal job scheduled (daily 03:00)")
