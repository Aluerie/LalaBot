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

## 🐳 Move PostgreSQL into Docker (maybe, after the bots are in containers)

The bots move to Docker first and keep using the host's PostgreSQL 19 (through `host-gateway`).
Moving the database into the compose stack later is optional; decide once the bots run fine in containers.

* For: the version is pinned in `compose.yml` (`postgres:19`), so the same file gives an identical database at home;
  major upgrades become "dump, change the tag, restore" instead of `pg_upgradecluster`; no more apt packages
  and extra clusters (like the 15/16 cleanup above).
* Against: it already works, and moving it is the riskiest step of the migration (the data has to move);
  the data lives in a Docker volume, so a wrong `docker compose down -v` or volume prune deletes it;
  LalaBot's `pg_dump`/`psql` would have to run through `docker exec`; one more thing that has to be up before the bots.

## 💾 Find a second place for the database backups (someday)

The daily backups only live in the Discord backups post, so losing that channel or Discord deleting attachments loses them all.
A second copy somewhere else would close that gap. Backblaze B2 refused the signup (even through a VPN), so it should be a
service I can actually register at, or one I already have: Oracle Object Storage in the VPS's account (20 GB free) is the
easiest, though it doesn't survive losing the Oracle account. The upload could be a gzipped `rclone copyto` after each
backup, keeping ~30 days.
