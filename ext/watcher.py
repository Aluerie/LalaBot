from __future__ import annotations

import asyncio
import datetime
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

import discord
from discord.ext import commands, tasks

from .utils import const

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from main import LalaBot


log = logging.getLogger(__name__)


@dataclass
class Check:
    """Health check for one watched service, plus its failure-tracking state."""

    name: str
    func: Callable[[], Awaitable[bool]]
    color: int
    failing_since: datetime.datetime | None = None
    is_notified: bool = False

    @property
    def is_okay(self) -> bool:
        return self.failing_since is None


def get_now() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.UTC)


class Watcher(commands.Cog):
    def __init__(self, bot: LalaBot) -> None:
        self.bot: LalaBot = bot
        self.irebot_last_heartbeat: datetime.datetime = get_now()
        self.checks: list[Check] = [
            Check("AluBot", self.check_alubot, const.DISCORD_COLOR),
            Check("IreBot", self.check_irebot, const.TWITCH_COLOR),
        ]

    @override
    async def cog_load(self) -> None:
        self.watch_loop.start()

    @override
    async def cog_unload(self) -> None:
        self.watch_loop.cancel()

    async def notify(self, content: str, *, embed: discord.Embed | None = None) -> None:
        """Send a message to the lalawatch channel, logging instead of raising on failure."""
        channel = self.bot.get_channel(const.LALAWATCH_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            log.error("Lalawatch channel %s is not available. Message: %s", const.LALAWATCH_CHANNEL_ID, content)
            return
        try:
            if embed is None:
                await channel.send(content)
            else:
                await channel.send(content, embed=embed)
        except discord.HTTPException:
            log.exception("Failed to send a message to the lalawatch channel. Message: %s", content)

    async def check_alubot(self) -> bool:
        guild = self.bot.get_guild(const.TEST_GUILD_ID)
        member = guild.get_member(const.ALUBOT_ID) if guild else None
        if member is None:
            await self.notify("❌ AluBot - member is not in the cache")
            return False

        is_status_online = member.status == discord.Status.online
        if not is_status_online:
            await self.notify(f"❌ AluBot - {is_status_online=}")
        return is_status_online

    async def check_irebot(self) -> bool:
        process = await asyncio.create_subprocess_exec("sudo", "systemctl", "is-active", "--quiet", const.IREBOT_SERVICE)
        is_service_good = await process.wait() == 0
        is_webhook_good = get_now() - self.irebot_last_heartbeat < const.HEARTBEAT_MAX_AGE
        is_all_good = is_webhook_good and is_service_good
        if not is_all_good:
            await self.notify(f"❌ IreBot ```py\n{is_service_good=}\n{is_webhook_good=}\n```")
        return is_all_good

    async def run_check(self, check: Check) -> None:
        """Run one check, then update its failure state and ping the owner or announce recovery as needed."""
        try:
            is_good = await check.func()
        except Exception:
            log.exception("Check %s raised an exception.", check.name)
            await self.notify(f"❌ {check.name} - the check itself raised an exception, see logs")
            is_good = False

        now = get_now()
        if is_good:
            if check.is_notified:
                await self.notify(f"✅ {check.name} is back online")
            check.failing_since = None
            check.is_notified = False
            return

        if check.failing_since is None:
            check.failing_since = now
        if not check.is_notified and now - check.failing_since >= const.ALERT_AFTER:
            await self.notify(
                f"{const.MENTION_OWNER}, {const.MADGE_EMOTE}",
                embed=discord.Embed(color=check.color, title=f"{check.name} is offline"),
            )
            check.is_notified = True

    @tasks.loop(seconds=899)
    async def watch_loop(self) -> None:
        """Run every health check and post a summary to the lalawatch channel.

        The first iteration only announces the start, so the services get time to send heartbeats first.
        """
        if self.watch_loop.current_loop == 0:
            await self.notify("🔍 Starting the watch loop")
            return

        for check in self.checks:
            await self.run_check(check)

        await self.notify("✅" if all(c.is_okay for c in self.checks) else "❌")

    @watch_loop.before_loop
    async def before(self) -> None:
        await self.bot.wait_until_ready()

    @watch_loop.error
    async def watch_loop_error(self, error: BaseException) -> None:
        log.error("Watch loop crashed.", exc_info=error)
        await self.notify(f"{const.MENTION_OWNER}, {const.MADGE_EMOTE} the watch loop crashed and stopped, see logs")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.channel.id == const.HEARTBEAT_CHANNEL_ID and message.webhook_id is not None:
            self.irebot_last_heartbeat = get_now()


async def setup(bot: LalaBot) -> None:
    """Load LalaBot extension. Framework of discord.py."""
    await bot.add_cog(Watcher(bot))
