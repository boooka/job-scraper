#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Деплой на удалённый сервер (например, deploy_user@deploy.example.com:2222)
# Требуется: ssh, rsync (для push). На сервере: docker, docker compose.
#
# Использование (из корня проекта или из deploy/):
#   ./deploy/deploy-remote.sh push     — git push + git pull на сервере + пересборка
#   ./deploy/deploy-remote.sh setup    — первый раз на сервере: создать каталог, .env, запустить
#   ./deploy/deploy-remote.sh start    — запустить контейнер на сервере
#   ./deploy/deploy-remote.sh stop     — остановить на сервере
#   ./deploy/deploy-remote.sh logs     — логи с сервера
#   ./deploy/deploy-remote.sh status   — статус на сервере
#   ./deploy/deploy-remote.sh backup   — бэкап БД на сервере
#   ./deploy/deploy-remote.sh migrate  — применить миграции на сервере
#   ./deploy/deploy-remote.sh createsuperuser        — создать админа (интерактивно)
#   ./deploy/deploy-remote.sh changepassword <user>  — сменить пароль админа
#   ./deploy/deploy-remote.sh manage <cmd> [args...]  — любая manage.py-команда
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
# -t allocates a TTY for interactive remote commands (createsuperuser, shell).
ssh_tty() { ssh -t "${SSH_OPTS[@]}" "$REMOTE" "$@"; }
scp_from_remote() { scp "${SCP_OPTS[@]}" "$REMOTE:$1" "$2"; }
scp_to_remote() { scp "${SCP_OPTS[@]}" "$1" "$REMOTE:$2"; }

# Compose file used on the server (prod by default; override via remote.conf).
COMPOSE_FILE_REMOTE="${COMPOSE_FILE:-docker-compose.prod.yml}"
DC="docker compose -f $COMPOSE_FILE_REMOTE"

cmd="${1:-help}"

