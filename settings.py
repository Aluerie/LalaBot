# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""LalaBot's secrets and URLs, read from environment variables and the git-ignored `.env` file."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ("env",)


class Env(BaseSettings):
    """Values from `.env` (see `.env.example`); an environment variable of the same name wins over the file.

    Empty values count as unset, so a missing ``DISCORD_TOKEN`` stops the bot at startup with a clear error.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    DISCORD_TOKEN: str
    HEALTHCHECK_URL: str = ""
    BACKUP_HEALTHCHECK_URL: str = ""


env = Env()
