from __future__ import annotations

import logging
from typing import TYPE_CHECKING, override

import discord
from discord.ext import commands, tasks

from .utils import const
from .utils.systemd import is_systemd_service, show_services

if TYPE_CHECKING:
    from main import LalaBot


log = logging.getLogger(__name__)


async def bots_presence() -> tuple[discord.Status, str]:
    """Get the Discord status (its colour) and custom status text that sum up whether the bots are up.

    * 🟢 online: every bot is up;
    * 🟡 idle: some bot is (re)starting or stopping, and none is down;
    * 🔴 do not disturb: some bot is down (failed or stopped).
    """
    services = await show_services(const.SERVICES, ["ActiveState"])
    states = {name: props.get("ActiveState", "?") for name, props in services.items()}
    up = sum(state == "active" for state in states.values())
    down = [name for name, state in states.items() if state in {"failed", "inactive"}]
    busy = [name for name, state in states.items() if state in {"activating", "deactivating", "reloading"}]

    if down:
        return discord.Status.dnd, f"🔴 {up}/{len(states)} up · {', '.join(down)} down"
    if busy:
        return discord.Status.idle, f"🟡 {up}/{len(states)} up · {', '.join(busy)} (re)starting"
    return discord.Status.online, f"🟢 All {len(states)} bots up"


class Presence(commands.Cog):
    """Keeps LalaBot's Discord status in sync with whether the bots, LalaBot included, are up.

    So a glance at the member list (green, yellow or red LalaBot) tells whether everything is fine.
    Only runs as a systemd service: at home there are no services to check.
    """

    def __init__(self, bot: LalaBot) -> None:
        self.bot: LalaBot = bot
        self.current: tuple[discord.Status, str] | None = None

    @override
    async def cog_load(self) -> None:
        if is_systemd_service():
            self.update_presence.start()

    @override
    async def cog_unload(self) -> None:
        self.update_presence.cancel()

    @tasks.loop(minutes=1)
    async def update_presence(self) -> None:
        # An exception would stop the loop for good, so show it in the status and try again next minute.
        try:
            presence = await bots_presence()
        except Exception:
            log.exception("Failed to check the bots for the presence")
            presence = discord.Status.idle, "❔ Can't check the bots, see /tail lalabot"

        # Presence updates are rate limited, so only send actual changes.
        if presence != self.current:
            status, text = presence
            await self.bot.change_presence(status=status, activity=discord.CustomActivity(text))
            self.current = presence

    @update_presence.before_loop
    async def before_update_presence(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: LalaBot) -> None:
    """Load LalaBot extension. Framework of discord.py."""
    await bot.add_cog(Presence(bot))
