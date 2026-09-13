from __future__ import annotations

import asyncio
import logging
from typing import override

import discord
from discord.ext import commands

from config import TOKEN

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

initial_extensions = (
    "ext.watcher",
    "ext.cmd",
)


class LalaBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents(
            guilds=True,
            members=True,
            presences=True,
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

    async def on_ready(self) -> None:
        log.info("Logged in as %s", self.user)

    @override
    async def setup_hook(self) -> None:
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
