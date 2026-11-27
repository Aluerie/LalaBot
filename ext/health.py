# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, override

import aiohttp
from discord.ext import commands, tasks

from settings import env

from .utils.systemd import is_systemd_service

if TYPE_CHECKING:
    from main import LalaBot


log = logging.getLogger(__name__)


class Health(commands.Cog):
    """Pings an external healthchecks.io check while the bot is connected to Discord.

    When the pings stop (bot down, stuck, disconnected, or the whole VPS down), healthchecks.io alerts the owner.
    Only pings when running as a systemd service, so a local run at home can't hide an outage in prod.
    """

    def __init__(self, bot: LalaBot) -> None:
        self.bot: LalaBot = bot
        self.session: aiohttp.ClientSession | None = None

    @override
    async def cog_load(self) -> None:
        if not is_systemd_service():
            log.info("Not running as a systemd service, so not pinging healthchecks.io.")
            return
        if not env.HEALTHCHECK_URL:
            log.warning("HEALTHCHECK_URL is not set in .env, so nothing reports that LalaBot is alive.")
            return
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        self.ping_loop.start()

    @override
    async def cog_unload(self) -> None:
        self.ping_loop.cancel()
        if self.session is not None:
            await self.session.close()

    @tasks.loop(minutes=5)
    async def ping_loop(self) -> None:
        if not env.HEALTHCHECK_URL or self.session is None:
            return
        # `is_ready()` stays True while discord.py reconnects, so also require a live gateway connection.
        if not self.bot.is_ready() or self.bot.ws is None or not self.bot.ws.open:
            return
        try:
            async with self.session.get(env.HEALTHCHECK_URL) as response:
                response.raise_for_status()
        except TimeoutError, aiohttp.ClientError:
            log.warning("Failed to ping healthchecks.io", exc_info=True)

    @ping_loop.before_loop
    async def before(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: LalaBot) -> None:
    """Load LalaBot extension. Framework of discord.py."""
    await bot.add_cog(Health(bot))
