#!/bin/sh
# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
# Show, for every bot on the VPS, whether its outage alerts are set up:
# the unit is linked from the repo, uses notify + watchdog, runs the crash alert script, and has a healthcheck URL.
# Run it on the VPS:  sh ~/LalaBot/scripts/check_monitoring.sh   (or `make monitoring` from LalaBot at home)

ok() { printf '  \033[32m✔\033[0m %s\n' "$1"; }
bad() { printf '  \033[31m✘\033[0m %s\n' "$1"; }
check() { if eval "$1"; then ok "$2"; else bad "$2"; fi; }

set -f  # the patterns below contain `*` and `[...]`, which must not expand to file names

# unit, repo folder, file that holds HEALTHCHECK_URL, pattern of a non-empty value (no spaces: entries split on them)
for entry in \
    "lalabot LalaBot config.py ^HEALTHCHECK_URL.*=.*[\"']" \
    "irebot IrenesBot .env ^HEALTHCHECK_URL=." \
    "alubot AluBot .env ^HEALTHCHECK_URL=." \
    "dota2bot Dota2Bot .env ^HEALTHCHECK_URL=."; do
    set -- $entry
    unit=$1 repo=/home/ubuntu/$2 config=/home/ubuntu/$2/$3 pattern=$4

    printf '\n\033[35m%s\033[0m (%s)\n' "$unit" "$(systemctl is-active "$unit")"
    fragment=$(readlink -f "$(systemctl show -p FragmentPath --value "$unit")")
    check '[ "$fragment" = "$repo/$unit.service" ]' "unit file is linked from the repo ($fragment)"
    check '[ "$(systemctl show -p Type --value "$unit")" = notify ]' "Type=notify"
    check '[ "$(systemctl show -p WatchdogUSec --value "$unit")" != 0 ]' "watchdog is on"
    check 'systemctl show -p ExecStopPost --value "$unit" | grep -q /home/ubuntu/LalaBot/scripts/notify_failure.sh' "LalaBot's crash alert script runs on stop"
    check 'grep -q "$pattern" "$config" 2>/dev/null' "HEALTHCHECK_URL is set in $config"
done

printf '\n\033[35mvps-resources\033[0m (disk space, memory, recovery and update alerts)\n'
fragment=$(readlink -f "$(systemctl show -p FragmentPath --value vps-resources.timer)")
check '[ "$fragment" = /home/ubuntu/LalaBot/vps-resources.timer ]' "timer is linked from the repo ($fragment)"
check '[ "$(systemctl is-active vps-resources.timer)" = active ]' "timer is running"
check '[ "$(systemctl show -p Result --value vps-resources.service)" = success ]' "last check succeeded"
check '[ -s /home/ubuntu/.config/monitoring-healthcheck-url ]' \
    "its healthchecks.io URL is in /home/ubuntu/.config/monitoring-healthcheck-url"

printf '\n\033[35mdatabase backups\033[0m (by LalaBot)\n'
backup_url_pattern="^BACKUP_HEALTHCHECK_URL.*=.*[\"']"
check 'grep -q "$backup_url_pattern" /home/ubuntu/LalaBot/config.py 2>/dev/null' \
    "BACKUP_HEALTHCHECK_URL is set in /home/ubuntu/LalaBot/config.py"
backup_remote=$(sed -n "s/^BACKUP_REMOTE *= *[\"']\([^\"']*\)[\"'].*/\1/p" /home/ubuntu/LalaBot/config.py 2>/dev/null)
check '[ -n "$backup_remote" ]' "BACKUP_REMOTE is set in /home/ubuntu/LalaBot/config.py"
check 'command -v rclone >/dev/null' "rclone is installed"
check 'rclone lsf --max-depth 1 "$backup_remote" >/dev/null 2>&1' "rclone can list the off-site backups ($backup_remote)"

printf '\n\033[35mshared\033[0m\n'
check '[ -s /home/ubuntu/.config/failure-webhook-url ]' "crash alert webhook URL file exists"
check 'command -v curl >/dev/null' "curl is installed"
echo
echo "Not checkable from here: in healthchecks.io, every bot's check needs period 5 min / grace 9 min,"
echo "the backup check period 1 day / grace 2 hours, and all of them the Discord integration for #💖healthcheck."
echo "The monitoring check (5 min / 9 min) also needs an email or app notification: it alerts when the webhook is gone."
