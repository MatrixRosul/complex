#!/bin/bash
# =============================================================================
# notify.sh <рівень> <текст>   —  алерт у Telegram (ARCHITECTURE ADR-019).
#
# Менеджер і так у телефоні, тому Telegram — основний канал. Email — резервний.
# Скрипт НІКОЛИ не повертає ненульовий код: збій телеграма не має валити бекап.
# =============================================================================
set -uo pipefail

LEVEL="${1:-info}"
TEXT="${2:-}"

TOKEN=""
if [[ -n "${TELEGRAM_BOT_TOKEN_FILE:-}" && -r "${TELEGRAM_BOT_TOKEN_FILE}" ]]; then
    TOKEN="$(tr -d '\r\n' < "${TELEGRAM_BOT_TOKEN_FILE}")"
fi

echo "[$(date -Iseconds)] [${LEVEL}] ${TEXT}"

if [[ -z "$TOKEN" || -z "${TELEGRAM_CHAT_ID:-}" ]]; then
    # Телеграм не налаштований — не привід шуміти в лог кожні 5 хвилин.
    exit 0
fi

case "$LEVEL" in
    ok)    ICON="✅" ;;
    warn)  ICON="⚠️" ;;
    fail)  ICON="🔴" ;;
    *)     ICON="ℹ️" ;;
esac

curl -fsS --max-time 10 \
    -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
    -d "chat_id=${TELEGRAM_CHAT_ID}" \
    -d "text=${ICON} Complex · бекап · $(hostname)
${TEXT}" \
    >/dev/null 2>&1 || echo "notify: не вдалось відправити в Telegram (ігноруємо)"

exit 0
