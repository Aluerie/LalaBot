from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from discord import app_commands

from main import LalaTree


def make_interaction(*, responded: bool) -> MagicMock:
    interaction = MagicMock()
    interaction.response.is_done.return_value = responded
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.command.qualified_name = "restart"
    return interaction


def run_on_error(interaction: MagicMock, error: app_commands.AppCommandError) -> None:
    tree = LalaTree.__new__(LalaTree)  # `on_error` doesn't touch the tree's state, so skip its client-bound __init__
    asyncio.run(tree.on_error(interaction, error))


def test_on_error_shows_the_original_error_ephemerally() -> None:
    interaction = make_interaction(responded=False)
    error = app_commands.CommandInvokeError(interaction.command, ValueError("boom"))
    run_on_error(interaction, error)
    interaction.response.send_message.assert_awaited_once()
    text = interaction.response.send_message.await_args.args[0]
    assert "ValueError: boom" in text
    assert interaction.response.send_message.await_args.kwargs == {"ephemeral": True}


def test_on_error_uses_a_followup_after_a_defer() -> None:
    interaction = make_interaction(responded=True)
    run_on_error(interaction, app_commands.AppCommandError("nope"))
    interaction.followup.send.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()
