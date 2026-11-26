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
