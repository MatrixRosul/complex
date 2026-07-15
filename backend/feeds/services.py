"""Фіди — сервіси: генерація, атомарний запис, перемикання поточного артефакту.

Фід віддається за ПОСТІЙНИМ URL `https://complex.ua/feeds/hotline.xml` (Caddy → file_server,
без редіректів, без авторизації, без challenge — бот Hotline їх не пройде). Тобто «поточна
версія» — це СТАН, а не файл: генерація 4×/добу переписує один і той самий шлях.

🔴 ЗАПИС — ТІЛЬКИ ATOMIC WRITE + RENAME. Бот заходить за розкладом і цілком може прийти рівно в
   мить запису. `os.replace()` на тому самому ФС атомарний — бот отримає або старий файл цілком,
   або новий цілком, але ніколи напівзаписаний XML (тобто «фід битий» → зняття з майданчика).

🔴 Фід публікується 7 днів. Не оновили — публікація ПРИПИНЯЄТЬСЯ і товари зникають з майданчика.
   Звідси heal_hotline_feed (щогодини): артефакт старший 24 год АБО файлу на диску немає →
   регенерація.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import SiteSettings
from feeds.generator import FeedStats, build_feed
from feeds.models import FeedArtifact
from sync.models import SyncRun

log = logging.getLogger(__name__)

__all__ = [
    "FEED_FILENAME",
    "FEED_STORAGE_NAME",
    "atomic_write",
    "current_artifact",
    "feed_dir",
    "feed_path",
    "generate_hotline_feed",
    "set_current",
]

FEED_FILENAME = "hotline.xml"
FEED_STORAGE_NAME = f"feeds/{FEED_FILENAME}"  # FileField.name → /media/feeds/hotline.xml
FEED_MAX_AGE_HOURS = 24


def feed_dir() -> Path:
    """Куди кладемо файл.

    Локально — `media/feeds/`. У проді файл живе в NAMED VOLUME `feeds_data` (Caddy монтує його
    :ro і віддає з /srv/feeds), а НЕ в шарі контейнера: інакше `compose up -d` зносить фід, і
    Hotline через 7 днів мовчки припиняє публікацію.
    """
    configured = getattr(settings, "HOTLINE_FEED_DIR", None)
    return Path(configured) if configured else Path(settings.MEDIA_ROOT) / "feeds"


def feed_path() -> Path:
    return feed_dir() / FEED_FILENAME


def atomic_write(path: Path, data: bytes) -> int:
    """Пише файл так, щоб читач ніколи не побачив половину. Повертає розмір у байтах."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".hotline-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())  # без fsync rename переживе краш, а вміст — ні
        # mkstemp дає 0600 — Caddy читає файл з іншого uid і отримав би 403 на публічній статиці.
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, path)  # атомарний на тому самому ФС
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return len(data)


@transaction.atomic
def set_current(artifact: FeedArtifact) -> None:
    """Перемикання поточного артефакту — ДВОКРОКОВЕ і в ОДНІЙ транзакції.

    ⚠️ `uniq_current_feed` — ЧАСТКОВИЙ unique-індекс (PG14, ADR-016), а він НЕ МОЖЕ бути
       DEFERRABLE. Тобто спершу знімаємо прапорець зі старого, і лише потім ставимо новому —
       інакше IntegrityError.
    """
    FeedArtifact.objects.filter(kind=artifact.kind, is_current=True).exclude(pk=artifact.pk).update(
        is_current=False
    )
    artifact.is_current = True
    artifact.save(update_fields=["is_current", "updated_at"])


def current_artifact() -> FeedArtifact | None:
    return FeedArtifact.objects.filter(kind=FeedArtifact.Kind.HOTLINE_XML, is_current=True).first()


def generate_hotline_feed(
    *,
    trigger: str = SyncRun.Trigger.CRON,
    dry_run: bool = False,
    user_id: int | None = None,
) -> FeedArtifact:
    """Генерує фід і повертає FeedArtifact з лічильниками.

    dry_run → XML будується (тобто помилки шаблону вилізуть), але файл НЕ пишеться і артефакт
    НЕ зберігається: повертається незбережений FeedArtifact з тими самими лічильниками.
    """
    started = time.monotonic()
    site = SiteSettings.get_solo()

    run: SyncRun | None = None
    if not dry_run:
        run = SyncRun.objects.create(
            kind=SyncRun.Kind.HOTLINE_FEED,
            trigger=trigger,
            started_by_id=user_id,
            status=SyncRun.Status.RUNNING,
        )

    try:
        xml, stats = build_feed(site=site)
    except Exception as exc:
        if run:
            _finish_run(run, SyncRun.Status.FAILED, stats=None, error=str(exc))
        log.exception("hotline_feed_failed")
        raise

    artifact = FeedArtifact(
        kind=FeedArtifact.Kind.HOTLINE_XML,
        items_count=stats.items,
        skipped_count=stats.skipped_total,
        skipped_reasons=stats.as_reasons(),
        no_mpn_count=stats.no_mpn,
        default_dims_count=stats.default_dims,
        size_bytes=len(xml),
        duration_ms=int((time.monotonic() - started) * 1000),
        run=run,
    )

    if dry_run:
        log.info("hotline_feed_dry_run items=%s skipped=%s", stats.items, stats.as_reasons())
        return artifact

    artifact.size_bytes = atomic_write(feed_path(), xml)
    artifact.file.name = FEED_STORAGE_NAME  # шлях фіксований — Django-ім'я НЕ уніфікуємо
    artifact.duration_ms = int((time.monotonic() - started) * 1000)
    artifact.save()
    set_current(artifact)

    SiteSettings.objects.filter(pk=site.pk).update(hotline_feed_generated_at=timezone.now())
    SiteSettings.invalidate()

    if run:
        _finish_run(run, SyncRun.Status.SUCCESS, stats=stats)

    log.info(
        "hotline_feed_generated artifact=%s items=%s skipped=%s no_mpn=%s path=%s",
        artifact.pk,
        stats.items,
        stats.as_reasons(),
        stats.no_mpn,
        feed_path(),
    )
    return artifact


def _finish_run(run: SyncRun, status: str, *, stats: FeedStats | None, error: str = "") -> None:
    run.status = status
    run.finished_at = timezone.now()
    run.duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)
    if stats is not None:
        run.rows_read = stats.items + stats.skipped_total
        run.rows_skipped = stats.skipped_total
        run.stats = {
            "items": stats.items,
            "skipped_reasons": stats.as_reasons(),
            "no_mpn": stats.no_mpn,
            "default_dims": stats.default_dims,
        }
    if error:
        run.traceback = error
    run.save(
        update_fields=[
            "status",
            "finished_at",
            "duration_ms",
            "rows_read",
            "rows_skipped",
            "stats",
            "traceback",
        ]
    )


def feed_is_stale() -> tuple[bool, str]:
    """Чи треба лікувати фід. Повертає (треба, причина)."""
    artifact = current_artifact()
    if artifact is None:
        return True, "no_current_artifact"
    if not feed_path().exists():
        # Класика: `compose up -d` знищив файл разом із шаром контейнера.
        return True, "file_missing"
    age = timezone.now() - artifact.generated_at
    if age.total_seconds() > FEED_MAX_AGE_HOURS * 3600:
        return True, f"stale_{int(age.total_seconds() // 3600)}h"
    return False, ""
