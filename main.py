from __future__ import annotations

import asyncio
import logging
from typing import override

import discord
from discord.ext import commands

from config import TOKEN
from ext.utils.systemd import sd_notify, watchdog_loop

log = logging.getLogger(__name__)

initial_extensions = (
    "ext.cmd",
    "ext.health",
)


class LalaBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents(
            guilds=True,
            messages=True,
        )
        super().__init__(
            command_prefix=commands.when_mentioned,
            help_command=None,
            intents=intents,
            activity=discord.Streaming(
                name='\N{BLACK HEART} type "/" in #jailed_bots',
                url="https://www.twitch.tv/irene_adler__",
            ),
        )
        self.watchdog_task: asyncio.Task[None] | None = None

    async def on_ready(self) -> None:
        log.info("Logged in as %s", self.user)

    @override
    async def setup_hook(self) -> None:
        sd_notify("READY=1")
        self.watchdog_task = asyncio.create_task(watchdog_loop())
        for extension in initial_extensions:
            try:
                await self.load_extension(extension)
            except Exception:
                log.exception("Failed to load extension %s.", extension)


async def run_bot() -> None:
    discord.utils.setup_logging()
    async with LalaBot() as bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(run_bot())
