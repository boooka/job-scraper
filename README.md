# Job Scraper

Periodic scraper for Lithuanian job boards — **cvbankas.lt**, **cvonline.lt**,
**cvmarket.lt**, **cv.lt** — with change tracking, RU translation, a Telegram
search bot, a normalized city dictionary, a Django admin panel, and daily
health reports to admins.

## Stack


| Layer              | Technology                                          |
| ------------------ | --------------------------------------------------- |
| Browser automation | Playwright (Chromium)                               |
| Async runtime      | asyncio                                             |
| Scheduler          | APScheduler 3 (cron)                                |
| ORM                | SQLAlchemy 2 (async)                                |
| Database           | PostgreSQL 16                                       |
| Migrations         | Alembic                                             |
| Validation         | Pydantic v2                                         |
| Translation        | DeepL API (+ local cache)                           |
| Telegram bot       | aiogram 3                                           |
| Admin panel        | Django 5 (unmanaged models over the Alembic schema) |
| Retries            | Tenacity                                            |
| Logging            | structlog (JSON)                                    |
| Packaging          | Poetry                                              |
| Lint / format      | Ruff + Black (enforced via pre-commit)              |




## What it does

1. **Scrapes** four job boards on a cron schedule (Playwright).
2. **Upserts** vacancies, resolving each to a **company** and a normalized
  **city** (so "Вильнюс" and "Vilnius" are one place).
3. **Tracks changes** field-by-field and **deactivates** vacancies that vanish
  from a source.
4. **Translates** new vacancies to Russian via DeepL (cached).
5. **Serves a Telegram bot**: full-text search (RU + original), filters,
  subscriptions with de-duplicated delivery.
6. **Notifies admins**: a daily health report plus alerts on new users and new
  subscribers.
7. **Exposes a Django admin** for browsing/editing the data.



## Quick Start



### 1. Clone & configure

```bash
git clone <repo>
cd job-scraper
cp .env.example .env
# edit .env — DATABASE_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_USERNAMES,
#             DEEPL_API_KEY, schedules
```



### 2. Run with Docker Compose

```bash
# Start DB + run migrations + scheduler + bot + admin
docker compose up -d

# Watch logs
docker compose logs -f scraper     # scheduler / scrapers
docker compose logs -f bot         # Telegram bot
docker compose logs -f admin       # Django admin

# Django admin → http://localhost:8000/admin/
docker compose exec admin python manage.py createsuperuser

# pgAdmin (dev profile) → http://localhost:5050  (admin@local.dev / admin)
docker compose --profile dev up -d pgadmin
```

> In development, `docker-compose.override.yml` mounts the source into the
> `scraper`, `bot`, `migrate` and `admin` containers, so code changes apply on
> `docker compose up -d <service>` without an image rebuild.



### 3. Local development (without Docker)

```bash
poetry install
playwright install chromium
pre-commit install                 # enable the lint gate (see Development)

poetry run alembic upgrade head    # migrations
poetry run python -m src.main scrape cvbankas   # one-off scrape
poetry run python -m src.main scheduler         # run the scheduler
```



## CLI (`python -m src.main <command>`)


| Command                                      | Purpose                                                                        |
| -------------------------------------------- | ------------------------------------------------------------------------------ |
| `scheduler`                                  | Run APScheduler with all scrape/translate/notify jobs (default)                |
| `scrape [cvbankas|cvonline|cvmarket|cv|all]` | One-off scrape of a source                                                     |
| `bot`                                        | Run the Telegram bot (long-polling)                                            |
| `migrate`                                    | Create tables from ORM metadata (dev; prefer Alembic in prod)                  |
| `backfill-cities`                            | Resolve every vacancy's raw location into the `cities` dictionary (idempotent) |
| `backfill-company-groups`                    | Group companies into canonical `company_groups` (idempotent)                   |
| `daily-report`                               | Build and send the daily admin health report now                               |




## Project Structure

