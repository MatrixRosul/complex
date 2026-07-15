#!/bin/bash
# =============================================================================
# Збирає /etc/pgbackrest/conf.d/40-repo.conf із секретів і env.
# Викликається entrypoint'ом обох контейнерів (postgres і backup) від root.
#
# Навіщо окремий файл, а не env-змінні PGBACKREST_*:
#   1. env видно в `docker inspect` і в breadcrumbs Sentry — секретам там не місце;
#   2. cron ВИЧИЩАЄ оточення. Задача о 02:00 не побачила б жодної PGBACKREST_*
#      змінної з compose і мовчки пішла б у репозиторій без ключів.
#      Конфіг-файл читається pgbackrest'ом завжди, незалежно від оточення.
# =============================================================================
set -euo pipefail

CONF_D=/etc/pgbackrest/conf.d
OUT="${CONF_D}/40-repo.conf"

read_secret() {
    local path="$1" name="$2"
    if [[ ! -r "$path" ]]; then
        echo "render-repo-config: FATAL: немає секрету ${name} (${path})." >&2
        echo "render-repo-config: створи його на VPS — див. \`make secrets-init\`" >&2
        exit 1
    fi
    # Хвостовий \n у ключі S3 → підпис не сходиться → «SignatureDoesNotMatch»
    # і година дебагу. Ріжемо все, що схоже на пробіл, по краях.
    tr -d '\r\n' < "$path"
}

: "${PGBACKREST_R2_BUCKET:?PGBACKREST_R2_BUCKET не заданий}"
: "${PGBACKREST_R2_ENDPOINT:?PGBACKREST_R2_ENDPOINT не заданий}"
: "${PGBACKREST_R2_KEY:?PGBACKREST_R2_KEY не заданий}"

S3_SECRET="$(read_secret /run/secrets/pgbackrest_s3_secret pgbackrest_s3_secret)"
CIPHER_PASS="$(read_secret /run/secrets/pgbackrest_cipher_pass pgbackrest_cipher_pass)"

mkdir -p "$CONF_D"

# umask 077 → файл народжується вже 0600, а не «на мілісекунду 0644».
( umask 077 && cat > "$OUT" <<EOF
# ЗГЕНЕРОВАНО render-repo-config.sh НА СТАРТІ КОНТЕЙНЕРА. Не редагувати руками.
[global]
repo1-s3-bucket=${PGBACKREST_R2_BUCKET}
repo1-s3-endpoint=${PGBACKREST_R2_ENDPOINT}
repo1-s3-key=${PGBACKREST_R2_KEY}
repo1-s3-key-secret=${S3_SECRET}
repo1-cipher-pass=${CIPHER_PASS}
EOF
)

chown postgres:postgres "$OUT"
chmod 0600 "$OUT"

echo "render-repo-config: ${OUT} готовий (0600, postgres)."
