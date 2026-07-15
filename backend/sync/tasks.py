"""
Celery-задачі синхронізації (SYNC.md §8).

Черги задані ЯВНО в декораторі, а не тільки через `CELERY_TASK_ROUTES`: маршрут — це
налаштування, яке можна забути перенести в інший env, а `queue=` в декораторі поїде разом
із задачею. `sync` іде з `-c 1`, але серіалізацію тримає advisory-lock у PostgreSQL,
а не концентрація воркера (SYNC.md §2, крок 0).

⚠️ `on_commit` — це ШВИДКИЙ ШЛЯХ, а не гарантія. Якщо процес помер між COMMIT і `.delay()`,
задача не поставлена НАЗАВЖДИ. Гарантію дають heal-задачі: `denorm_dirty` виставляється
СИНХРОННО, у тій самій транзакції, тому навіть повна втрата черги самозагоюється за 5 хв.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from urllib.parse import urlparse

import httpx
from celery import shared_task
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from catalog.enums import ProductSource
from catalog.models import Product, ProductImage
from sync.models import PriceSource, ProductPriceSnapshot, SyncLogEntry, SyncRun
from sync.services import (
    RunLog,
    ensure_main_image,
    prune_stale_images,
    rebuild_denorm,
    recalc_prices,
    rollback_run,
    run_price_sync,
)
from sync.services import (
    heal_denorm as _heal_denorm,
)
from sync.services import (
    reap_stale_runs as _reap_stale_runs,
)

log = logging.getLogger(__name__)

# --- Фото -------------------------------------------------------------------
MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_CONTENT_TYPES = frozenset(
    {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif", "image/avif"}
)
IMAGE_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
MAX_IMAGE_ATTEMPTS = 5
#: Деривативи: 300 / 700 / 1600 px. Імена — ЛАТИНИЦЕЮ: кирилиця в шляху = товар без фото
#: у фіді Hotline, тобто товар, якого ніхто не побачить.
DERIVATIVES = (("file_thumb", 300, "sm"), ("file_card", 700, "md"), ("file_large", 1600, "lg"))


# ---------------------------------------------------------------------------
# Прайс
# ---------------------------------------------------------------------------


@shared_task(
    queue="sync",
    acks_late=True,
    soft_time_limit=600,
    time_limit=720,
    expires=600,
    ignore_result=False,
)
def sync_prices(
    source_id: int | None = None,
    *,
    source_code: str = "",
    force: bool = False,
    dry_run: bool = False,
    started_by: int | None = None,
    trigger: str = SyncRun.Trigger.CRON,
) -> dict[str, object]:
    """Головна задача: повний прогін прайсу одного джерела."""
    source = (
        PriceSource.objects.get(pk=source_id)
        if source_id
        else PriceSource.objects.get(code=source_code)
    )
    run = run_price_sync(
        source,
        trigger=trigger,
        dry_run=dry_run,
        force=force,
        user_id=started_by,
    )
    return {
        "run_id": str(run.id),
        "status": run.status,
        "created": run.products_created,
        "updated": run.products_updated,
        "deactivated": run.products_deactivated,
        "failed": run.rows_failed,
    }


@shared_task(queue="sync")
def sync_all_prices(trigger: str = SyncRun.Trigger.CRON) -> list[str]:
    """Фан-аут по всіх активних джерелах. Per-source advisory-lock — усередині."""
    from core.models import SiteSettings

    if not SiteSettings.get_solo().sync_enabled:  # kill-switch в адмінці
        log.warning("Синхронізація вимкнена в налаштуваннях магазину — прогін пропущено.")
        return []

    codes = list(
        PriceSource.objects.filter(is_active=True, kind=PriceSource.Kind.GOOGLE_SHEET)
        .order_by("priority")
        .values_list("code", flat=True)
    )
    for code in codes:
        sync_prices.delay(source_code=code, trigger=trigger)
    return codes


@shared_task(queue="sync")
def rollback_sync_run(run_id: str, user_id: int | None = None) -> dict[str, object]:
    """Відкотити прогін зі знімка `ProductPriceSnapshot` (SYNC.md §5)."""
    run = rollback_run(run_id, user_id=user_id)
    return {"run_id": str(run.id), "status": run.status, "restored": run.products_updated}


@shared_task(queue="sync")
def recalc_usd_prices(
    new_rate: str | float | Decimal | None = None,
    *,
    scope: str = "usd",
    old_rate: str | float | Decimal | None = None,
    old_rounding: str = "",
    user_id: int | None = None,
) -> dict[str, object]:
    """Перерахунок цін після зміни курсу / округлення / націнки.

    ⚠️ `new_rate` тут — АРГУМЕНТ АУДИТУ, а не джерело правди: формула бере курс із
    `SiteSettings` усередині тієї ж транзакції. Інакше два перерахунки з різними
    аргументами перезаписали б одне одного, і в БД лишився б курс, якого ніхто не вводив.

    ⚠️ Формула — ТА САМА `complex_price_uah()`, що й у синку, І З НАЦІНКОЮ. У v1 recalc рахував
    `base * rate` без markup → перша ж зміна курсу СТИРАЛА маржу на всіх USD-товарах.
    """
    run = recalc_prices(
        scope=scope,
        old_rate=Decimal(str(old_rate)) if old_rate is not None else None,
        old_rounding=old_rounding,
        user_id=user_id,
    )
    return {"run_id": str(run.id), "status": run.status, "recalculated": run.products_updated}


@shared_task(queue="sync")
def fill_specs_for_new(run_id: str, product_ids: list[int]) -> int:
    """Характеристики з окремої таблиці — окремою задачею (не блокує прайс)."""
    from sync.services import fill_specs_for_products

    run = SyncRun.objects.select_related("price_source").get(pk=run_id)
    if run.price_source is None:
        return 0
    rlog = RunLog(run)
    filled = fill_specs_for_products(run.price_source, product_ids, run, rlog)
    rlog.flush()
    SyncRun.objects.filter(pk=run.pk).update(specs_filled=filled)
    return filled


# ---------------------------------------------------------------------------
# Фото — черга images
# ---------------------------------------------------------------------------


class UnsafeImageURL(ValueError):
    """SSRF-guard: URL веде не туди, куди має."""


def assert_safe_url(url: str) -> None:
    """SSRF-guard: тільки https + публічний IP.

    ⚠️ Постачальник кладе URL у таблицю руками. `http://169.254.169.254/latest/meta-data/`
    у колонці «Фото» — це не паранойя, це стандартний спосіб винести креденшели з хмари.
    Тому: схема тільки `https`, резолв DNS і ЗАБОРОНА private / loopback / link-local /
    reserved адрес — для КОЖНОЇ адреси, яку віддав резолвер.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise UnsafeImageURL(f"Тільки https, отримано {parsed.scheme!r}")
    if not parsed.hostname:
        raise UnsafeImageURL("URL без хоста")

    try:
        infos = socket.getaddrinfo(parsed.hostname, 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise UnsafeImageURL(f"DNS не резолвиться: {exc}") from exc

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise UnsafeImageURL(f"Приватна/службова адреса заборонена: {ip}")


@shared_task(
    queue="images",
    acks_late=True,
    rate_limit="20/s",
    soft_time_limit=60,
    time_limit=90,
)
def download_product_image(
    image_id: int | None = None,
    *,
    product_id: int | None = None,
    url: str = "",
    run_id: str | None = None,
) -> str:
    """Завантажити фото товару.

    ТРИ РІВНІ ДЕДУПЛІКАЦІЇ (щоб не качати те саме 4 рази на добу — SYNC.md §7):
      1. `UniqueConstraint(product, source_url_hash)` — запис не дублюється;
      2. **ETag → `If-None-Match` → 304** — тіло взагалі не качаємо;
      3. `content_hash = sha256(bytes)` — байти ті самі → не переливаємо в сховище.

    Невідновна помилка (403, битий файл, SSRF) → `failed_reason`, БЕЗ ретраїв.
    Відновна (5xx, таймаут) → `next_retry_at = now + 2^attempts хв`, максимум 5 спроб.
    """
    import hashlib

    from PIL import Image

    from sync.services import url_hash

    if image_id is not None:
        image = ProductImage.objects.filter(pk=image_id).first()
        if image is None:
            return "gone"
    else:
        if not (product_id and url):
            raise ValueError("Потрібен або image_id, або пара (product_id, url)")
        image, _ = ProductImage.objects.get_or_create(
            product_id=product_id,
            source_url_hash=url_hash(url),
            defaults={
                "source": ProductSource.SHEET,
                "source_url": url[:1000],
                "last_seen_run": run_id,
            },
        )

    src = image.source_url
    if not src:
        return "no-url"

    try:
        assert_safe_url(src)
    except UnsafeImageURL as exc:
        _fail_image(image, f"ssrf: {exc}", retryable=False)
        return "unsafe"

    headers = {"User-Agent": "Complex-sync/1.0"}
    if image.etag:
        headers["If-None-Match"] = image.etag

    try:
        with (
            httpx.Client(timeout=IMAGE_TIMEOUT, follow_redirects=True, max_redirects=3) as client,
            client.stream("GET", src, headers=headers) as resp,
        ):
            if resp.status_code == 304:
                ProductImage.objects.filter(pk=image.pk).update(
                    downloaded_at=timezone.now(), updated_at=timezone.now()
                )
                return "not-modified"

            if resp.status_code >= 400:
                retryable = resp.status_code >= 500 or resp.status_code == 429
                _fail_image(image, f"http {resp.status_code}", retryable=retryable)
                return f"http-{resp.status_code}"

            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype and ctype not in ALLOWED_CONTENT_TYPES:
                _fail_image(image, f"content-type {ctype}", retryable=False)
                return "bad-content-type"

            declared = resp.headers.get("content-length")
            if declared and int(declared) > MAX_IMAGE_BYTES:
                _fail_image(image, f"too big: {declared} B", retryable=False)
                return "too-big"

            buf = BytesIO()
            for chunk in resp.iter_bytes(64 * 1024):
                buf.write(chunk)
                # ⚠️ Content-Length може брехати — тому ліміт перевіряємо і ПО ФАКТУ стріму.
                if buf.tell() > MAX_IMAGE_BYTES:
                    _fail_image(image, "too big (stream)", retryable=False)
                    return "too-big"
            etag = resp.headers.get("etag", "")
    except httpx.HTTPError as exc:
        _fail_image(image, f"network: {exc}"[:250], retryable=True)
        return "network-error"

    payload = buf.getvalue()
    content_hash = hashlib.sha256(payload).hexdigest()
    if content_hash == image.content_hash and image.downloaded_at:
        ProductImage.objects.filter(pk=image.pk).update(etag=etag[:255], updated_at=timezone.now())
        return "unchanged"

    try:
        Image.open(BytesIO(payload)).verify()  # битий файл — невідновна помилка
        source_image = Image.open(BytesIO(payload))
        source_image.load()
    except Exception as exc:
        _fail_image(image, f"broken image: {exc}"[:250], retryable=False)
        return "broken"

    stem = image.source_url_hash[:16]
    image.file.save(
        f"{stem}.{(source_image.format or 'jpg').lower()}", ContentFile(payload), save=False
    )

    rgb = source_image.convert("RGB")
    for field_name, size, suffix in DERIVATIVES:
        derivative = rgb.copy()
        derivative.thumbnail((size, size))
        out = BytesIO()
        derivative.save(out, format="WEBP", quality=82, method=4)
        getattr(image, field_name).save(
            f"{stem}_{suffix}.webp", ContentFile(out.getvalue()), save=False
        )

    image.width, image.height = source_image.size
    image.content_hash = content_hash
    image.etag = etag[:255]
    image.downloaded_at = timezone.now()
    image.failed_reason = ""
    image.next_retry_at = None
    image.save()

    # ⚠️ ПОРЯДОК КРИТИЧНИЙ. Спершу прибираємо застаріле фото, і лише потім призначаємо головне.
    #    Навпаки — і виходить рівно той баг, який ми лікуємо: старе фото ще на місці, тому
    #    ensure_main_image() бачить головне і мовчить; наступним рядком старе (воно ж головне)
    #    видаляється — і товар лишається БЕЗ головного фото взагалі. Тобто без фото у фіді.
    if run_id:
        prune_stale_images([image.product_id], run_id)
    ensure_main_image(image.product_id)

    transaction.on_commit(lambda: rebuild_product_denorm.delay([image.product_id]))
    return "ok"


def _fail_image(image: ProductImage, reason: str, *, retryable: bool) -> None:
    attempts = image.attempts + 1
    next_retry = None
    if retryable and attempts < MAX_IMAGE_ATTEMPTS:
        next_retry = timezone.now() + timedelta(minutes=2**attempts)
    ProductImage.objects.filter(pk=image.pk).update(
        attempts=attempts,
        failed_reason=reason[:255],
        next_retry_at=next_retry,
        updated_at=timezone.now(),
    )
    log.warning("Фото %s не завантажилось (%s), retry=%s", image.pk, reason, next_retry)

    # ⚠️ ДЕНОРМАЛІЗАЦІЯ НЕ ЗАЛЕЖИТЬ ВІД ФОТО — і цей рядок саме про це.
    #
    # Історія бага: `rebuild_product_denorm` ставився ЄДИНОГО разу — в УСПІШНОМУ фіналі
    # download_product_image(). Тобто характеристики товару (specs_json, filter_tokens,
    # specs_num, FTS-вектор) будувались як ПОБІЧНИЙ ЕФЕКТ вдало завантаженої картинки.
    # Одне мертве посилання на фото (404 / SSL / SSRF / таймаут) — і товар лишався з
    # порожнім specs_json НАЗАВЖДИ: у картці «Характеристики уточнюються», у порівнянні
    # порожня колонка, у фільтрах він не знаходиться. У базі це рівно 29 товарів зі
    # 100% збігом: усі, у кого failed_reason<>'', мали specs_json_uk = [].
    #
    # Тому перебудову ставимо і на ПОМИЛЦІ фото: фото — це фото, характеристики — це
    # характеристики, і одне не має права вбивати інше.
    transaction.on_commit(lambda: rebuild_product_denorm.delay([image.product_id]))


@shared_task(queue="images")
def heal_images(limit: int = 200) -> int:
    """Фото, які так і не доїхали: задача загубилась або настав час ретраю."""
    from django.db.models import Q

    ids = list(
        ProductImage.objects.filter(
            Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=timezone.now()),
            downloaded_at__isnull=True,
            failed_reason="",
            attempts__lt=MAX_IMAGE_ATTEMPTS,
        ).values_list("id", flat=True)[:limit]
    )
    for image_id in ids:
        download_product_image.delay(image_id=image_id)
    return len(ids)