```
src/
├── main.py                     # Entrypoint / CLI dispatch
├── config.py                   # Settings (pydantic-settings)
├── logger.py                   # structlog setup
├── scheduler.py                # APScheduler job definitions
├── scrapers/
│   ├── base.py                 # BaseScraper: Playwright lifecycle, retry, parse_salary
│   ├── cvbankas.py  cvonline.py  cvmarket.py  cv.py
├── db/
│   ├── engine.py               # Async engine + session factory
│   └── repository.py           # Vacancy/Company/City/Translation/Telegram/Stats repos
├── models/
│   ├── orm.py                  # SQLAlchemy models (Vacancy, Company, City, …)
│   └── schemas.py              # Pydantic VacancyData
└── services/
    ├── scrape_service.py       # Orchestration: scrape → upsert → deactivate → translate
    ├── city_normalizer.py      # LT/RU/EN location → canonical city
    ├── city_backfill.py        # One-off city backfill
    ├── translation_service.py  # DeepL translation catch-up
    ├── deepl_client.py         # DeepL API client
    ├── search_query.py         # Search query parser (include/exclude/fuzzy/regex)
    ├── subscription_notifier.py# Push new matches to subscribers
    ├── admin_notifier.py       # Daily report + notify_admins
    ├── telegram_bot.py         # aiogram bot (search, subscriptions, admin)
    ├── telegram_messages.py    # Bot message templates
    ├── metrics.py / metrics_exporter.py
admin_panel/                    # Django admin (separate service / Dockerfile.django)
├── config/                     # settings, urls, wsgi
└── core/                       # unmanaged models + ModelAdmin registrations
migrations/versions/            # Alembic revisions (0001 … 0007_cities)
tests/                          # pytest (async, sqlite in-memory)
```



## Data Model Highlights

- **Vacancy** — scraped listing. Keeps the raw `location` string plus a FK to a
normalized `city`; `display_location` prefers the city's translation.
- **Company** — deduped per `(source, external_id)` (synthetic id from name when
the site has none). Linked to a canonical **CompanyGroup**.
- **CompanyGroup** — canonical company across sources. Per-source `Company` rows
resolve to one group by a normalized name key (strips legal forms/quotes/case/
diacritics), so "UAB „Biuro“" / "Biuro, UAB" from different boards unify. See
[company_normalizer.py](src/services/company_normalizer.py); grouping is
automatic but correctable in the admin. `Vacancy.display_company` shows the
canonical group name.
- **City** — canonical `name_en` (always set) + optional `name_translated` (RU).
See [city_normalizer.py](src/services/city_normalizer.py); different
spellings resolve to one row (diacritic- and case-insensitive).
- **VacancyTranslation / TranslationCache** — RU translations and a canonical
text cache.
- **ScrapeRun / VacancyChange** — run log and field-level audit trail.



### Integrity guard

`VacancyRepository.upsert_vacancy` **skips** items with a blank `external_id` or
a root-like URL (`https://host` with no path). Both are signatures of a parser
regression that would otherwise collapse many distinct vacancies into one row
(matched by `(source, external_id)`) and rewrite it every scrape. Skipped items
are logged and counted (`skipped`) in the run summary, and never overwrite an
already-good row.

## City Normalization

Sources spell places differently (cvbankas/cvonline in Russian, cv/cvmarket in
Lithuanian). `normalize_city()` maps every known spelling to one canonical
`(name_en, name_translated)`. On upsert the vacancy gets a `city_id`; search and
the top-location buttons work across languages and are deduped.

Backfill existing rows once:

```bash
python -m src.main backfill-cities
```

## Company Unification

Per-source `Company` rows are grouped into a canonical **CompanyGroup** by a
normalized name key (`company_normalizer.normalize_company_name`: casefold +
deaccent, strips legal forms UAB/AB/MB/SIA/OÜ/filialas/ООО…). Resolved on upsert;
`display_company` and the bot's company search/filter use the group, so the same
company appears once across boards and search is case/diacritic-insensitive.

Backfill existing rows once:

```bash
python -m src.main backfill-company-groups
```



## Change Tracking

Each scrape: inserts new vacancies (`first_seen_at`), diffs tracked fields
(`title`, `company_name`, `location`, `salary_*`, `description`) writing a `vacancy_changes` row per change, and deactivates
vacancies absent from the run.

