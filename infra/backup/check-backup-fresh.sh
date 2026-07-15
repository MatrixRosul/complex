#!/bin/bash
# =============================================================================
# HEALTHCHECK сервісу `backup`.
#
# Здоров'я бекап-сервісу — це НЕ «процес cron живий». Живий cron, який три тижні
# нічого не забекапив, здоровим не є. Тому перевіряємо факт: коли востаннє в
# репозиторії з'явився успішний бекап.
#
#   свіжіше 36 год  → healthy   (добовий diff + запас на повільну ніч)
#   старше          → unhealthy → видно в `docker compose ps`, ловиться
#                                  зовнішнім моніторингом
# =============================================================================
set -uo pipefail

# shellcheck source=/dev/null
[[ -r /var/lib/pgbackrest-state/env.sh ]] && source /var/lib/pgbackrest-state/env.sh

STANZA="${PGBACKREST_STANZA:-nisa}"
MAX_AGE_SEC=$(( 36 * 3600 ))

INFO=$(gosu postgres pgbackrest --stanza="$STANZA" info --output=json 2>/dev/null) || {
    echo "unhealthy: pgbackrest info не відповідає (репозиторій недоступний?)"
    exit 1
}

# .backup[-1] — найсвіжіший бекап у станзі; timestamp.stop — коли він завершився.
LAST_STOP=$(echo "$INFO" | jq -r '.[0].backup[-1].timestamp.stop // empty')

if [[ -z "$LAST_STOP" ]]; then
    echo "unhealthy: у репозиторії НЕМАЄ ЖОДНОГО бекапу"
    exit 1
fi

AGE=$(( $(date +%s) - LAST_STOP ))

if (( AGE > MAX_AGE_SEC )); then
    echo "unhealthy: останній бекап $(( AGE / 3600 )) год тому (ліміт 36 год)"
    exit 1
fi

echo "healthy: останній бекап $(( AGE / 3600 )) год тому"
exit 0
