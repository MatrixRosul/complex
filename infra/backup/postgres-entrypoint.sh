#!/bin/bash
# =============================================================================
# Entrypoint сервісу `postgres`.
#
# Єдине, що він додає до штатного docker-entrypoint.sh: рендерить конфіг
# pgBackRest з секретів ДО старту постгреса. Без цього перший же archive_command
# впаде («no repo configured»), WAL почне копичитись у pg_wal, і бекапів не буде.
# =============================================================================
set -euo pipefail

/usr/local/bin/render-repo-config.sh

# Далі — штатний entrypoint офіційного образу (initdb, права, su до postgres).
exec docker-entrypoint.sh "$@"
