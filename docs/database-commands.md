# 🐘 Database commands

Commands for looking at and working with the bots' PostgreSQL database, on the VPS (prod) and at home (the `dev-db` container). They're written for PowerShell 7 and run from `D:\CODE\LalaBot`.

The setup in short: there's one database, `alubot`. Each bot has its own schema in it: `alubot`, `irenesbot` and `dota2bot`. On the VPS it's a native PostgreSQL 19, and the bots connect through its Unix socket as `ubuntu`. At home it's a `postgres:19` container on `localhost:5433` (see [dev-db/compose.yml](../dev-db/compose.yml)).

## 🔌 Connect

The easiest way to explore is an interactive `psql` session, where you type SQL and see the results.

Prod (`-t` gives ssh a terminal, so psql can be interactive):

```powershell
ssh -t vps psql -d alubot
```

Home (start the container first, if Docker Desktop didn't already):

```powershell
make db
```

```powershell
docker compose -f dev-db/compose.yml exec postgres psql -U postgres -d alubot
```

## 🧭 Inside psql

Lines starting with `\` are psql's own commands, not SQL. SQL ends with `;`.

| Command | What it shows |
| --- | --- |
| `\dn` | schemas |
| `\dt irenesbot.*` | the tables of one schema |
| `\d irenesbot.tokens` | one table: columns, types, defaults, keys, indexes |
| `\df public.*` | functions (e.g. `similarity` from `pg_trgm`) |
| `\x` | toggle one-column-per-line output, for wide rows |
| `\q` | quit |

## ⚡ One query without a session

Pipe the SQL in. Don't put it in `ssh vps '...'` with quotes: PowerShell and the remote shell between them lose the inner quotes, and the query falls apart.

```powershell
"select version, name, applied_at from irenesbot.schema_migrations order by version;" | ssh vps psql -d alubot
```

The same at home:

```powershell
"select version, name, applied_at from irenesbot.schema_migrations order by version;" | docker compose -f dev-db/compose.yml exec -T postgres psql -U postgres -d alubot
```

(`-T` because the input is a pipe, not a terminal.)

## 🗃️ Migrations

Each bot keeps its schema changes in `migrations/NNNN_description.sql`. When the bot starts, it applies the ones the database doesn't have yet, each in a transaction, and records them in `<schema>.schema_migrations`. So a schema change is a new file in the bot's repo, and the deploy applies it. Never edit an applied file; add a new one.

Which migrations a schema has, on prod:

```powershell
"select version, name, applied_at from alubot.schema_migrations order by version;" | ssh vps psql -d alubot
```

What the runner did at the last start of a bot:

```powershell
ssh vps "journalctl -u irebot --since today --no-pager | grep Migrations"
```

Try a new migration at home before pushing: start the bot locally (`make run` in its repo). It applies the file to the home database at startup, and stops with the error if the file fails.

## 🔌 Connections

Each bot's pool keeps 2 connections open and opens up to 10. They show up under the bot's name:

```powershell
"select application_name, state, count(*) from pg_stat_activity where datname = current_database() group by 1, 2 order by 1;" | ssh vps psql -d alubot
```

LalaBot's `/db` shows this too, with the database size and the biggest tables.

## 💾 Dumps

A dump is the database as SQL text: the statements that recreate it. LalaBot's daily backup is the same `pg_dump alubot`.

The schema of one bot, without data, into a file:

```powershell
ssh vps pg_dump --schema-only --schema irenesbot alubot > irenesbot-schema.sql
```

### 🧪 A throwaway copy of prod at home

Good for trying something risky on real data. Create an empty database, then stream prod's dump straight into it:

```powershell
docker compose -f dev-db/compose.yml exec -T postgres createdb -U postgres scratch
```

```powershell
ssh vps pg_dump alubot | docker compose -f dev-db/compose.yml exec -T postgres psql -U postgres -d scratch -q -v ON_ERROR_STOP=1
```

Connect to it with `-d scratch` instead of `-d alubot`. Delete it when you're done:

```powershell
docker compose -f dev-db/compose.yml exec -T postgres dropdb -U postgres scratch
```

### 🔄 Refresh the home database from prod

This replaces the home `alubot` database with a fresh copy of prod. Stop the bots running at home first. `WITH (FORCE)` would cut their connections anyway.

```powershell
docker compose -f dev-db/compose.yml exec -T postgres psql -U postgres -d postgres -c "DROP DATABASE alubot WITH (FORCE)"
```

```powershell
docker compose -f dev-db/compose.yml exec -T postgres createdb -U postgres alubot
```

```powershell
ssh vps pg_dump alubot | docker compose -f dev-db/compose.yml exec -T postgres psql -U postgres -d alubot -q -v ON_ERROR_STOP=1
```

The dump makes `ubuntu` the owner of everything, so that role has to exist at home. It does in the current container. After a fresh container (`docker compose down -v` deletes the data volume), create it once:

```powershell
docker compose -f dev-db/compose.yml exec -T postgres psql -U postgres -d postgres -c "CREATE ROLE ubuntu NOLOGIN"
```

To restore a backup from the Discord backups post instead, download its `.sql` file and pipe that in: `Get-Content alubot.sql -Raw | docker compose ...` in place of `ssh vps pg_dump alubot | docker compose ...`.
