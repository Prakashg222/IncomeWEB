# Ledgerly daily income tracker

A Flask application for importing UIDAI ECMP HTML reports, reviewing rate-based income, and maintaining an audit trail. The existing `auto_reader.py` remains the extraction engine; income is calculated with `Decimal` using the category rules and effective-dated rates stored in the database. HTML amount-charged fields are never used as income.

## Local setup (Windows PowerShell)

Use Python 3.12 or newer and PostgreSQL for a production-like local setup. The included development configuration can also use SQLite locally; Vercel/production rejects SQLite and local file storage.

```powershell
cd C:\Users\NANI\OneDrive\Desktop\uidai_auto_reader
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Edit `.env`: set a long random `SECRET_KEY`, a local PostgreSQL `DATABASE_URL`, and a unique admin username/email/password (at least 12 characters). The development app defaults to a local SQLite database if `DATABASE_URL` is unset. Do not commit `.env`.

Initialize the database and app defaults:

```powershell
flask --app app:app db upgrade
flask --app app:app seed-config
flask --app app:app create-admin
flask --app app:app run --debug
```

Open `http://127.0.0.1:5000`. If you are initializing a new development database without migrations, run `flask --app app:app init-db` instead of `db upgrade`; it creates tables and seeds the existing category configuration. The admin account is created separately with `create-admin`.

Run the focused suite:

```powershell
python -m pytest
```

## Report workflow and rates

1. Sign in as an admin and upload an `.html` or `.htm` UIDAI ECMP report (maximum 10 MB).
2. The app extracts metadata and records, detects duplicate file content, and shows a review preview.
3. Missing rates or unmapped rows are highlighted; the report cannot be confirmed until resolved.
4. Confirming saves the report and per-record income snapshots. Pending reports never contribute to dashboard totals.

`category_rates.json` remains the seed source for categories and its existing rates; `Mandatory Biometric Update` is ₹20.00 per eligible record, and the other sample categories have no rate until configured. `seed-config` inserts missing defaults without changing existing database categories, rates, or share settings. A new rate uses an effective date; already-confirmed report totals and per-record applied rates do not change.

Revenue-share percentages are intentionally not assumed. Configure both percentages together in Admin → Settings; the app validates that they total 100%. The default eligible status is seeded from `category_rates.json`.

The dashboard date explorer lets you browse dates directly, see confirmed income and uploaded report files for the selected date, and navigate between months. Sundays are highlighted and labeled as holidays; the calendar marks dates with uploads and confirmed income.

Members can register with an Aadhaar number, email and password. Registration stores a keyed, non-reversible Aadhaar lookup digest and the final four digits only; set a stable `AADHAAR_LOOKUP_KEY` in each environment. Self-registered accounts remain pending and cannot sign in until an admin approves them from Admin → Users. Admin-created accounts are approved immediately.

## PostgreSQL and migrations

Set `DATABASE_URL` to a PostgreSQL connection URL, for example:

```text
postgresql+psycopg://user:password@host:5432/database
```

For a schema change, create and review a migration, then apply it:

```powershell
flask --app app:app db migrate -m "Describe schema change"
flask --app app:app db upgrade
```

## Monthly email

Vercel invokes `/api/monthly-email` daily at 18:25 UTC (23:55 India Standard Time). The endpoint only sends on the last calendar day of the month, uses a protected bearer token, and records each month in a unique email log. Failed months can be retried from Admin → Settings after correcting SMTP credentials. Configure `GMAIL_USERNAME`, `GMAIL_APP_PASSWORD` (a Google App Password), and an admin recipient in settings or `ADMIN_EMAIL`. A month already marked sent is never sent again.

## Vercel deployment

1. Push this project to a Git repository and import it into Vercel. The root directory must be this project.
2. Provision managed PostgreSQL and an S3-compatible object store (S3, R2, or compatible service). Set these Vercel environment variables for Production and Preview as appropriate:
   - `FLASK_ENV=production`
   - `SECRET_KEY`
   - `AADHAAR_LOOKUP_KEY` (keep this stable so members can continue signing in)
   - `DATABASE_URL` (PostgreSQL using `postgresql+psycopg://`)
   - `AWS_S3_BUCKET`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`; optionally `AWS_S3_ENDPOINT_URL` (or `AWS_ENDPOINT_URL_S3`) for an S3-compatible endpoint
   - `CRON_SECRET`
   - `ADMIN_USERNAME`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` for initial account creation
   - `COMPANY_SHARE_PERCENT` and `USER_SHARE_PERCENT` only when the existing share split is known (both must add up to 100)
   - `GMAIL_USERNAME`, `GMAIL_APP_PASSWORD`, and optionally `MAIL_FROM` for monthly reports
4. From a trusted local terminal, link the Vercel project and pull its production environment. This writes secrets to a local-only file ignored by Git:

   ```powershell
   npx vercel login
   npx vercel link
   npx vercel env pull .env.production.local --environment=production
   flask --env-file .env.production.local --app app:app db upgrade
   flask --env-file .env.production.local --app app:app seed-config
   flask --env-file .env.production.local --app app:app create-admin
   ```

5. Deploy with `npx vercel --prod`. Vercel requires PostgreSQL and object storage: production mode fails to start if the database or persistent bucket is missing. Original HTML uploads are stored in the bucket, never the serverless filesystem.
6. Keep the admin password and cloud credentials out of source control. Remove `ADMIN_PASSWORD` from Vercel and delete `.env.production.local` after setup.

`vercel.json` configures the Flask serverless entry point, static assets and daily protected monthly-report check. Vercel Cron uses `CRON_SECRET` as the bearer authorization value.

## Configuration and privacy

See `.env.example` for the supported environment variables. Resident names and source record fields are accessible only to admins and the report's uploader; uploaded source HTML is private and is downloaded through authenticated routes. Local uploads are stored under `instance/uploads` only for local development.

## Project layout

```text
api/index.py                 Vercel Flask entry point
app.py                       Single Flask app factory and CLI commands
auto_reader.py               Existing UIDAI ECMP parser
category_rates.json          Existing category/rate configuration
config.py, extensions.py     Application configuration and extensions
models.py                    Users, reports, metadata, income, rates and audit models
routes.py                    Authentication, reports, admin and monthly-email routes
services/                    Income calculation, email and object storage
templates/                   Responsive Bootstrap pages
static/css, static/js        Application styling, interactions and charts
tests/                       Focused parser, auth and report workflow tests
vercel.json                  Serverless routing and scheduled invocation
```
