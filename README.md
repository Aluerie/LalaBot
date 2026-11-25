# 🤖 LalaBot

A small Discord bot that looks after my other bots on the VPS
([AluBot](https://github.com/Aluerie/AluBot), IreBot, Dota2Bot),
so I can manage them from Discord, e.g. restart one from my phone when it is broken.

## 🎮 Commands

All of them are owner-only.

* `/restart <service>`, `/start <service>`, `/stop <service>` — run `sudo systemctl` on a bot's service.
  LalaBot can only be restarted from Discord: once stopped, nothing could start it back.
* `/status` — whether each service is running, since when, and how many times systemd auto-restarted it.
* `/tail <service> [lines]` — the latest lines of a service's systemd journal, right in the chat as a code block.
* `/journal <service> [lines]` — the latest lines of a service's systemd journal, as a file.
* `/logs <service>` — the log file a bot writes itself (`.temp/*.log` in its folder), as a file.
* `/backup` — back up the VPS PostgreSQL database right now.
  It also happens on its own every day at 18:05 UTC.

## 🗂️ Bot-stuff forum

Whatever LalaBot posts on its own (e.g. database backups) goes to a forum channel,
one post per feature, so features don't need a channel each.
Each post is created by hand, and its thread ID goes into [ext/utils/const.py](ext/utils/const.py).
LalaBot needs these permissions in the forum: View Channel, Send Messages in Posts and Attach Files.

## ⚙️ Config

Copy [config.example.py](config.example.py) to `config.py` (git-ignored) and fill in the values.

## 🩺 Monitoring

LalaBot doesn't watch the other bots anymore; monitoring lives outside of any bot:

* **healthchecks.io** — every bot (AluBot, IreBot, Dota2Bot, LalaBot) pings its own check every few minutes while it's healthy.
  When pings stop (bot down, stuck, disconnected, or the whole VPS down), healthchecks.io notifies me.
  LalaBot's check URL goes into `config.py` as `HEALTHCHECK_URL`.
* **systemd `ExecStopPost=`** — [scripts/notify_failure.sh](scripts/notify_failure.sh) posts to a Discord webhook
  right away whenever a service crashes, gets killed by the watchdog, or fails to start.
  The webhook URL lives on the VPS in `/home/ubuntu/.config/failure-webhook-url`.
  A crash loop only posts once per 30 minutes per service, counting the failures in between.
* **systemd watchdog** — with `Type=notify` + `WatchdogSec=`, a bot whose event loop gets stuck is restarted automatically.
* **Disk space and memory** — `vps-resources.timer` runs [scripts/check_resources.sh](scripts/check_resources.sh)
  every 5 minutes, which posts to the same webhook when `/` is 90%+ full or less than 10% of RAM is available,
  reminds every 6 hours while it lasts, and posts again once it's fine.
* **Database backups** — after every successful backup LalaBot pings a daily healthchecks.io check
  (`BACKUP_HEALTHCHECK_URL` in `config.py`), and pings its `/fail` URL when the daily backup fails.
  So healthchecks.io alerts both on a failed backup and on a day without one.
* **LalaBot's Discord status** — every minute LalaBot checks the other bots' services and shows the result
  in its status: 🟢 online "All 3 bots up", 🟡 idle while one is (re)starting, 🔴 Do Not Disturb when one is down.
* **Check it all** — `make monitoring` shows whether every bot and the timer above are set up on the VPS.
