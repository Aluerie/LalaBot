# 🤖 LalaBot

A small Discord bot to control my bots' systemd services from Discord (`/systemctl`),
e.g. to restart [my main bot](https://github.com/Aluerie/AluBot) when it is broken.

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
