"""Liveness reporting for the supervisor.

Two halves of one arrangement. ``sd_notify`` speaks the systemd notify protocol,
which ``sdnotify-supervise`` on the FreeBSD server implements as well; and
``run_watchdog`` only sends the keepalive while a real Telegram round-trip
succeeds. A wedged process — dead long-poll socket, a proxy that accepts
connections but answers nothing — therefore goes quiet and gets restarted,
which is exactly what a hang should look like from the outside.

Outside a supervisor (``NOTIFY_SOCKET`` unset) both are no-ops.
"""

import asyncio
import logging
import os
import socket

from aiogram import Bot

logger = logging.getLogger(__name__)


def sd_notify(state: str) -> bool:
    """Send a notify-protocol line. False when there is no supervisor listening."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return False
    # A leading '@' is systemd's spelling of the abstract namespace, which the
    # kernel API spells as a leading NUL.
    if address[0] == "@":
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(state.encode())
        return True
    except OSError as exc:
        logger.warning("sd_notify(%r) not delivered: %s", state, exc)
        return False


async def run_watchdog(bot: Bot, *, interval: float, probe_timeout: float) -> None:
    """Ping the supervisor every ``interval`` seconds, but only while Telegram answers."""
    if not os.environ.get("NOTIFY_SOCKET"):
        logger.info("NOTIFY_SOCKET unset — watchdog disabled (not running under a supervisor)")
        return
    logger.info("watchdog active: probe every %gs, probe timeout %gs", interval, probe_timeout)
    while True:
        await asyncio.sleep(interval)
        try:
            # The outer timeout is belt and braces: request_timeout covers the
            # HTTP call, this covers a hang that never reaches the HTTP layer.
            async with asyncio.timeout(probe_timeout + 5):
                await bot.get_me(request_timeout=int(probe_timeout))
        except Exception as exc:
            logger.warning(
                "Telegram probe failed (%s) — withholding the keepalive so the "
                "supervisor restarts us",
                type(exc).__name__,
            )
        else:
            sd_notify("WATCHDOG=1")
