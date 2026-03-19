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
CONTAINER="job_scraper"
BACKUP_DIR="./backups"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

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
    info "Применение миграций..."
    alembic upgrade head
    fi
    ;;

  start)
    info "Сборка и запуск контейнера..."
    $COMPOSE up -d --build
    info "Бот запущен. Логи: ./deploy.sh logs"
    ;;

  stop)
    info "Остановка контейнера..."
    $COMPOSE down
    info "Контейнер остановлен."
    ;;

  restart)
    info "Перезапуск контейнера..."
    $COMPOSE restart $CONTAINER
    info "Контейнер перезапущен."
    ;;

  update)
    info "Обновление кода..."
    git pull
    info "Пересборка образа..."
    $COMPOSE up -d --build --force-recreate
    info "Бот обновлён и перезапущен."
    ;;

  logs)
    $COMPOSE logs -f $CONTAINER
    ;;

  status)
    $COMPOSE ps $CONTAINER
    echo ""
    info "Использование ресурсов:"
    docker stats $CONTAINER --no-stream 2>/dev/null || warn "Контейнер не запущен."
    ;;

  help|*)
    echo ""
    echo "Использование: ./deploy.sh <команда>"
    echo ""
    echo "  setup    — первый запуск: создать .env и сгенерировать ключи"
    echo "  start    — запустить бота (собрать образ если нужно)"
    echo "  stop     — остановить бота"
    echo "  restart  — перезапустить без пересборки"
    echo "  update   — git pull + пересборка + перезапуск"
    echo "  logs     — показать логи в реальном времени"
    echo "  status   — статус контейнера и использование ресурсов"
    echo ""
    ;;
esac
