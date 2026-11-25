# 🤖 LalaBot

A small Discord bot that looks after my other bots on the VPS
([AluBot](https://github.com/Aluerie/AluBot), IreBot, Dota2Bot),
so I can manage them from Discord, e.g. restart one from my phone when it is broken.

## 🎮 Commands

All of them are owner-only.

* `/systemctl <restart|stop|start> <service>` — run `sudo systemctl` on a bot's service.
* `/status` — whether each service is running, since when, and how many times systemd auto-restarted it.
* `/journal <service> [lines]` — the latest lines of a service's systemd journal, as a file.
* `/logs <service>` — the log file a bot writes itself (`.temp/*.log` in its folder), as a file.
* `/backup` — back up the VPS PostgreSQL database to the backup channel right now.
  It also happens on its own every day at 18:05 UTC.

## ⚙️ Config

Copy [config.example.py](config.example.py) to `config.py` (git-ignored) and fill in the values.

## 🩺 Monitoring

LalaBot doesn't watch the other bots anymore; monitoring lives outside of any bot:

* **healthchecks.io** — every bot (AluBot, IreBot, LalaBot) pings its own check every few minutes while it's healthy.
  When pings stop (bot down, stuck, disconnected, or the whole VPS down), healthchecks.io notifies me.
  LalaBot's check URL goes into `config.py` as `HEALTHCHECK_URL`.
* **systemd `ExecStopPost=`** — [scripts/notify_failure.sh](scripts/notify_failure.sh) posts to a Discord webhook
  right away whenever a service crashes, gets killed by the watchdog, or fails to start.
  The webhook URL lives on the VPS in `/home/ubuntu/.config/failure-webhook-url`.
  A crash loop only posts once per 30 minutes per service, counting the failures in between.
* **systemd watchdog** — with `Type=notify` + `WatchdogSec=`, a bot whose event loop gets stuck is restarted automatically.
