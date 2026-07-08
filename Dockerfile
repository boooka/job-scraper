FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps for Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ── deps layer ────────────────────────────────────────────────────────
FROM base AS deps

RUN pip install poetry==1.8.3
COPY pyproject.toml poetry.lock* ./
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi
RUN pip install --upgrade sshtunnel
# 1. Устанавливаем переменную окружения, чтобы браузеры ставились в общее место
ENV PLAYWRIGHT_BROWSERS_PATH=/usr/local/bin/playwright-browsers

# Install Playwright browsers (Chromium only)
RUN playwright install-deps chromium   
# 2. Устанавливаем сам браузер (под root)
RUN playwright install chromium --with-deps

# 3. Если вы создаете пользователя scraper, дайте ему права на эту папку
RUN chmod -R 777 /usr/local/bin/playwright-browsers

# ── final image ───────────────────────────────────────────────────────
FROM deps AS final

COPY src/ ./src/
COPY migrations/ ./migrations/
COPY alembic.ini ./

# Non-root user
RUN useradd -m -u 1000 scraper && chown -R scraper:scraper /app
USER scraper

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import asyncio; from src.db.engine import check_connection; asyncio.run(check_connection())" || exit 1

CMD ["python", "-m", "src.main", "scheduler"]
