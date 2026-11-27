# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
from __future__ import annotations

import asyncio
import logging
from typing import override

import discord
from discord import app_commands
from discord.ext import commands

from ext.utils.systemd import is_systemd_service, sd_notify, watchdog_loop
from settings import env

log = logging.getLogger(__name__)

initial_extensions = (
    "ext.backup",
    "ext.cmd",
    "ext.health",
    "ext.jobs",
    "ext.presence",
    "ext.report",
    "ext.services",
)


class LalaTree(app_commands.CommandTree["LalaBot"]):
    """Command tree that tells the user (ephemerally) when a slash command fails, and logs the error.

    Without it, discord.py only logs the error and Discord shows "The application did not respond".
    """

    @override
    async def on_error(self, interaction: discord.Interaction[LalaBot], error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CommandSignatureMismatch):
            log.warning("Slash command %s is out of sync with Discord: %s", interaction.command, error)
            text = "This command changed since the last sync: run `@LalaBot sync` and try again."
        else:
            name = interaction.command.qualified_name if interaction.command else "?"
            log.error("Unhandled error in /%s", name, exc_info=error)
            # Most errors arrive wrapped in `CommandInvokeError`; show what the command itself raised.
            original = getattr(error, "original", error)
            details = f"{type(original).__name__}: {original}"[:1900]
            text = f"Something broke:\n```\n{details}\n```"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            # E.g. the interaction expired (3 s to respond, 15 min for followups); the error is logged above anyway.
            log.warning("Could not tell the user about the error in %s", interaction.command, exc_info=True)


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
            tree_cls=LalaTree,
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
        await bot.start(env.DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(run_bot())
