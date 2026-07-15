#!/bin/bash
# =============================================================================
# restore-drill.sh — ЩОТИЖНЕВА АВТОМАТИЧНА ПЕРЕВІРКА ВІДНОВЛЮВАНОСТІ.
#
#   НЕПЕРЕВІРЕНИЙ БЕКАП = ВІДСУТНІЙ БЕКАП.  (ARCHITECTURE ADR-019)
#
# «pgbackrest info каже, що бекап є» не означає нічого. Значення має тільки одне:
# з цього бекапу реально піднімається БД, і в ній реально лежать наші дані.
# Тому скрипт робить саме це, по-справжньому:
#
#   1. restore з R2 у тимчасовий каталог (--type=immediate: до точки узгодженості);
#   2. ПІДНІМАЄ postgres на порту 5433 з відновленого каталогу;
#   3. рахує товари й замовлення, перевіряє, що на місці SQL-функція
#      complex_price_uah() і послідовність order_number_seq — тобто що RunSQL-міграції
#      (ADR-022) пережили відновлення. Порожня БД, яка «успішно піднялась», —
#      це провал, а не успіх;
#   4. звіряє кількість товарів з БОЙОВОЮ базою: відновилось < 50% — тривога
#      (бекап зроблено з напівпорожньої/битої БД);
#   5. гасить тимчасову БД, чистить каталог, пінгує Healthchecks / шле алерт.
#
# ⚠️ archive_mode=off на тимчасовій БД — ОБОВ'ЯЗКОВО. Інакше вона почне
#    архівувати власний WAL у бойовий репозиторій і зіпсує його.
# =============================================================================
set -uo pipefail

# shellcheck source=/dev/null
[[ -r /var/lib/pgbackrest-state/env.sh ]] && source /var/lib/pgbackrest-state/env.sh

STANZA="${PGBACKREST_STANZA:-nisa}"
DB="${POSTGRES_DB:-nisa}"
# Суперюзер кластера. В офіційному образі його ім'я == POSTGRES_USER, і саме під
# ним працює peer-авторизація через unix-сокет (пароль не потрібен).
PGU="${POSTGRES_USER:-postgres}"
DRILL_DIR=/var/lib/pgbackrest-drill/data
SOCK_DIR=/tmp/drill-socket
PORT=5433
STATE_DIR=/var/lib/pgbackrest-state
STARTED_AT=$(date +%s)

hc_ping() {
    [[ -n "${HEALTHCHECKS_DRILL_URL:-}" ]] || return 0
    curl -fsS --max-time 10 --retry 3 "${HEALTHCHECKS_DRILL_URL}${1}" >/dev/null 2>&1 || true
}

cleanup() {
    pg_ctl -D "$DRILL_DIR" -m immediate stop >/dev/null 2>&1 || true
    rm -rf "${DRILL_DIR:?}" "$SOCK_DIR"
}
trap cleanup EXIT

fail() {
    echo "=== restore-drill: ПРОВАЛ — $1 ===" >&2
    date -Iseconds > "${STATE_DIR}/last_drill_fail"
    hc_ping "/fail"
    /usr/local/bin/notify.sh fail "RESTORE-DRILL ПРОВАЛИВСЯ: $1

Це означає, що з бекапу МОЖЕ НЕ ВІДНОВИТИСЬ прод. Розбиратись треба зараз,
а не тоді, коли він знадобиться."
    exit 1
}

echo "=== restore-drill: старт, $(date -Iseconds) ==="
hc_ping "/start"

# --- 1. Restore ------------------------------------------------------------
rm -rf "${DRILL_DIR:?}"
mkdir -p "$DRILL_DIR" "$SOCK_DIR"
chmod 0700 "$DRILL_DIR"

echo "--- restore з R2 у ${DRILL_DIR}…"
pgbackrest --stanza="$STANZA" \
    --pg1-path="$DRILL_DIR" \
    --type=immediate \
    --target-action=promote \
    restore \
    || fail "pgbackrest restore впав"

