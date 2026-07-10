#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy.sh — управление ботом на домашнем сервере
# Использование:
#   ./deploy.sh setup   — первый запуск (создать .env, сгенерировать ключ)
#   ./deploy.sh start   — запустить / пересобрать
#   ./deploy.sh stop    — остановить
#   ./deploy.sh restart — перезапустить
#   ./deploy.sh update  — обновить код и пересобрать образ
#   ./deploy.sh logs    — показать логи (Ctrl+C для выхода)
#   ./deploy.sh status  — статус контейнера
#   ./deploy.sh backup  — сделать бэкап базы данных
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

COMPOSE="docker compose"
BOT_SERVICE="bot"
SCRAPER_SERVICE="scraper"
ADMIN_SERVICE="admin"
POSTGRES_SERVICE="postgres"
BACKUP_DIR="./backups"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
ENV_FILE=".env"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

ensure_backup_dir() {
  mkdir -p "$BACKUP_DIR"
}

compose_postgres_running() {
  $COMPOSE ps -q "$POSTGRES_SERVICE" >/dev/null 2>&1 && [ -n "$($COMPOSE ps -q "$POSTGRES_SERVICE" 2>/dev/null)" ]
}

load_db_env() {
  DB_NAME="${POSTGRES_DB:-job_scraper}"
  DB_USER="${POSTGRES_USER:-scraper}"
  DB_PASSWORD="${POSTGRES_PASSWORD:-scraper_pass}"
  if [ -f "$ENV_FILE" ]; then
    get_env_value() {
      local key="$1"
      local val
      val="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d'=' -f2- || true)"
      val="${val%\"}"
      val="${val#\"}"
      val="${val%\'}"
      val="${val#\'}"
      echo "$val"
    }
    DB_NAME="$(get_env_value "POSTGRES_DB" || true)"
    DB_USER="$(get_env_value "POSTGRES_USER" || true)"
    DB_PASSWORD="$(get_env_value "POSTGRES_PASSWORD" || true)"
    DB_NAME="${DB_NAME:-job_scraper}"
    DB_USER="${DB_USER:-scraper}"
    DB_PASSWORD="${DB_PASSWORD:-scraper_pass}"
  fi
}

backup_db() {
  ensure_backup_dir
  load_db_env
  local out_file="${BACKUP_DIR}/job-scraper-${TIMESTAMP}.sql.gz"
  if ! compose_postgres_running; then
    warn "Postgres контейнер не запущен, пропускаю бэкап БД."
    return 0
  fi
  info "Создаю бэкап БД в ${out_file} ..."
  if $COMPOSE exec -T \
    -e PGPASSWORD="$DB_PASSWORD" \
    "$POSTGRES_SERVICE" \
    sh -lc "pg_dump -U '$DB_USER' -d '$DB_NAME'" | gzip > "$out_file"; then
    info "Бэкап БД создан: $out_file"
  else
    rm -f "$out_file"
    error "Не удалось создать бэкап БД."
  fi
}

backup_env() {
  ensure_backup_dir
  if [ -f "$ENV_FILE" ]; then
    cp "$ENV_FILE" "${BACKUP_DIR}/env-${TIMESTAMP}.bak"
    info "Бэкап .env создан: ${BACKUP_DIR}/env-${TIMESTAMP}.bak"
  else
    warn ".env не найден, бэкап .env пропущен."
  fi
}

backup_before_migrate() {
  info "Автобэкап перед миграциями..."
  backup_env
  backup_db
}

latest_db_backup() {
  ls -1t "${BACKUP_DIR}"/job-scraper-*.sql.gz 2>/dev/null | head -n 1 || true
}

latest_env_backup() {
  ls -1t "${BACKUP_DIR}"/env-*.bak 2>/dev/null | head -n 1 || true
}

apply_migrations() {
  info "Применение миграций..."
  $COMPOSE run --rm migrate
}

restore_db() {
  local backup_file="${1:-}"
  if [ -z "$backup_file" ]; then
    backup_file="$(latest_db_backup)"
  fi
  [ -n "$backup_file" ] || error "Не найден файл бэкапа БД."
  [ -f "$backup_file" ] || error "Файл бэкапа БД не существует: $backup_file"

  load_db_env
  info "Восстановление БД из: $backup_file"
  $COMPOSE up -d "$POSTGRES_SERVICE"
  gzip -dc "$backup_file" | $COMPOSE exec -T \
    -e PGPASSWORD="$DB_PASSWORD" \
    "$POSTGRES_SERVICE" \
    sh -lc "psql -U '$DB_USER' -d '$DB_NAME' -v ON_ERROR_STOP=1"
  info "Восстановление БД завершено."
}

