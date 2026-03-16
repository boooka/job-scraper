# Job Scraper

Periodic scraper for Lithuanian job boards: **cvbankas.lt**, **cvonline.lt**, **cvmarket.lt**.

## Stack

| Layer | Technology |
|-------|-----------|
| Browser automation | Playwright (Chromium) |
| Async runtime | asyncio |
| Scheduler | APScheduler 3 (cron) |
| ORM | SQLAlchemy 2 async |
| Database | PostgreSQL 16 |
| Migrations | Alembic |
| Validation | Pydantic v2 |
| Retries | Tenacity |
| Logging | structlog (JSON) |
| Packaging | Poetry |

## Quick Start

### 1. Clone & configure

```bash
git clone <repo>
cd job-scraper
cp .env.example .env
# edit .env — set DATABASE_URL if needed
```

### 2. Run with Docker Compose

```bash
# Start DB + run migrations + start scheduler
docker compose up -d

# Watch logs
docker compose logs -f scraper

# Run a one-off scrape (dev override)
docker compose -f docker-compose.yml -f docker-compose.override.yml up scraper

# Open pgAdmin (dev)
docker compose --profile dev up pgadmin
# → http://localhost:5050  (admin@local.dev / admin)
```

### 3. Local development (without Docker)

```bash
# Install deps
poetry install
playwright install chromium

# Run migrations
poetry run alembic upgrade head

# One-off scrape
poetry run python -m src.main scrape cvbankas

# Start scheduler
poetry run python -m src.main scheduler
```

## Project Structure

```
src/
├── main.py               # Entrypoint (scheduler | scrape | migrate)
├── config.py             # Settings via pydantic-settings
├── logger.py             # structlog setup
├── scheduler.py          # APScheduler job definitions
├── scrapers/
│   ├── base.py           # BaseScraper (Playwright lifecycle, retry)
│   ├── cvbankas.py
│   ├── cvonline.py
│   └── cvmarket.py
├── db/
│   ├── engine.py         # Async engine + session factory
│   └── repository.py     # VacancyRepository, ScrapeRunRepository
├── models/
│   ├── orm.py            # SQLAlchemy models
│   └── schemas.py        # Pydantic VacancyData
└── services/
    └── scrape_service.py  # Orchestration: scrape → upsert → deactivate
migrations/
    versions/0001_initial_schema.py
tests/
    conftest.py
    test_repository.py
    test_schemas.py
```

## Change Tracking

Every time a vacancy is scraped, the system:

1. **Inserts** new vacancies (`first_seen_at` set)
2. **Detects changes** in tracked fields: `title`, `company`, `location`, `salary_*`, `description`
3. **Writes** a `vacancy_changes` row per changed field with `old_value` / `new_value`
4. **Deactivates** vacancies absent from the latest scrape (`is_active = false`)

### Useful queries

```sql
-- All changes for a vacancy
SELECT * FROM vacancy_changes
WHERE vacancy_id = '<uuid>'
ORDER BY changed_at DESC;

-- Salary changes in last 7 days
SELECT v.title, v.company, vc.old_value, vc.new_value, vc.changed_at
FROM vacancy_changes vc
JOIN vacancies v ON v.id = vc.vacancy_id
WHERE vc.field_name LIKE 'salary%'
  AND vc.changed_at > now() - interval '7 days';

-- Latest scrape run per source
SELECT * FROM v_latest_scrape_runs;
```

## Scheduler Configuration

Cron expressions (5-field) in `.env`:

```
SCHEDULE_CVBANKAS=0 */4 * * *    # every 4 hours
SCHEDULE_CVONLINE=30 */4 * * *   # every 4 hours offset by 30 min
SCHEDULE_CVMARKET=0 1 * * *      # daily at 01:00
```

## CI/CD (GitHub Actions)

| Stage | Trigger | Action |
|-------|---------|--------|
| `lint` | every push/PR | ruff + black + mypy |
| `test` | after lint | pytest + coverage |
| `docker` | push to `main` | build & push to GHCR |
| `deploy` | after docker | SSH deploy to server |

Required GitHub secrets:
- `DEPLOY_HOST` — production server IP
- `DEPLOY_USER` — SSH user
- `DEPLOY_SSH_KEY` — private SSH key

## Adding a New Source

1. Create `src/scrapers/mysource.py` extending `BaseScraper`
2. Set `source = "mysource"` class attribute
3. Implement `scrape_all()` as an async generator of `VacancyData`
4. Add a job in `src/scheduler.py`
5. Add cron setting to `.env.example` and `src/config.py`