```sql
-- All changes for a vacancy
SELECT * FROM vacancy_changes WHERE vacancy_id = '<uuid>' ORDER BY changed_at DESC;

-- Latest scrape run per source
SELECT * FROM v_latest_scrape_runs;
```



## Telegram Bot

- Full-text search over original text **and** RU translation
(`include`, `-exclude`, `~fuzzy`, and admin-only `regex`).
- Context filters (🧩 Фильтры): query, location (normalized), **company**
(top list or free text; case/diacritic-insensitive), date range
(today / 3 / 7 / 14 / 30 days / any), salary range, auto-search.
- Result cards show title, canonical company, location, **salary** (when
present) and the listing's **last-updated date**, with "Открыть вакансию" and
"Подписаться на этот запрос" buttons.
- Subscriptions with de-duplicated delivery of new matches; "📋 Мои подписки"
lists each with current-offers / unsubscribe buttons.
- Full button reference is in `/help` (admin commands shown only to admins).
- Admin: `/admin_stats` (delivery + metrics), `🛠 Админ` menu.



### Admin notifications

- **Daily health report** (`daily_report` job, `SCHEDULE_DAILY_REPORT`):
vacancies added per source & total, translated per period & total, overall
totals, and per-source scraper health. Alerts if **no** new vacancies appeared
in `DAILY_REPORT_STALE_HOURS`.
- **New user** and **new subscriber** alerts (toggle with
`ADMIN_NOTIFY_NEW_USERS` / `ADMIN_NOTIFY_NEW_SUBSCRIPTIONS`).

Recipients are resolved from `TELEGRAM_ADMIN_USERNAMES` (their last chat id).

## Django Admin

A separate `admin` compose service (`Dockerfile.django`, no Playwright) serves
Django admin at `http://localhost:8000/admin/`. Models are **unmanaged** — they
reflect the Alembic-owned schema, so Alembic stays the single source of truth
for those tables. Django manages only its own auth/session tables.

```bash
docker compose up -d admin
docker compose exec admin python manage.py createsuperuser
```

**Schedules** — the four scrapers plus translation/notification/report jobs have
their cron expressions in the `schedules` table, editable in the admin. The
scheduler seeds it from `.env` on first start, then reloads once a minute:
changed crons reschedule live, jobs enable/disable, and the **Run now** action
fires a job out of schedule — all without restarting the `scraper` container.

