#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Деплой на удалённый сервер (например, deploy_user@deploy.example.com:2222)
# Требуется: ssh, rsync (для push). На сервере: docker, docker compose.
#
# Использование (из корня проекта или из deploy/):
#   ./deploy/deploy-remote.sh push     — синхронизировать код и перезапустить бота
#   ./deploy/deploy-remote.sh setup    — первый раз на сервере: создать каталог, .env, запустить
#   ./deploy/deploy-remote.sh start    — запустить контейнер на сервере
#   ./deploy/deploy-remote.sh stop     — остановить на сервере
#   ./deploy/deploy-remote.sh logs     — логи с сервера
#   ./deploy/deploy-remote.sh status   — статус на сервере
#   ./deploy/deploy-remote.sh backup   — бэкап БД на сервере
#   ./deploy/deploy-remote.sh ssh      — открыть SSH-сессию в REMOTE_DIR
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Загрузка конфига (remote.conf.local переопределяет remote.conf)
CONF_FILE="$SCRIPT_DIR/remote.conf"
if [ -f "$SCRIPT_DIR/remote.conf.local" ]; then
  CONF_FILE="$SCRIPT_DIR/remote.conf.local"
fi
# shellcheck source=remote.conf
source "$CONF_FILE"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }


SSH_OPTS=(-o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
[ -n "${SSH_KEY:-}" ]    && SSH_OPTS+=(-i "$SSH_KEY")
SCP_OPTS=()
[ -n "${REMOTE_PORT:-}" ] && SSH_OPTS+=(-p "$REMOTE_PORT") && SCP_OPTS+=(-P "$REMOTE_PORT")

REMOTE="${REMOTE_USER}@${REMOTE_HOST}"
ssh_cmd() { ssh "${SSH_OPTS[@]}" "$REMOTE" "$@"; }
scp_cmd() { scp "${SCP_OPTS[@]}" "$REMOTE:$REMOTE_DIR/backups/$@" "$LOCAL_DIR/backups/$@"; }

cmd="${1:-help}"

case "$cmd" in
  push)
    info "Синхронизация кода на $REMOTE:$REMOTE_DIR ..."
    rsync -avz --delete \
      -e "ssh ${SSH_OPTS[*]}" \
      --exclude='.git' \
      --exclude='data' \
      --exclude='.env' \
      --exclude='backups' \
      --exclude='__pycache__' \
      --exclude='*.pyc' \
      --exclude='.venv' \
      --exclude='deploy/remote.conf.local' \
      "$PROJECT_ROOT/" \
      "$REMOTE:$REMOTE_DIR/"
    info "Перезапуск бота на сервере..."
    ssh_cmd "cd $REMOTE_DIR && docker compose up -d --build"
    info "Готово."
    ;;
  setup)
    info "Первоначальная настройка на $REMOTE ..."
    ssh_cmd "mkdir -p $REMOTE_DIR/data $REMOTE_DIR/backups"
    rsync -avz -e "ssh ${SSH_OPTS[*]}" \
      --exclude='.git' --exclude='data' --exclude='backups' --exclude='.venv' \
      --exclude='__pycache__' --exclude='*.pyc' \
      "$PROJECT_ROOT/" \
      "$REMOTE:$REMOTE_DIR/"
    info "Запуск ./deploy/deploy-local.sh setup на сервере (создание .env)..."
    ssh_cmd "cd $REMOTE_DIR && ./deploy/deploy-local.sh setup" || true
    info "Готово. Задайте TELEGRAM_BOT_TOKEN в .env на сервере ($REMOTE_DIR/.env), затем: ./deploy/deploy-remote.sh start"
    ;;
  start)
    ssh_cmd "cd $REMOTE_DIR && ./deploy/deploy-local.sh start"
    ;;
  stop)
    ssh_cmd "cd $REMOTE_DIR && ./deploy/deploy-local.sh stop"
    ;;
  restart)
    ssh_cmd "cd $REMOTE_DIR && ./deploy/deploy-local.sh restart"
    ;;
  logs)
    ssh_cmd "cd $REMOTE_DIR && ./deploy/deploy-local.sh logs"
    ;;
  status)
    ssh_cmd "cd $REMOTE_DIR && ./deploy/deploy-local.sh status"
    ;;
  ssh)
    ssh_cmd "cd $REMOTE_DIR && exec \$SHELL"
    ;;
  help|*)
    echo ""
    echo "Деплой на $REMOTE (порт ${REMOTE_PORT:-22})"
    echo ""
    echo "  push    — rsync кода на сервер + docker compose up -d --build"
    echo "  setup   — создать каталог, первый rsync, .env из example"
    echo "  start   — на сервере: ./deploy.sh start"
    echo "  stop    — на сервере: ./deploy.sh stop"
    echo "  restart — на сервере: ./deploy.sh restart"
    echo "  logs    — логи контейнера с сервера"
    echo "  status  — статус контейнера на сервере"
    echo "  ssh     — войти по SSH в каталог проекта на сервере"
    echo ""
    ;;
esac
