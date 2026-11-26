# 📝 TODO

## 🐘 Remove the old PostgreSQL 15 and 16 clusters (after 2026-12-03)

On 2026-11-26 the database moved from PostgreSQL 15 to 19 (`pg_upgradecluster`).
The old clusters are kept stopped as a fallback for a week; once the bots and the daily backups look fine on 19:

1. Check that the old 16 cluster (from December 2023, 39 MB) is empty: it should only list `postgres` and the `template` databases.

   ```sh
   sudo pg_ctlcluster 16 main start && sudo -u postgres psql -p 5434 -d template1 -c '\l'; sudo pg_ctlcluster 16 main stop
   ```

2. Drop both old clusters (deletes their data for good).

   ```sh
   sudo pg_dropcluster 15 main && sudo pg_dropcluster 16 main
   ```

3. Remove their packages, and the `postgresql` metapackage, which installs (and creates a cluster for) every new major version.

   ```sh
   sudo apt remove postgresql postgresql-15 postgresql-16 postgresql-client-15 postgresql-client-16 && sudo apt autoremove
   ```

4. At home, update the local PostgreSQL 17 to 19 before the next backup restore test: a 19 dump may not load into 17.

## 🔁 Reboot the VPS by itself when updates need it (maybe)

unattended-upgrades installs kernel updates but never reboots, so `scripts/check_updates.sh` alerts after a week
and `/reboot` is a manual step. LalaBot could do it on its own instead:

* At a quiet time, e.g. Sundays 05:00 UTC, check `/var/run/reboot-required`; if it exists, post "🔁 Rebooting
  for updates" to the bot-stuff forum and run `sudo systemctl reboot`, then post "✅ back" after the boot
  (`/reboot`'s marker file already does that part).
* Skip it when the file is younger than a day, so a fresh update gets a chance to be looked at first.
* Decide whether it's wanted at all: it trades a manual reboot for a short planned outage every few weeks.
