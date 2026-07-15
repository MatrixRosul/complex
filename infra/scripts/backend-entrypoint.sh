#!/bin/sh
# =============================================================================
# Entrypoint Django-контейнерів (web / worker / beat).
#
# Робить рівно дві речі:
#
# 1. МІСТИТЬ СЕКРЕТИ З ФАЙЛІВ У ENV.
#    Секрети приходять у контейнер файлами (`docker compose secrets:` →
#    /run/secrets/*), бо env світиться в `docker inspect`, у логах краш-дампів
#    і в breadcrumbs Sentry (ROADMAP REL-01, INTEGRATIONS §4). Але settings/base.py
#    читає їх через env("LIQPAY_PRIVATE_KEY"). Міст між двома світами — тут:
#    FOO_FILE=/run/secrets/foo  →  FOO=<вміст файлу>.
#    Так само роблять офіційні образи postgres/mysql (_FILE-конвенція).
#
# 2. Збирає статику в спільний том, якщо RUN_COLLECTSTATIC=1 (тільки web).
#
# Міграції ТУТ НЕ ЗАПУСКАЮТЬСЯ. Свідомо: web має 2 репліки, і «migrate на старті»
# означав би дві паралельні міграції на одній БД. Міграції — окремий крок деплою
# (deploy.yml), під lock_timeout.
# =============================================================================
set -eu

# --- 1. *_FILE → env ------------------------------------------------------
# Проходимо по всіх змінних, що закінчуються на _FILE, і підставляємо вміст.
for var in $(env | sed -n 's/^\([A-Z0-9_]*\)_FILE=.*/\1/p'); do
    file_var="${var}_FILE"
    file_path=$(eval "printf '%s' \"\$${file_var}\"")

    if [ ! -r "$file_path" ]; then
        echo "entrypoint: FATAL: ${file_var}=${file_path} — файл не читається." >&2
        echo "entrypoint: секрет не створено на VPS? див. \`make secrets-init\`" >&2
        exit 1
    fi

    # Обрізаємо ХІБА ЩО хвостовий \n (редактори його дописують, а токен з \n
    # на кінці — це підпис LiqPay, що не сходиться, і година дебагу).
    value=$(cat "$file_path")
    export "${var}=${value}"
    unset "${file_var}"
done

# --- 2. Статика -----------------------------------------------------------
# Том static_data спільний з Caddy: Django кладе, Caddy віддає.
# Ідемпотентно; на 2 репліках web може виконатись двічі — це безпечно
# (ManifestStaticFilesStorage пише атомарно, вміст детермінований).
if [ "${RUN_COLLECTSTATIC:-0}" = "1" ]; then
    echo "entrypoint: collectstatic → ${STATIC_ROOT:-/app/staticfiles}"
    python manage.py collectstatic --noinput
fi

exec "$@"
