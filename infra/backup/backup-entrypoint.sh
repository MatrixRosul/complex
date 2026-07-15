#!/bin/bash
# =============================================================================
# Entrypoint сервісу `backup`.  Порядок:
#
#   1. рендер конфіга pgBackRest із секретів;
#   2. збереження оточення у файл — cron його вичищає, а скриптам потрібні
#      HEALTHCHECKS_* і TELEGRAM_*;
#   3. stanza-create (ідемпотентно) + `pgbackrest check` — наскрізна перевірка,
#      що archive_command реально доїжджає до R2. Саме тут ловляться «ключ без
#      прав на запис» і «бакет не той», а не через тиждень, коли бекап знадобився;
#   4. якщо в репозиторії ЖОДНОГО бекапу — робимо повний прямо зараз, у фоні.
#      Свіжорозгорнутий прод не має ходити без бекапу до найближчої неділі;
#   5. cron у передньому плані (PID 1 контейнера).
# =============================================================================
set -euo pipefail

STANZA="${PGBACKREST_STANZA:-nisa}"
STATE_DIR=/var/lib/pgbackrest-state
ENV_FILE="${STATE_DIR}/env.sh"

/usr/local/bin/render-repo-config.sh

# --- 2. Оточення для cron-задач -------------------------------------------
mkdir -p "$STATE_DIR"
( umask 077 && cat > "$ENV_FILE" <<EOF
# ЗГЕНЕРОВАНО backup-entrypoint.sh. Cron стартує з порожнім оточенням —
# backup.sh і restore-drill.sh підтягують ці значення звідси.
export PGBACKREST_STANZA="${STANZA}"
export POSTGRES_USER="${POSTGRES_USER:-postgres}"
export POSTGRES_DB="${POSTGRES_DB:-nisa}"
export HEALTHCHECKS_BACKUP_URL="${HEALTHCHECKS_BACKUP_URL:-}"
export HEALTHCHECKS_DRILL_URL="${HEALTHCHECKS_DRILL_URL:-}"
export TELEGRAM_BOT_TOKEN_FILE="${TELEGRAM_BOT_TOKEN_FILE:-}"
export TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"
export PGHOST=/var/run/postgresql
EOF
)
chown postgres:postgres "$ENV_FILE"

# --- 3. Станза + наскрізна перевірка --------------------------------------
echo "backup-entrypoint: stanza-create (ідемпотентно)…"
gosu postgres pgbackrest --stanza="$STANZA" stanza-create || \
    echo "backup-entrypoint: станза вже існує — ок"

echo "backup-entrypoint: pgbackrest check — перевіряємо, що archive_command доїжджає до R2…"
if gosu postgres pgbackrest --stanza="$STANZA" check; then
    echo "backup-entrypoint: check OK"
else
    # Не падаємо: контейнер лишається живим, але healthcheck зробить його
    # unhealthy, а алерт уже пішов. Впасти тут = crash-loop без діагностики.
    /usr/local/bin/notify.sh fail "pgbackrest check ПРОВАЛИВСЯ на старті.
WAL не архівується → бекапів немає. Перевір ключі бакета бекапів і archive_command."
fi

# --- 4. Перший бекап, якщо репозиторій порожній ----------------------------
if ! gosu postgres pgbackrest --stanza="$STANZA" info --output=json 2>/dev/null \
        | jq -e '.[0].backup | length > 0' >/dev/null 2>&1; then
    echo "backup-entrypoint: у репозиторії немає жодного бекапу — робимо повний зараз (фоном)."
    gosu postgres /usr/local/bin/backup.sh full &
fi

# --- 5. Cron ---------------------------------------------------------------
echo "backup-entrypoint: розклад:"
grep -vE '^\s*(#|$)' /etc/cron.d/complex-backup || true

# -f: передній план (PID 1). -L 2: логи cron у stdout, щоб їх бачив `docker logs`.
exec cron -f -L 2