# --- 2. Підняти відновлену БД ---------------------------------------------
echo "--- піднімаємо тимчасовий postgres на порту ${PORT}…"
pg_ctl -D "$DRILL_DIR" -w -t 300 -l "${DRILL_DIR}/drill.log" -o "\
    -p ${PORT} \
    -c listen_addresses='' \
    -c unix_socket_directories=${SOCK_DIR} \
    -c archive_mode=off \
    -c archive_command=/bin/true \
    -c shared_buffers=64MB \
    -c max_connections=10" \
    start \
    || { tail -50 "${DRILL_DIR}/drill.log" >&2; fail "відновлена БД не піднялась"; }

# Дочекатись виходу з recovery (промоушен після --type=immediate).
for _ in $(seq 1 60); do
    IN_RECOVERY=$(psql -h "$SOCK_DIR" -p "$PORT" -U "$PGU" -d postgres -tAc \
        "SELECT pg_is_in_recovery()" 2>/dev/null || echo "err")
    [[ "$IN_RECOVERY" == "f" ]] && break
    sleep 2
done
[[ "${IN_RECOVERY:-}" == "f" ]] || fail "БД не вийшла з recovery за 120 с"

Q() { psql -h "$SOCK_DIR" -p "$PORT" -U "$PGU" -d "$DB" -tAc "$1" 2>/dev/null; }

# --- 3. Дані реально на місці? --------------------------------------------
PRODUCTS=$(Q "SELECT count(*) FROM catalog_product")
ORDERS=$(Q "SELECT count(*) FROM orders_order")
[[ "$PRODUCTS" =~ ^[0-9]+$ ]] || fail "не читається catalog_product у відновленій БД"
[[ "$ORDERS"   =~ ^[0-9]+$ ]] || fail "не читається orders_order у відновленій БД"

# Схема — це не тільки таблиці. Ціна рахується SQL-функцією (ADR-005), номер
# замовлення — послідовністю (ADR-014). Якщо RunSQL-міграції не відновились,
# прод підніметься і ВПАДЕ на першому ж синку.
FUNC=$(Q "SELECT count(*) FROM pg_proc WHERE proname = 'complex_price_uah'")
SEQ=$(Q "SELECT count(*) FROM pg_class WHERE relkind = 'S' AND relname = 'order_number_seq'")
[[ "${FUNC:-0}" -ge 1 ]] || fail "у відновленій БД немає функції complex_price_uah() — RunSQL-міграції не доїхали"
[[ "${SEQ:-0}"  -ge 1 ]] || fail "у відновленій БД немає послідовності order_number_seq"

# --- 4. Звірка з бойовою БД ------------------------------------------------
LIVE_PRODUCTS=$(psql -h /var/run/postgresql -U "$PGU" -d "$DB" -tAc \
    "SELECT count(*) FROM catalog_product" 2>/dev/null || echo "0")

if [[ "${LIVE_PRODUCTS:-0}" -gt 100 ]]; then
    MIN_OK=$(( LIVE_PRODUCTS / 2 ))
    [[ "$PRODUCTS" -ge "$MIN_OK" ]] || \
        fail "у бекапі ${PRODUCTS} товарів проти ${LIVE_PRODUCTS} на проді (< 50%). Бекап неповний."
fi

ELAPSED=$(( $(date +%s) - STARTED_AT ))
date -Iseconds > "${STATE_DIR}/last_drill_success"

echo "=== restore-drill: OK за ${ELAPSED}с — товарів ${PRODUCTS} (прод: ${LIVE_PRODUCTS}), замовлень ${ORDERS} ==="
hc_ping ""
/usr/local/bin/notify.sh ok "Restore-drill пройдено за ${ELAPSED}с.
Товарів у бекапі: ${PRODUCTS} (на проді ${LIVE_PRODUCTS}), замовлень: ${ORDERS}.
Бекап відновлюваний."
