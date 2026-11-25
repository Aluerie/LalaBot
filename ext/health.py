from __future__ import annotations

import logging
from typing import TYPE_CHECKING, override

import aiohttp
from discord.ext import commands, tasks

import config

if TYPE_CHECKING:
    from main import LalaBot


log = logging.getLogger(__name__)

HEALTHCHECK_URL: str | None = getattr(config, "HEALTHCHECK_URL", None)


class Health(commands.Cog):
    """Pings an external healthchecks.io check while the bot is connected to Discord.

    When the pings stop (bot down, stuck, disconnected, or the whole VPS down), healthchecks.io alerts the owner.
    """

    def __init__(self, bot: LalaBot) -> None:
        self.bot: LalaBot = bot
        self.session: aiohttp.ClientSession | None = None

    @override
    async def cog_load(self) -> None:
        if HEALTHCHECK_URL is None:
            log.warning("HEALTHCHECK_URL is not set in config.py, so nothing reports that LalaBot is alive.")
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
        if HEALTHCHECK_URL is None or self.session is None or not self.bot.is_ready():
            return
        try:
            async with self.session.get(HEALTHCHECK_URL) as response:
                response.raise_for_status()
        except (TimeoutError, aiohttp.ClientError):
            log.warning("Failed to ping healthchecks.io", exc_info=True)

    @ping_loop.before_loop
    async def before(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: LalaBot) -> None:
    """Load LalaBot extension. Framework of discord.py."""
    await bot.add_cog(Health(bot))
