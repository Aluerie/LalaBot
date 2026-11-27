# 🤖 LalaBot

A small Discord bot that looks after my other bots on the VPS
([AluBot](https://github.com/Aluerie/AluBot), IreBot, Dota2Bot),
so I can manage them from Discord, e.g. restart one from my phone when it is broken.

## 🎮 Commands

All of them are owner-only.

* `/restart <service>`, `/start <service>`, `/stop <service>` — run `sudo systemctl` on a bot's service.
  LalaBot can only be restarted from Discord: once stopped, nothing could start it back.
* `/status` — whether each service is running, since when, and how many times systemd auto-restarted it,
  with a 🔄 Restart button next to each bot.
* `/tail <service> [lines]` — the latest lines of a service's systemd journal, right in the chat as a code block.
* `/journal <service> [lines]` — the latest lines of a service's systemd journal, as a file.
* `/grep <service> <pattern> [since]` — search a service's journal, e.g. every `Traceback` of the last day;
  shows the newest matches and attaches all of them when they don't fit.
* `/versions` — the commit each bot runs, and whether it's behind GitHub, has local changes,
  or wasn't restarted since its last commit (a deploy that didn't fully go through).
* `/vps` — the VPS's health: uptime, load, memory, disk, pending updates, whether it needs a reboot,
  failed systemd units, PostgreSQL clusters and whether the monitoring checks below pass.
* `/reboot` — reboot the VPS after a confirmation button; LalaBot says when it's back and how long it took.
* `/report [days]` — every bot's uptime, crashes and restarts, and the VPS's reboots, over the last days (7 by default).
  It's also posted to its forum post every Monday at 09:00 UTC, once `WEEKLY_REPORT_THREAD_ID` is set.
* `/db` — the database's size, connections, biggest tables and its last backup.
* `/jobs [upcoming]` — every bot's scheduled jobs (reminders, birthdays...): how many of each kind, the next ones,
  and the ones with problems (stuck, overdue, retrying or given up on), with ▶️ Retry now, 🧹 Clear or 🗑️ Delete buttons.
* `/alertwebhook` — create LalaBot's own webhook in the current channel and make the VPS alerts post through it.
  Only alerts through LalaBot's webhook can have working buttons (see below).
* `/backup` — back up the VPS PostgreSQL database right now.
  It also happens on its own every day at 18:05 UTC.

## 🗂️ Bot-stuff forum

Whatever LalaBot posts on its own (e.g. database backups) goes to a forum channel,
one post per feature, so features don't need a channel each.
Each post is created by hand, and its thread ID goes into [ext/utils/const.py](ext/utils/const.py).
LalaBot needs these permissions in the forum: View Channel, Send Messages in Posts and Attach Files.

## ⚙️ Config

Copy [.env.example](.env.example) to `.env` (git-ignored) and fill in the values. `make scp` copies it to the VPS.

## 🩺 Monitoring

The full explanation, with every notification, step-by-step walkthroughs and every file, is in
[docs/how-it-works.md](docs/how-it-works.md).

LalaBot doesn't watch the other bots anymore; monitoring lives outside of any bot:

* **healthchecks.io** — every bot (AluBot, IreBot, Dota2Bot, LalaBot) pings its own check every few minutes while it's healthy.
  When pings stop (bot down, stuck, disconnected, or the whole VPS down), healthchecks.io notifies me.
  LalaBot's check URL goes into `.env` as `HEALTHCHECK_URL`.
* **systemd `ExecStopPost=`** — [scripts/notify_failure.sh](scripts/notify_failure.sh) posts to a Discord webhook
  right away whenever a service crashes, gets killed by the watchdog, or fails to start,
  with the service's last log lines (usually the traceback) in the message,
  and 🔄 Restart, 📓 Tail and 📁 Journal buttons that LalaBot handles (when the webhook is LalaBot's, from `/alertwebhook`).
  The webhook URL lives on the VPS in `/home/ubuntu/.config/failure-webhook-url`.
  A crash loop only posts once per 30 minutes per service, counting the failures in between.
  Once a crashed service has run steadily for 5 minutes, [scripts/check_recoveries.sh](scripts/check_recoveries.sh)
  (run by `vps-resources.timer`) posts "✅ back up", with how many more failures there were since the alert.
* **systemd watchdog** — with `Type=notify` + `WatchdogSec=`, a bot whose event loop gets stuck is restarted automatically.
* **Disk space and memory** — `vps-resources.timer` runs [scripts/check_resources.sh](scripts/check_resources.sh)
  every 5 minutes, which posts to the same webhook when `/` is 90%+ full or less than 10% of RAM is available,
  reminds every 6 hours while it lasts, and posts again once it's fine.
* **Updates** — the same timer runs [scripts/check_updates.sh](scripts/check_updates.sh), which posts when security
  updates have been pending for a week (so unattended-upgrades is broken) or the VPS has needed a reboot for a week.
* **The monitoring itself** — the timer's last step, [scripts/ping_monitoring.sh](scripts/ping_monitoring.sh),
  pings a healthchecks.io check whose URL lives on the VPS in `/home/ubuntu/.config/monitoring-healthcheck-url`.
  A failed step skips it, and it pings `/fail` when the Discord webhook is gone, so a broken timer, check or webhook
  gets noticed. This check notifies by email or the healthchecks.io app, since the webhook may be what's broken.
* **Database backups** — after every successful backup LalaBot pings a daily healthchecks.io check
  (`BACKUP_HEALTHCHECK_URL` in `.env`), and pings its `/fail` URL when the daily backup fails.
  So healthchecks.io alerts both on a failed backup and on a day without one.
* **LalaBot's Discord status** — every minute LalaBot checks the bots' services (itself included) and shows the result
  in its status: 🟢 online "All 4 bots up", 🟡 idle while one is (re)starting, 🔴 Do Not Disturb when one is down.
* **Check it all** — `make monitoring` shows whether every bot and the timer above are set up on the VPS.

## 🧪 Tests

```sh
uv run pytest
```

The deploy runs them too, with Ruff and ty, before it touches the VPS.
