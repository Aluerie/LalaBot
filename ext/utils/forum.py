# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from discord.ext import commands


async def get_thread(bot: commands.Bot, thread_id: int) -> discord.Thread:
    """Get a post (thread) of the bot-stuff forum by its ID, fetching it when it isn't cached.

    Archived threads aren't cached, hence the fetch. Sending a message to an archived thread unarchives it.
    Raises ``TypeError`` when the ID belongs to something other than a thread.
    """
    thread = bot.get_channel(thread_id) or await bot.fetch_channel(thread_id)
    if not isinstance(thread, discord.Thread):
        msg = f"Channel {thread_id} is not a thread."
        raise TypeError(msg)
    return thread