# ---------------------------------------------------------------------------
# Денормалізація і самозагоювання
# ---------------------------------------------------------------------------


@shared_task(queue="default")
def rebuild_product_denorm(product_ids: list[int]) -> int:
    """specs_json / filter_tokens / specs_num / main_image_url / FTS-вектори."""
    return rebuild_denorm(product_ids)


@shared_task(queue="default")
def heal_denorm(limit: int = 500) -> int:
    """Гарантія проти втраченої on_commit-задачі (ADR-011).

    `denorm_dirty=True` виставляється СИНХРОННО в транзакції синку. Тому товар, чия задача
    загубилась між COMMIT і `.delay()`, не лишається назавжди без specs_json/filter_tokens:
    він є в БД, але не знаходився б фільтрами і йшов би у фід без фото — і ніхто б не дізнався.
    """
    return _heal_denorm(limit)


@shared_task(queue="default")
def reap_stale_runs(older_than_minutes: int = 20) -> int:
    """`SyncRun`, що завис у RUNNING (OOM-kill воркера), → FAILED + алерт."""
    n = _reap_stale_runs(older_than_minutes)
    if n:
        log.error("reap_stale_runs: %s прогонів висіли в RUNNING → FAILED", n)
    return n


# ---------------------------------------------------------------------------
# Прибирання
# ---------------------------------------------------------------------------


