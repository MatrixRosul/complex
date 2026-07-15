#!/bin/bash
# =============================================================================
# backup.sh {full|diff}   —  base backup у R2.  Запускається cron'ом від postgres.
#
# WAL їде окремо і безперервно (archive_command, archive_timeout=300s) — звідси
# RPO ≈ 5 хв. Цей скрипт робить лише опорні точки:
#   full — щонеділі, diff — решту днів.
#
# Успіх → пінг у Healthchecks.io (dead-man's switch: якщо пінга не було довше
# grace-періоду, алерт прилітає САМ, навіть якщо цей контейнер помер мовчки —
# а мовчазна смерть cron'а і є найчастіший реальний збій бекап-систем).
# =============================================================================
set -uo pipefail

# shellcheck source=/dev/null
[[ -r /var/lib/pgbackrest-state/env.sh ]] && source /var/lib/pgbackrest-state/env.sh

TYPE="${1:-diff}"
STANZA="${PGBACKREST_STANZA:-nisa}"
STATE_DIR=/var/lib/pgbackrest-state
STARTED_AT=$(date +%s)

echo "=== backup.sh: ${TYPE} backup, станза ${STANZA}, $(date -Iseconds) ==="

hc_ping() {  # $1 = суфікс (порожній | /fail | /start)
    [[ -n "${HEALTHCHECKS_BACKUP_URL:-}" ]] || return 0
    curl -fsS --max-time 10 --retry 3 "${HEALTHCHECKS_BACKUP_URL}${1}" >/dev/null 2>&1 || true
}

hc_ping "/start"

if pgbackrest --stanza="$STANZA" --type="$TYPE" backup; then
    ELAPSED=$(( $(date +%s) - STARTED_AT ))

    # Розмір репозиторію — щоб зростання не стало сюрпризом у рахунку за R2.
    SIZE=$(pgbackrest --stanza="$STANZA" info --output=json 2>/dev/null \
        | jq -r '.[0].backup[-1].info.repository.delta // 0' \
        | awk '{printf "%.1f MiB", $1/1048576}')

    date -Iseconds > "${STATE_DIR}/last_success"
    echo "$TYPE" > "${STATE_DIR}/last_type"

    echo "=== backup.sh: OK за ${ELAPSED}с, у репозиторій долито ${SIZE} ==="
    hc_ping ""
    exit 0
else
    RC=$?
    echo "=== backup.sh: ПРОВАЛ (код ${RC}) ===" >&2

    hc_ping "/fail"
    /usr/local/bin/notify.sh fail "Бекап (${TYPE}) ПРОВАЛИВСЯ, код ${RC}.
Логи: docker compose logs backup   /   /var/log/pgbackrest/
Поки бекап не полагоджено — RPO необмежене."
    exit "$RC"
fi
