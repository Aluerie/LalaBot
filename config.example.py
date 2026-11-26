# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
# Copy this file to `config.py` (git-ignored) and fill in the values.

TOKEN = ""  # Discord bot token: Discord Developer Portal -> your application -> Bot -> Reset Token
HEALTHCHECK_URL = None  # healthchecks.io ping URL ("https://hc-ping.com/<uuid>"); only pinged when run as a systemd service
BACKUP_HEALTHCHECK_URL = None  # healthchecks.io ping URL of the daily database backup (period 1 day); same rule as above
BACKUP_REMOTE = None  # rclone destination of the off-site backup copies, e.g. "b2:my-bucket/alubot"; same rule as above
