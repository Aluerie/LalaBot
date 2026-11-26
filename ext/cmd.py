# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from .utils import const

if TYPE_CHECKING:
    from main import LalaBot


log = logging.getLogger(__name__)


class Cmd(commands.Cog):
    def __init__(self, bot: LalaBot) -> None:
        self.bot: LalaBot = bot

    @commands.command()
    @commands.is_owner()
    async def sync(self, ctx: commands.Context[LalaBot]) -> None:
        synced = await ctx.bot.tree.sync()
        log.info("Synced %d slash commands", len(synced))
        text = f"synced {len(synced)} slash commands {const.HU_EMOTE}"
        try:
            await ctx.send(text)
        except discord.Forbidden:
            # The sync worked, but LalaBot can't post here: without any answer it'd look like it failed,
            # and syncing again and again only runs into Discord's rate limit.
            await ctx.author.send(f"{text} (I can't post in {ctx.channel.jump_url}, so here)")

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context[LalaBot], error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            # manual list, but whatever.
            await ctx.send(f"{const.HU_EMOTE} Use slash commands!")
        elif isinstance(error, (commands.BadLiteralArgument, commands.MissingRequiredArgument)):
            await ctx.send(str(error))
        elif isinstance(error, commands.NotOwner):
            await ctx.send(f"{const.HU_EMOTE} Not for you.")
        else:
            log.error("Unhandled error in command %s", ctx.command, exc_info=error)


async def setup(bot: LalaBot) -> None:
    """Load LalaBot extension. Framework of discord.py."""
    await bot.add_cog(Cmd(bot))
