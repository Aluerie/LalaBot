from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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
        await ctx.bot.tree.sync()
        await ctx.send(f"synced the tree {const.HU_EMOTE}")

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
