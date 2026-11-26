# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
from __future__ import annotations

import asyncio
import logging
from typing import override

import discord
from discord.ext import commands

from config import TOKEN
from ext.utils.systemd import is_systemd_service, sd_notify, watchdog_loop

log = logging.getLogger(__name__)

initial_extensions = (
    "ext.backup",
    "ext.cmd",
    "ext.health",
    "ext.presence",
    "ext.services",
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
            # In prod, `ext.presence` replaces it with whether the bots are up once the first check is done.
            activity=discord.CustomActivity("👀 Checking the bots…" if is_systemd_service() else "🏠 Running at home"),
        )
        self.watchdog_task: asyncio.Task[None] | None = None

    async def on_ready(self) -> None:
        log.info("Logged in as %s", self.user)

    @override
    async def setup_hook(self) -> None:
        sd_notify("READY=1")
        self.watchdog_task = asyncio.create_task(watchdog_loop())
        # No try/except: a broken extension should crash the bot, so the crash alert fires and systemd retries,
        # instead of LalaBot staying online (and pinging healthchecks.io) without its commands.
        for extension in initial_extensions:
            await self.load_extension(extension)


async def run_bot() -> None:
    discord.utils.setup_logging()
    async with LalaBot() as bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(run_bot())
