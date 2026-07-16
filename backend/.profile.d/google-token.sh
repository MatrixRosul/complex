#!/usr/bin/env bash
# Матеріалізує OAuth-токен Google на дино. Виконується Heroku при старті КОЖНОГО дино
# (web, worker, beat, release) — до запуску процесу з Procfile.
#
# ЧОМУ ЦЕ ВЗАГАЛІ ПОТРІБНО. sync/sheets.py читає токен З ФАЙЛА (шлях —
# GOOGLE_OAUTH_TOKEN_FILE). Файл — секрет, він у .gitignore і в репозиторій не потрапляє,
# а диск дино ефемерний. Тому єдиний шлях — config var + запис у файл на старті.
#
# ⚠️ НАЙВАЖЛИВІШЕ: якщо токена немає, sync НЕ ПАДАЄ — він тихо перемикається у
#    ФІКСТУРНИЙ РЕЖИМ і показує демо-дані з sync/fixtures/ як справжній каталог
#    (sheets.py::get_client). Саме тому тут `chmod 0600` і жодного `|| true`:
#    краще зламаний старт, ніж «синк ніби працює, а дані ліві».
set -euo pipefail

if [ -n "${GOOGLE_OAUTH_TOKEN_JSON:-}" ]; then
    umask 077   # токен = доступ до таблиць БЕЗ пароля; світу його не показуємо
    printf '%s' "$GOOGLE_OAUTH_TOKEN_JSON" > "${HOME}/google_token.json"
fi
