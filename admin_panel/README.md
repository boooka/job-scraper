# Admin Panel (Django)

A lightweight Django admin over the scraper's PostgreSQL database. Runs as its
own compose service (`admin`, built from `../Dockerfile.django` — no Playwright)
and is served at `http://localhost:8000/admin/`.

## Design

- **Unmanaged models** (`core/models.py`, `Meta.managed = False` + explicit
  `db_table`) mirror the Alembic-owned schema. Alembic remains the single source
  of truth for those tables; Django never migrates them.
- Django manages **only its own** tables (auth, sessions, admin log,
  contenttypes) via the standard migration in `core/migrations/`.
- Foreign keys use `db_constraint=False` + `on_delete=DO_NOTHING` because the DB
  already defines the constraints.

## Registered admins (`core/admin.py`)

- **Editable**: `City`, `Company`, `Vacancy` (with translation & change inlines),
  `VacancyTranslation`, `TelegramSubscription`, `TelegramUser`.
- **Read-only** (audit/technical): `ScrapeRun`, `VacancyChange`,
  `TranslationCache`, `TelegramSubscriptionDelivery`.

## Run

```bash
# via compose (recommended)
docker compose up -d admin
docker compose exec admin python manage.py createsuperuser

# or locally (uses psycopg, not asyncpg — DATABASE_URL is converted in settings)
python manage.py migrate      # creates Django's own tables only
python manage.py runserver 0.0.0.0:8000
```

## Configuration

Reads `DATABASE_URL` (the `+asyncpg` suffix is stripped to a sync DSN in
`config/settings.py`), plus `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`,
`DJANGO_ALLOWED_HOSTS`.