**In production the admin is hardened** (see [Secure remote deploy](#secure-remote-deploy-single-vps)):
it lives under a secret URL path (`DJANGO_ADMIN_PATH`), not `/admin`, and sits
behind Caddy HTTP Basic Auth on top of the Django login.



## Configuration (`.env`)

Key settings (see `[.env.example](.env.example)` for the full list):

```
DATABASE_URL=postgresql+asyncpg://scraper:scraper_pass@localhost:5432/job_scraper

# Schedules (5-field cron)
SCHEDULE_CVBANKAS=0 */12 * * *
SCHEDULE_CVONLINE=30 */6 * * *
SCHEDULE_CVMARKET=45 */6 * * *
SCHEDULE_CV=15 */4 * * *
SCHEDULE_TRANSLATIONS=*/15 * * * *
SCHEDULE_SUBSCRIPTION_NOTIFICATIONS=*/10 * * * *
SCHEDULE_DAILY_REPORT=0 9 * * *

# DeepL — one or more keys, comma-separated. The client rotates on quota
# (HTTP 456); translations stop only when ALL keys are spent, at which point
# the `translations` schedule is auto-disabled and the daily report warns.
DEEPL_API_KEY=key1:fx,key2:fx
DEEPL_TARGET_LANG=RU

# Telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_USERNAMES=admin1,admin2
ADMIN_NOTIFY_NEW_USERS=true
ADMIN_NOTIFY_NEW_SUBSCRIPTIONS=true
DAILY_REPORT_STALE_HOURS=24

# Django admin
DJANGO_SECRET_KEY=change-me
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=*

# Production-only (docker-compose.prod.yml + Caddy)
ADMIN_DOMAIN=joblt.example.com        # domain Caddy issues a TLS cert for
DJANGO_ADMIN_PATH=some-secret-path    # admin URL instead of /admin
ADMIN_BASIC_USER=admin                # Caddy Basic Auth user
ADMIN_BASIC_HASH=$$2a$$14$$...        # bcrypt hash — double every $ to $$
```



## Development



### Lint gate (pre-commit)

Ruff is the linter (config in `pyproject.toml`); Black owns line length, so Ruff
`E501` is disabled. A pre-commit hook **blocks commits** on any lint error.

```bash
pre-commit install                 # once per clone
pre-commit run --all-files         # run manually on everything
poetry run ruff check .            # lint
poetry run ruff check . --fix      # lint + autofix
poetry run black .                 # format
```



### Tests

```bash
poetry run pytest                  # async tests on in-memory sqlite
```



### Migrations

```bash
poetry run alembic revision -m "..."   # new revision (edit by hand)
poetry run alembic upgrade head
```



## CI/CD (GitHub Actions)


| Stage    | Trigger        | Action              |
| -------- | -------------- | ------------------- |
| `lint`   | every push/PR  | ruff + black + mypy |
| `test`   | after lint     | pytest + coverage   |
| `docker` | push to `main` | build & push image  |
| `deploy` | after docker   | SSH deploy          |




## Production Operations

```bash
# Pull, rebuild, restart
git pull && docker compose build && docker compose up -d --remove-orphans

# Migrations
docker compose run --rm migrate

# Restart a single service
docker compose restart bot
docker compose restart scraper

# Backup / restore
docker compose exec postgres pg_dump -U scraper -d job_scraper > backup.sql
cat backup.sql | docker compose exec -T postgres psql -U scraper -d job_scraper
```

### Secure remote deploy (single VPS)

`docker-compose.prod.yml` targets a single VPS (e.g. Hetzner CX23): Postgres is
not exposed, containers have memory limits and log rotation, and a **Caddy**
service terminates TLS (auto Let's Encrypt for `ADMIN_DOMAIN`) in front of the
admin. Two layers protect the admin:

1. **Secret URL path** — `DJANGO_ADMIN_PATH` replaces `/admin`; `/admin` and `/`
   return 404 with no redirect leaking the real path.
2. **Caddy HTTP Basic Auth** — a second factor before the Django login. Generate
   the hash with the `$` already doubled (compose would otherwise expand `$…` in
   the hash and blank it):

   ```bash
   docker run --rm caddy:2-alpine caddy hash-password --plaintext 'пароль' | sed 's/\$/\$\$/g'
   ```

   Put `ADMIN_BASIC_USER` / `ADMIN_BASIC_HASH` in `.env` with every `$` in the
   hash doubled to `$$` (e.g. `ADMIN_BASIC_HASH=$$2a$$14$$…`).

All server ops run **from your machine** via `deploy/deploy-remote.sh` (config in
`deploy/remote.conf.local`) — no server shell needed:

```bash
./deploy/deploy-remote.sh push              # rsync code + rebuild + migrate + restart
./deploy/deploy-remote.sh migrate           # apply migrations
./deploy/deploy-remote.sh createsuperuser   # interactive, over SSH
./deploy/deploy-remote.sh changepassword <user>
./deploy/deploy-remote.sh manage <cmd> …    # any manage.py command
./deploy/deploy-remote.sh logs | status | backup | restore-db [file]
```

> Shell scripts and configs are pinned to LF via `.gitattributes` — a CRLF
> checkout on Windows would otherwise break the shebang on the server
> (`env: bash\r`).



## Adding a New Source

1. Create `src/scrapers/mysource.py` extending `BaseScraper`.
2. Set `source = "mysource"` and implement `scrape_all()` as an async generator
  of `VacancyData` (always yield a non-empty `external_id` and a full URL —
   the integrity guard drops the rest).
3. Register a job in `src/scheduler.py` and a `SCHEDULE_MYSOURCE` in
  `config.py` / `.env.example`.
4. Add selectors/quirks notes and a test.

