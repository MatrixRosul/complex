#!/usr/bin/env bash
# =============================================================================
# secrets-init.sh — створює infra/secrets/* (порожні, 0600) і генерує те,
# що можна згенерувати. Запускати НА VPS.
#
# Ці файли монтуються в контейнери як `docker compose secrets` (/run/secrets/*),
# а НЕ передаються через env: env світиться в `docker inspect`, у краш-дампах
# і в breadcrumbs Sentry. У git вони не потрапляють НІКОЛИ (.gitignore).
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SECRETS_DIR="${REPO_ROOT}/infra/secrets"

mkdir -p "$SECRETS_DIR"
chmod 0700 "$SECRETS_DIR"

# Секрети, які МАЄ згенерувати машина — людина вигадає гірший.
AUTO_GENERATED=(
    django_secret_key
    next_revalidate_secret
    pgbackrest_cipher_pass
)

# Секрети, які треба взяти у провайдера і вписати руками.
MANUAL=(
    r2_secret_access_key
    pgbackrest_s3_secret
    google_sa.json
    np_api_key
    liqpay_private_key
    anthropic_api_key
    telegram_bot_token
    email_host_password
)

gen() { LC_ALL=C tr -dc 'A-Za-z0-9_-' < /dev/urandom | head -c 64; }

for name in "${AUTO_GENERATED[@]}"; do
    path="${SECRETS_DIR}/${name}"
    if [[ -s "$path" ]]; then
        echo "  = ${name} (вже є, не чіпаю)"
        continue
    fi
    ( umask 077 && gen > "$path" )
    echo "  + ${name} — згенеровано"
done

for name in "${MANUAL[@]}"; do
    path="${SECRETS_DIR}/${name}"
    if [[ -s "$path" ]]; then
        echo "  = ${name} (вже є, не чіпаю)"
        continue
    fi
    ( umask 077 && : > "$path" )
    echo "  ! ${name} — ПОРОЖНІЙ, впиши значення"
done

chmod 0600 "$SECRETS_DIR"/* 2>/dev/null || true

cat <<'EOF'

--------------------------------------------------------------------------
Далі:
  1. Впиши значення у порожні файли (позначені `!`):
       vim infra/secrets/liqpay_private_key
     Хвостовий \n редактор допише сам — entrypoint його зріже, це нормально.

  2. 🔴 ЗБЕРЕЖИ КОПІЮ infra/secrets/pgbackrest_cipher_pass ПОЗА ЦИМ СЕРВЕРОМ.
     Це ключ шифрування бекапів. VPS згорів разом з ключем → бекапи в R2
     перетворились на випадкові байти. Менеджер паролів, не /root.

  3. Перевір, що нічого не витекло в git:
       git status --short infra/secrets/     # має бути порожньо
--------------------------------------------------------------------------
EOF
