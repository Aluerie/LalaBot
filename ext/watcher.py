from __future__ import annotations

import asyncio
import datetime
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, override

import discord
from discord.ext import commands, tasks

from main import LalaBot

from .utils import const

if TYPE_CHECKING:
    from main import LalaBot


@dataclass
class Check:
    name: str
    func: Any
    color: int
    counter: int = 0
    is_notified: bool = False
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def is_okay(self) -> bool:
        return self.counter == 0


def get_now() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.UTC)


class Watcher(commands.Cog):
    def __init__(self, bot: LalaBot) -> None:
        self.bot: LalaBot = bot
        self.checks: dict[str, Check] = {
            "alubot": Check("alubot", self.check_alubot, const.DISCORD_COLOR),
            "irebot": Check("irebot", self.check_irebot, const.TWITCH_COLOR, data={"dt": get_now()}),
        }

    @override
    async def cog_load(self) -> None:
        self.watch_loop.start()

    @discord.utils.cached_property
    def test_guild(self) -> discord.Guild:
        return self.bot.get_guild(const.TEST_GUILD_ID)  # pyright: ignore[reportReturnType]

    @discord.utils.cached_property
    def spam_channel(self) -> discord.TextChannel:
        return self.test_guild.get_channel(const.SPAM_CHANNEL_ID)  # pyright: ignore[reportReturnType]

    @discord.utils.cached_property
    def lalawatch_channel(self) -> discord.TextChannel:
        return self.test_guild.get_channel(const.LALAWATCH_CHANNEL_ID)  # pyright: ignore[reportReturnType]

    async def check_alubot(self) -> bool:
        member: discord.Member = self.test_guild.get_member(const.ALUBOT_ID)  # pyright: ignore[reportAssignmentType]
        return member.status == discord.Status.online

    async def check_irebot(self) -> bool:
        # service
        process = await asyncio.create_subprocess_shell("sudo systemctl is-active --quiet irebot")
        result = await process.wait()
        is_service_good = result == 0
        is_webhook_good = get_now() - self.checks["irebot"].data["dt"] < datetime.timedelta(minutes=15)
        return is_webhook_good and is_service_good

    @tasks.loop(seconds=599)
    async def watch_loop(self) -> None:
        """This task checks whether @AluBot is online in discord.

        It does so via an egregious rich presence check.
        But hey, I'm not sure if I know any better ways for this.
        """

        for check_name, check in self.checks.items():
            if await check.func():
                check.counter = 0
                check.is_notified = False
            else:
                check.counter += 1
                if check.counter > const.COUNTER_LOOP_MAX:
                    await self.spam_channel.send(
                        content=f"{const.MENTION_OWNER}, {const.MADGE_EMOTE}",
                        embed=discord.Embed(color=check.color, title=f"{check_name} is offline"),
                    )
                    check.is_notified = True
                else:
                    await self.lalawatch_channel.send("✅")

        if not_okay_checks := [c for c in self.checks.values() if not c.is_okay]:
            await self.lalawatch_channel.send(f"❌ {','.join(c.name for c in not_okay_checks)}")
        else:
            await self.lalawatch_channel.send("✅")

    @watch_loop.before_loop
    async def before(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.channel.id == const.HEARTBEAT_CHANNEL_ID:
            self.checks["irebot"].data["dt"] = get_now()


async def setup(bot: LalaBot) -> None:
    """Load LalaBot extension. Framework of discord.py."""
    await bot.add_cog(Watcher(bot))
