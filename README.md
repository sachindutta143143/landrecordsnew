# Village People Management — Railway build

## Railway deployment
1. Deploy this folder as the `web` service.
2. In **web → Variables**, create `DATABASE_URL` using **Add Reference** from the Postgres service. Select the Postgres service's `DATABASE_PRIVATE_URL` (private networking) or `DATABASE_URL` (public URL if required). Do not leave the value empty and do not paste a made-up URL.
3. Confirm the variable is saved, then redeploy the web service.
4. Check `/health/config`, then `/health/db`. Expected: `database_configured: true` and `connected: true`.
5. Open `/admin`.

The app does not silently save to SQLite. If the PostgreSQL variable is absent, the app shows a diagnostic setup page and refuses data operations. `/health/config` reveals only variable names and URL scheme, never secrets. `/health/db` tests the real connection and creates missing tables.

## Database safety
- `db.create_all()` creates missing tables only. This build never drops tables, truncates tables, or deletes existing rows.
- `create_all()` does not migrate changed columns. If a previous schema differs, back up first and use a deliberate migration; do not reset the Railway volume.
- Existing rows are preserved. Do not delete the Postgres service or `postgres-volume`.

## Features
- Admin: all village and person records, aggregate statistics, village-wise and gender-wise summaries, add/edit/delete, filters, CSV export, temporary president links and revocation.
- President: token-based access limited to their own village; add/view/search/filter/export own village records.
- Village-wise serial numbers.
- Fields: name, father's name, address, gender. No caste/community field.

## Access warning
Admin is intentionally no-login because that was requested. Anyone who knows the public `/admin` URL can view and manage all records. Do not use this mode for real personal information on a public website without adding administrator authentication.

## Start
`gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
