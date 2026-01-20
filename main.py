from __future__ import annotations

import asyncio
import logging
import re
from typing import Literal, Self, override

import discord
from discord.ext import commands, tasks

from config import TOKEN

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

# Global const Variables
COUNTER_LOOP_MAX = 10
COMMAND_PREFIX = "%"
DISCORD_COLOR = 0x5865F2
TWITCH_COLOR = 0x9146FF
MADGE_EMOTE = "<:DankMadgeThreat:1125591898241892482>"
MENTION_OWNER = "<@!312204139751014400>"
SPAM_CHANNEL_ID = 970823670702411810
TEST_GUILD_ID = 759916212842659850
ALUBOT_ID = 713124699663499274
LALA_BOT_ID = 812763204010246174


class LalaBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents(
            guilds=True,
            members=True,
            presences=True,
            messages=True,
        )
        super().__init__(
            command_prefix=commands.when_mentioned_or(COMMAND_PREFIX),
            help_command=None,
            intents=intents,
            activity=discord.Streaming(
                name="\N{BLACK HEART} slash in #jailed_bots",
                url="https://www.twitch.tv/irene_adler__",
            ),
        )
        # very lazy;

        # for discord status loop
        self.counter_1: int = 0
        self.is_notified_1: bool = False

        # for systemctl loop
        self.counter_2: int = 0
        self.is_notified_2: bool = False

    @override
    async def setup_hook(self) -> None:
        self.watch_loop_1.start()

    async def on_ready(self) -> None:
        log.info("Logged in as %s", self.user)

    @discord.utils.cached_property
    def test_guild(self) -> discord.Guild:
        return self.get_guild(TEST_GUILD_ID)  # pyright: ignore[reportReturnType]

    @discord.utils.cached_property
    def spam_channel(self) -> discord.TextChannel:
        return self.test_guild.get_channel(SPAM_CHANNEL_ID)  # pyright: ignore[reportReturnType]

    @tasks.loop(seconds=69)
    async def watch_loop_1(self) -> None:
        """This task checks whether @AluBot is online in discord.

        It does so via an egregious rich presence check.
        But hey, I'm not sure if I know any better ways for this.
        """
        member: discord.Member = self.test_guild.get_member(ALUBOT_ID)  # pyright: ignore[reportAssignmentType]

        if member.status == discord.Status.online:
            self.counter_1 = 0
            self.is_notified_1 = False

        elif member.status == discord.Status.offline and not self.is_notified_1:
            self.counter_1 += 1
            if self.counter_1 > COUNTER_LOOP_MAX:
                await self.spam_channel.send(
                    content=f"{MENTION_OWNER}, {MADGE_EMOTE}",
                    embed=discord.Embed(color=DISCORD_COLOR, title=f"{member.display_name} is now offline"),
                )
                self.is_notified_1 = True

    @tasks.loop(seconds=70)
    async def watch_loop_2(self) -> None:
        """This task checks whether @IreBot is online on twitch.

        It does so via checking if the service is active.

        Source
        ------
        https://stackoverflow.com/a/57208026/19217368
        """
        process = await asyncio.create_subprocess_shell("systemctl is-active --quiet service-name")
        result = await process.wait()

        if result == 0:
            self.counter_2 = 0
            self.is_notified_2 = False
        elif not self.is_notified_2:
            self.counter_2 += 1
            if self.counter_2 > COUNTER_LOOP_MAX:
                await self.spam_channel.send(
                    content=f"{MENTION_OWNER}, {MADGE_EMOTE}",
                    embed=discord.Embed(color=TWITCH_COLOR, title="IreBot is now offline"),
                )
                self.is_notified_2 = True

    @watch_loop_2.before_loop
    @watch_loop_1.before_loop
    async def before(self) -> None:
        await self.wait_until_ready()

    @override
    async def on_message(self, message: discord.Message, /) -> None:
        # it doesn't react when doing simple "@LalaBot" otherwise even with commands.when_mentioned
        # it needs a command to follow like "@LalaBot hey"
        mention_regex = re.compile(rf"<@!?{LALA_BOT_ID}>")

        if mention_regex.fullmatch(message.content):
            await message.channel.send(f"allo {MADGE_EMOTE} use slash commands;")
            return

        await self.process_commands(message)

    @override
    async def on_command_error(self, ctx: commands.Context[Self], error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            # manual list, but whatever.
            await ctx.send(f"allo {MADGE_EMOTE}. My commands are ^^^ping and ^^^system.")
        elif isinstance(error, (commands.BadLiteralArgument, commands.MissingRequiredArgument)):
            await ctx.send(str(error))


bot = LalaBot()


@bot.tree.command()
async def systemctl(
    interaction: discord.Interaction[LalaBot],
    request: Literal["restart", "stop", "start"],
    service: Literal["alubot", "irenesbot", "lalabot"],
) -> None:
    try:
        result = await asyncio.create_subprocess_shell(f"sudo systemctl {request} {service}")
        await interaction.response.send_message(f"I think we successfully did it. `result={result}`")
    except Exception:
        log.exception("Exception happened during !systemctl command", stack_info=True)
        # it might not go off
        await interaction.response.send_message("Something went wrong.")


bot.run(TOKEN)