@shared_task(queue="default")
def purge_sync_logs(days: int = 90, batch: int = 10_000) -> int:
    """Батчами по 10k з комітами: звичайний DELETE на мільйонах рядків = довга транзакція,
    bloat і autovacuum-шторм."""
    cutoff = timezone.now() - timedelta(days=days)
    total = 0
    while True:
        ids = list(
            SyncLogEntry.objects.filter(created_at__lt=cutoff).values_list("id", flat=True)[:batch]
        )
        if not ids:
            return total
        total += SyncLogEntry.objects.filter(id__in=ids).delete()[0]


@shared_task(queue="default")
def purge_snapshots(days: int = 30) -> int:
    """`ProductPriceSnapshot` старші 30 днів. Відкат старішого прогону і так не має сенсу."""
    cutoff = timezone.now() - timedelta(days=days)
    return ProductPriceSnapshot.objects.filter(run__started_at__lt=cutoff).delete()[0]


@shared_task(queue="default")
def remap_unmapped_products(external_id: str) -> int:
    """Контент-менеджер створив категорію → товари самі переїжджають з «__unmapped__».

    Ідемпотентно, один UPDATE. У v1 шляху назад не було ЗОВСІМ: `category` писалась тільки
    при створенні товару, тому товари з «__unmapped__» лишались там НАЗАВЖДИ.
    """
    from catalog.models import Category

    unmapped = Category.objects.filter(external_id=Category.UNMAPPED_EXTERNAL_ID).first()
    target = Category.objects.filter(external_id=external_id).first()
    if unmapped is None or target is None or target.pk == unmapped.pk:
        return 0

    n = Product.objects.filter(category=unmapped, source_category_ext_id=external_id).update(
        category=target, is_active=True, denorm_dirty=True, updated_at=timezone.now()
    )
    if n:
        log.info("remap_unmapped_products(%s): переїхало %s товарів", external_id, n)
    return n