case "$cmd" in
  push)
    # Git-based deploy: push the current branch to origin, then the server
    # pulls it. (No rsync — mixing rsync with the server's `git pull` leaves the
    # working tree dirty and blocks the merge.)
    branch="$(git -C "$PROJECT_ROOT" rev-parse --abbrev-ref HEAD)"
    info "Локальный git push: origin/$branch ..."
    git -C "$PROJECT_ROOT" push origin "$branch"
    info "Деплой на сервере (git pull + автобэкап + миграции + пересборка)..."
    ssh_cmd "cd $REMOTE_DIR && git pull --ff-only origin $branch && ./deploy/deploy-local.sh update"
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
  backup)
    info "Запускаю бэкап на сервере..."
    ssh_cmd "cd $REMOTE_DIR && ./deploy/deploy-local.sh backup"
    mkdir -p "$LOCAL_DIR/backups"
    latest_file="$(ssh_cmd "ls -1t $REMOTE_DIR/backups/job-scraper-*.sql.gz 2>/dev/null | head -n 1 | xargs -n1 basename" || true)"
    latest_env="$(ssh_cmd "ls -1t $REMOTE_DIR/backups/env-*.bak 2>/dev/null | head -n 1 | xargs -n1 basename" || true)"
    if [ -n "${latest_file:-}" ]; then
      info "Скачиваю последний бэкап: $latest_file"
      scp_from_remote "$REMOTE_DIR/backups/$latest_file" "$LOCAL_DIR/backups/$latest_file"
      info "Бэкап сохранен локально в $LOCAL_DIR/backups/$latest_file"
    else
      warn "На сервере не найден SQL-бэкап для скачивания."
    fi
    if [ -n "${latest_env:-}" ]; then
      info "Скачиваю последний бэкап env: $latest_env"
      scp_from_remote "$REMOTE_DIR/backups/$latest_env" "$LOCAL_DIR/backups/$latest_env"
      info "Бэкап env сохранен локально в $LOCAL_DIR/backups/$latest_env"
    else
      warn "На сервере не найден env-бэкап для скачивания."
    fi
    ;;
  restore-db)
    db_file="${2:-}"
    if [ -z "${db_file}" ]; then
      db_file="$(ls -1t "$LOCAL_DIR"/backups/job-scraper-*.sql.gz 2>/dev/null | head -n 1 || true)"
    fi
    [ -n "${db_file:-}" ] || err "Укажите путь к SQL-бэкапу или положите его в ./backups"
    [ -f "$db_file" ] || err "Файл не найден: $db_file"
    remote_name="$(basename "$db_file")"
    info "Загружаю SQL-бэкап на сервер: $remote_name"
    scp_to_remote "$db_file" "$REMOTE_DIR/backups/$remote_name"
    info "Восстанавливаю БД на сервере..."
    ssh_cmd "cd $REMOTE_DIR && ./deploy/deploy-local.sh restore-db ./backups/$remote_name"
    info "Восстановление БД завершено."
    ;;
  restore-env)
    env_file="${2:-}"
    if [ -z "${env_file}" ]; then
      env_file="$(ls -1t "$LOCAL_DIR"/backups/env-*.bak 2>/dev/null | head -n 1 || true)"
    fi
    [ -n "${env_file:-}" ] || err "Укажите путь к env-бэкапу или положите его в ./backups"
    [ -f "$env_file" ] || err "Файл не найден: $env_file"
    remote_name="$(basename "$env_file")"
    info "Загружаю env-бэкап на сервер: $remote_name"
    scp_to_remote "$env_file" "$REMOTE_DIR/backups/$remote_name"
    info "Восстанавливаю .env на сервере..."
    ssh_cmd "cd $REMOTE_DIR && ./deploy/deploy-local.sh restore-env ./backups/$remote_name"
    info "Восстановление .env завершено."
    ;;
  restore-all)
    db_file="${2:-}"
    env_file="${3:-}"
    if [ -z "${db_file}" ]; then
      db_file="$(ls -1t "$LOCAL_DIR"/backups/job-scraper-*.sql.gz 2>/dev/null | head -n 1 || true)"
    fi
    if [ -z "${env_file}" ]; then
      env_file="$(ls -1t "$LOCAL_DIR"/backups/env-*.bak 2>/dev/null | head -n 1 || true)"
    fi
    [ -n "${db_file:-}" ] || err "Не найден SQL-бэкап для restore-all"
    [ -n "${env_file:-}" ] || err "Не найден env-бэкап для restore-all"
    [ -f "$db_file" ] || err "Файл не найден: $db_file"
    [ -f "$env_file" ] || err "Файл не найден: $env_file"
    remote_db="$(basename "$db_file")"
    remote_env="$(basename "$env_file")"
    info "Загружаю бэкапы на сервер..."
    scp_to_remote "$db_file" "$REMOTE_DIR/backups/$remote_db"
    scp_to_remote "$env_file" "$REMOTE_DIR/backups/$remote_env"
    info "Восстанавливаю .env и БД на сервере..."
    ssh_cmd "cd $REMOTE_DIR && ./deploy/deploy-local.sh restore-all ./backups/$remote_env ./backups/$remote_db"
    info "Полное восстановление завершено."
    ;;
  migrate)
    info "Применяю миграции на сервере..."
    ssh_cmd "cd $REMOTE_DIR && $DC run --rm migrate"
    info "Миграции применены."
    ;;
  createsuperuser)
    info "Создание суперпользователя (интерактивно)..."
    ssh_tty "cd $REMOTE_DIR && $DC exec admin python manage.py createsuperuser"
    ;;
  changepassword)
    user="${2:-}"
    [ -n "$user" ] || err "Использование: changepassword <username>"
    ssh_tty "cd $REMOTE_DIR && $DC exec admin python manage.py changepassword '$user'"
    ;;
  manage)
    shift
    [ $# -ge 1 ] || err "Использование: manage <django-команда> [аргументы]"
    ssh_tty "cd $REMOTE_DIR && $DC exec admin python manage.py $*"
    ;;
  ssh)
    ssh_cmd "cd $REMOTE_DIR && exec \$SHELL"
    ;;
  help|*)
    echo ""
    echo "Деплой на $REMOTE (порт ${REMOTE_PORT:-22})"
    echo ""
    echo "  push    — rsync кода на сервер + ./deploy/deploy-local.sh update"
    echo "  setup   — создать каталог, первый rsync, .env из example"
    echo "  start   — на сервере: ./deploy.sh start"
    echo "  stop    — на сервере: ./deploy.sh stop"
    echo "  restart — на сервере: ./deploy.sh restart"
    echo "  logs    — логи контейнера с сервера"
    echo "  status  — статус контейнера на сервере"
    echo "  backup  — бэкап БД на сервере + скачать последний локально"
    echo "  restore-db [file]   — восстановить БД на сервере из локального бэкапа"
    echo "  restore-env [file]  — восстановить .env на сервере из локального бэкапа"
    echo "  restore-all [db] [env] — восстановить БД и .env на сервере"
    echo "  migrate — применить миграции на сервере"
    echo "  createsuperuser        — создать админа (интерактивно)"
    echo "  changepassword <user>  — сменить пароль админа"
    echo "  manage <cmd> [args...]  — любая manage.py-команда на сервере"
    echo "  ssh     — войти по SSH в каталог проекта на сервере"
    echo ""
    ;;
esac