restore_env() {
  local env_backup="${1:-}"
  if [ -z "$env_backup" ]; then
    env_backup="$(latest_env_backup)"
  fi
  [ -n "$env_backup" ] || error "Не найден файл бэкапа .env."
  [ -f "$env_backup" ] || error "Файл бэкапа .env не существует: $env_backup"
  cp "$env_backup" "$ENV_FILE"
  info ".env восстановлен из: $env_backup"
}

cmd="${1:-help}"

case "$cmd" in

  setup)
    info "Первоначальная настройка..."
    if [ ! -f .env ]; then
      cp .env.example .env
      # replace placeholder in .env
      info "Файл .env создан."
      warn "Укажите TELEGRAM_BOT_TOKEN в файле .env и запустите: ./deploy.sh start"
    else
      warn ".env уже существует, пропускаю."
    fi
    info "Запускаю postgres для подготовки миграций..."
    $COMPOSE up -d "$POSTGRES_SERVICE"
    backup_before_migrate
    apply_migrations
    info "Setup завершен. Запустите: ./deploy/deploy-local.sh start"
    ;;

  start)
    info "Запускаю postgres для подготовки миграций..."
    $COMPOSE up -d "$POSTGRES_SERVICE"
    backup_before_migrate
    apply_migrations
    info "Сборка и запуск контейнеров..."
    $COMPOSE up -d --build "$SCRAPER_SERVICE" "$BOT_SERVICE" "$ADMIN_SERVICE"
    info "Бот, скрапер и админка запущены. Логи: ./deploy.sh logs"
    ;;

  stop)
    info "Остановка контейнера..."
    $COMPOSE down
    info "Контейнер остановлен."
    ;;

  restart)
    info "Перезапуск контейнеров..."
    $COMPOSE restart "$SCRAPER_SERVICE" "$BOT_SERVICE" "$ADMIN_SERVICE"
    info "Контейнеры перезапущены."
    ;;

  update)
    info "Обновление кода..."
    git pull
    info "Запускаю postgres для подготовки миграций..."
    $COMPOSE up -d "$POSTGRES_SERVICE"
    backup_before_migrate
    apply_migrations
    info "Пересборка образа..."
    $COMPOSE up -d --build --force-recreate "$SCRAPER_SERVICE" "$BOT_SERVICE" "$ADMIN_SERVICE"
    info "Бот, скрапер и админка обновлены и перезапущены."
    ;;

  logs)
    $COMPOSE logs -f "$BOT_SERVICE" "$SCRAPER_SERVICE"
    ;;

  status)
    $COMPOSE ps "$BOT_SERVICE" "$SCRAPER_SERVICE" "$ADMIN_SERVICE" "$POSTGRES_SERVICE"
    echo ""
    info "Использование ресурсов:"
    docker stats job_scraper_bot job_scraper_app job_scraper_admin job_scraper_db --no-stream 2>/dev/null || warn "Контейнеры не запущены."
    ;;

  backup)
    backup_env
    backup_db
    info "Ручной бэкап завершен."
    ;;

  restore-db)
    restore_db "${2:-}"
    ;;

  restore-env)
    restore_env "${2:-}"
    ;;

  restore-all)
    restore_env "${2:-}"
    restore_db "${3:-}"
    info "Восстановление .env и БД завершено."
    ;;

  help|*)
    echo ""
    echo "Использование: ./deploy.sh <команда>"
    echo ""
    echo "  setup    — первый запуск: создать .env, автобэкап, миграции"
    echo "  start    — автобэкап + миграции + запуск бота/скрапера"
    echo "  stop     — остановить бота"
    echo "  restart  — перезапустить без пересборки"
    echo "  update   — git pull + автобэкап + миграции + пересборка"
    echo "  logs     — показать логи в реальном времени"
    echo "  status   — статус контейнера и использование ресурсов"
    echo "  backup   — ручной бэкап .env и БД"
    echo "  restore-db [file]        — восстановить БД из бэкапа (или последнего)"
    echo "  restore-env [file]       — восстановить .env из бэкапа (или последнего)"
    echo "  restore-all [env] [db]   — восстановить и .env, и БД (или последние)"
    echo ""
    ;;
esac
