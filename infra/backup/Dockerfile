# =============================================================================
# PostgreSQL 16 + pgBackRest.  ОДИН образ на ДВІ ролі:
#
#   1. сервіс `postgres`  — бо archive_command виконує САМЕ postgres-процес,
#      отже бінарник pgbackrest мусить лежати в його контейнері;
#   2. сервіс `backup`    — cron: base/diff backup + щотижневий restore-drill.
#      Йому потрібні postgres-бінарники (pg_ctl, psql), щоб реально ПІДНЯТИ
#      відновлену БД і перевірити її. Неперевірений бекап = відсутній бекап.
#
# Збірка (контекст — корінь репо):
#   docker build -f infra/backup/Dockerfile -t complex-postgres .
# =============================================================================

FROM postgres:16-bookworm

# В офіційному образі вже підключено apt-репозиторій PGDG — звідти приїде
# свіжий pgbackrest, а не музейний з debian/stable.
#   cron      — розклад бекапів
#   curl      — пінги Healthchecks.io і алерти в Telegram (в базовому образі його немає)
#   jq        — розбір `pgbackrest info --output=json` у healthcheck'у
#   ca-certificates — TLS до R2 (без нього S3 мовчки не працює)
#   tzdata    — Europe/Kyiv, інакше «щоніч о 02:00» буде о 02:00 UTC
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        pgbackrest \
        cron \
        curl \
        jq \
        ca-certificates \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Europe/Kyiv
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Каталоги pgBackRest. Власник — postgres: і archive-push (з postgres-контейнера),
# і backup (з backup-контейнера) працюють від цього користувача.
RUN mkdir -p \
        /etc/pgbackrest/conf.d \
        /var/log/pgbackrest \
        /var/spool/pgbackrest \
        /var/lib/pgbackrest-state \
        /var/lib/pgbackrest-drill \
    && chown -R postgres:postgres \
        /etc/pgbackrest \
        /var/log/pgbackrest \
        /var/spool/pgbackrest \
        /var/lib/pgbackrest-state \
        /var/lib/pgbackrest-drill \
    && chmod 0750 /etc/pgbackrest/conf.d

COPY infra/backup/render-repo-config.sh   /usr/local/bin/render-repo-config.sh
COPY infra/backup/postgres-entrypoint.sh  /usr/local/bin/postgres-entrypoint.sh
COPY infra/backup/backup-entrypoint.sh    /usr/local/bin/backup-entrypoint.sh
COPY infra/backup/backup.sh               /usr/local/bin/backup.sh
COPY infra/backup/restore-drill.sh        /usr/local/bin/restore-drill.sh
COPY infra/backup/check-backup-fresh.sh   /usr/local/bin/check-backup-fresh.sh
COPY infra/backup/notify.sh               /usr/local/bin/notify.sh
COPY infra/backup/crontab                 /etc/cron.d/complex-backup

RUN chmod 0755 /usr/local/bin/*.sh \
    && chmod 0644 /etc/cron.d/complex-backup

# Роль за замовчуванням — postgres (сервіс `backup` перебиває entrypoint у compose).
ENTRYPOINT ["/usr/local/bin/postgres-entrypoint.sh"]
CMD ["postgres"]
