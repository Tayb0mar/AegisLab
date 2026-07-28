# AegisLab — MySQL Setup (MySQL Workbench, Windows)

MySQL is the official database of the project. SQLite is not used.

## 1. Prerequisites

- MySQL Server 8.x installed and running (the Windows MySQL Installer sets it
  up as a service).
- MySQL Workbench connected to your local server.

## 2. Create the schema

1. Open MySQL Workbench and connect to your local instance.
2. `File → Open SQL Script…` → select `sql/schema.sql` from this repository.
3. Execute it (lightning-bolt icon). The script is idempotent: it can be run
   repeatedly without duplicate-index errors because all indexes are declared
   inline in the `CREATE TABLE IF NOT EXISTS` statements.
4. Refresh the schema panel: you should see the `aegislab` database with
   tables `sensor_readings`, `alerts`, `system_events`.

## 3. Create a least-privilege application account

Run in a Workbench query tab, choosing your own password:

```sql
CREATE USER IF NOT EXISTS 'aegislab_app'@'localhost' IDENTIFIED BY 'CHOOSE_A_PASSWORD';
GRANT SELECT, INSERT, UPDATE ON aegislab.* TO 'aegislab_app'@'localhost';
FLUSH PRIVILEGES;
```

The application never needs `DROP`, `CREATE` or `DELETE` privileges
(SR-006). Do not use the root account in `.env`.

## 4. Configure the backend

Copy `.env.example` to `.env` and set:

```dotenv
DB_BACKEND=mysql
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=aegislab
DB_USER=aegislab_app
DB_PASSWORD=CHOOSE_A_PASSWORD
```

`.env` is gitignored; never commit real credentials.

## 5. Optional demonstration data

`sql/seed_data.sql` inserts ~10 simulated readings, three alerts and a few
system events (all labelled `source='simulated'`). Run it the same way as the
schema, after the schema. Only for demo/development databases.

## 6. Verifying inserts

With the backend running (any mode), in Workbench:

```sql
USE aegislab;
SELECT * FROM sensor_readings ORDER BY id DESC LIMIT 10;
SELECT * FROM alerts ORDER BY id DESC LIMIT 10;
SELECT * FROM system_events ORDER BY id DESC LIMIT 10;
```

## 7. Schema notes

- Serial field `light` is stored as column `light_level` (DR-002).
- `recorded_at`/`created_at` are set by the database; the backend session
  runs with `time_zone='+00:00'`, so stored timestamps are UTC.
- `sensor_readings.source` (`device`/`simulated`) is a documented additive
  extension of the baseline schema; old rows default to `device` and existing
  queries are unaffected.
- `alerts.reading_id` is a foreign key to `sensor_readings(id)` with
  `ON DELETE SET NULL`.
- Clean reset during early development: drop and recreate the development
  database (`DROP DATABASE aegislab;` **manually in Workbench**, then re-run
  `schema.sql`). Application code never drops anything.
