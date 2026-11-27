# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, override

import discord
from discord.ext import commands

from .utils import const

if TYPE_CHECKING:
    from collections.abc import Iterable

    from main import LalaBot


log = logging.getLogger(__name__)

COMPARED_KEYS = frozenset(
    {
        "name",
        "type",
        "description",
        "options",
        "required",
        "choices",
        "value",
        "min_value",
        "max_value",
        "min_length",
        "max_length",
        "autocomplete",
        "channel_types",
        "default_member_permissions",
        "nsfw",
    }
)
"""Fields of a command (and its options and choices) that decide whether Discord has it right.

Discord adds fields of its own (IDs, versions) and fills in defaults (``contexts``, ``integration_types``) that
discord.py leaves out, so comparing everything would see a change on every start.
"""


def normalized(payload: Any) -> Any:
    """Keep only the compared fields of a command payload, recursively, dropping empty or default ones.

    Two more differences that aren't changes: Discord returns ``default_member_permissions`` as a string while
    discord.py sends a number, and it strips the emoji variation selector (U+FE0F) from texts ("⚔️" comes back "⚔").
    """
    if isinstance(payload, dict):
        return {
            key: str(value) if key == "default_member_permissions" else normalized(value)
            for key, value in payload.items()
            if key in COMPARED_KEYS and value not in (None, False, "", [])
        }
    if isinstance(payload, list):
        return [normalized(item) for item in payload]
    if isinstance(payload, str):
        return payload.replace("\N{VARIATION SELECTOR-16}", "")
    return payload


def signatures(payloads: Iterable[dict[str, Any]]) -> dict[str, str]:
    """Map each command (by type and name) to its normalized payload as JSON, for comparing."""
    return {
        f"{payload.get('type', 1)}:{payload['name']}": json.dumps(normalized(payload), sort_keys=True)
        for payload in payloads
    }


def changed_commands(local: Iterable[dict[str, Any]], remote: Iterable[dict[str, Any]]) -> list[str]:
    """Name the commands that differ between LalaBot's tree and what Discord has: new, changed or removed ones."""
    ours, theirs = signatures(local), signatures(remote)
    names = {key.split(":", 1)[1] for key in ours.keys() ^ theirs.keys()}
    names |= {key.split(":", 1)[1] for key in ours.keys() & theirs.keys() if ours[key] != theirs[key]}
    return sorted(names)


class Cmd(commands.Cog):
    def __init__(self, bot: LalaBot) -> None:
        self.bot: LalaBot = bot
        self.autosync_task: asyncio.Task[None] | None = None

    @override
    async def cog_load(self) -> None:
        self.autosync_task = asyncio.create_task(self.autosync(), name="lalabot-autosync")

    @override
    async def cog_unload(self) -> None:
        if self.autosync_task is not None:
            self.autosync_task.cancel()

    async def autosync(self) -> None:
        """Once ready, sync the slash commands with Discord, but only if they changed since the last sync.

        Syncing on every start would be simpler, but Discord rate-limits it, and that's what made repeated
        manual syncs slow. Comparing first needs no state of its own: Discord's copy of the commands is the state.
        """
        await self.bot.wait_until_ready()
        try:
            local = [command.to_dict(self.bot.tree) for command in self.bot.tree.get_commands()]
            remote = await self.bot.http.get_global_commands(self.bot.application_id)  # ty: ignore[invalid-argument-type]
            changed = changed_commands(local, remote)  # ty: ignore[invalid-argument-type]
            if not changed:
                log.info("Slash commands are up to date with Discord (%d), no sync needed", len(local))
                return
            synced = await self.bot.tree.sync()
            log.info("Synced %d slash commands, these changed: %s", len(synced), ", ".join(changed))
        except discord.HTTPException:
            log.exception("Autosync of the slash commands failed, `@LalaBot sync` does it by hand")

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
