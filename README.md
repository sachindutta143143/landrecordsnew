# Village People Management V11

Flask + PostgreSQL app for Railway.

## Railway deploy
1. Keep the existing Postgres service and volume.
2. In the **Web** service > Variables > Add Reference, select the Postgres service's `DATABASE_URL` and create a Web variable named `DATABASE_URL`.
3. Redeploy Web. `start.sh` checks the same PostgreSQL URL aliases as `app.py`, then runs `flask --app app init-db` when a DB configuration exists.
4. Verify `/health/config` and `/health/db` as described in `RAILWAY_FIX.txt`.

`db.create_all()` creates missing tables; it does not delete existing tables or records. No SQLite fallback is used for application records. If no PostgreSQL variable is injected, only the diagnostic/setup page is served.

**Security:** Admin no-login mode is enabled at the owner's request. Anyone with the public URL can administer records. Do not use with real personal data unless access is otherwise restricted.
