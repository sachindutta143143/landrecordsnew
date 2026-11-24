# Village People Management — integrated build

## Railway
1. Deploy this folder as the Web service.
2. In Web service Variables, add a Railway reference named `DATABASE_URL` pointing to the Postgres service's `DATABASE_URL`.
3. Set `SECRET_KEY` to a long random value.
4. Deploy. Check `/health/config` then `/health/db`.
5. Open `/admin`.

The app creates missing tables on first database-backed request. It does not drop tables or erase existing rows. `create_all()` does not migrate changed columns; back up the database before any schema migration.

## Access
- `/admin` is intentionally no-login as requested. Anyone who knows the public URL can see/manage all records. This is unsafe for real personal data on a public website.
- President URLs are token-based and scoped to one village. Generate them from Admin > Temporary links. The raw link is shown once after creation; copy it immediately.
- President can add, view and export own village records. Admin can add, edit, delete and export all records.

## Data
Fields: name, father's name, address, gender. No caste/community collection in this build.
