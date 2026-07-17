"""
Celery-задачі доставки.

`sync_np_refs` — НІЧНИЙ СИНК ДОВІДНИКІВ НП (03:00, CELERY_BEAT_SCHEDULE).
Сенс усієї конструкції: API НП не викликається на критичному шляху checkout — чекаут
читає області / населені пункти / відділення з НАШОЇ БД. Аптайм НП не має права
зупиняти продажі.

──────────────────────────────────────────────────────────────────────────────
ДВА ЗАПОБІЖНИКИ, БЕЗ ЯКИХ ЦЕЙ ТАСК ЛАМАЄ МАГАЗИН (INTEGRATIONS §1.5)
──────────────────────────────────────────────────────────────────────────────
1. ДЕАКТИВАЦІЯ, А НЕ ВИДАЛЕННЯ.
   Відділення НП закриваються і переносяться регулярно. Ref зниклого просто не приходить
   у відповіді. Без відмітки `last_seen_run` рядок лишався б `is_active=True` НАЗАВЖДИ →
   покупець обирає неіснуюче відділення → замовлення створюється, а ТТН НЕ вибивається.
   DELETE теж не можна: на ці ref-и посилаються вже створені замовлення.
   → `qs.exclude(last_seen_run=run.id).update(is_active=False)`

2. GUARD НА РІЗКЕ ПАДІННЯ КІЛЬКОСТІ.
   Дзеркальна біда: частковий збій НП віддає 300 відділень замість 53 000 → довідник
   збіднюється → у дропдауні НЕМАЄ відділень → замовлення оформити НЕМОЖЛИВО.
   Повна зупинка продажів через збій ЧУЖОГО API — при тому, що вся ідея локальних
   довідників була саме в тому, щоб від нього не залежати.
   → прийшло менше 80% від наявних активних → ABORTED, довідники НЕ ЧІПАЄМО.

Плюс синтетичний smoke-тест: після синку в Ужгороді й Києві МУСЯТЬ бути активні
відділення. Ні — rollback усієї транзакції.

──────────────────────────────────────────────────────────────────────────────
🔴 ПАСТКА, ЯКУ ЗАКРИВАЄ КРОК «delivery_city_ref»
──────────────────────────────────────────────────────────────────────────────
`SettlementRef ≠ CityRef` (INTEGRATIONS §1.4): це ДВА РІЗНІ UUID на той самий Ужгород.
Канонічний для `CityRecipient` і `getWarehouses` — CityRef (DeliveryCity).
Але масовий `Address/getSettlements` поля `DeliveryCity` НЕ віддає — воно є лише у
`searchSettlements`. Тому `delivery_city_ref` виводимо:
   а) з самої відповіді, якщо `DeliveryCity` там раптом є (нові версії API);
   б) інакше — з дампа `getWarehouses`: він містить ОБИДВА (`SettlementRef` + `CityRef`);
   в) інакше — зіставленням (Area, назва) з NPCity — для НП без жодного відділення
      (туди можлива лише адресна доставка).
Нуль додаткових запитів до НП.
"""

from __future__ import annotations

import contextlib
import logging
import zlib
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from typing import Any

from celery import shared_task
from django.db import connection, transaction
from django.db.models import Count, Q
from django.utils import timezone

from delivery.client import (
    KYIV_CITY_REF,
    NP_SETTLEMENTS_PAGE_LIMIT,
    UZHHOROD_CITY_REF,
    NovaPoshtaClient,
    NovaPoshtaError,
    NovaPoshtaUnavailable,
    get_sync_client,
)
from delivery.models import DeliveryQuote, NPArea, NPCity, NPSettlement, NPWarehouse
from sync.models import SyncRun

log = logging.getLogger(__name__)

__all__ = ["purge_delivery_quotes", "sync_np_refs"]

#: Прийшло менше цієї частки від наявного активного довідника → ABORTED.
GUARD_MIN_RATIO = Decimal("0.8")

#: Ретеншн аналітичних розрахунків (INTEGRATIONS §1.8).
QUOTE_RETENTION_DAYS = 30

#: Скільки рядків пхаємо в один bulk_create/bulk_update.
BATCH = 2000


# ---------------------------------------------------------------------------
# Advisory lock (Postgres): два синки одночасно = гонка за is_active
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def advisory_lock(name: str) -> Iterator[bool]:
    """`pg_try_advisory_lock` — НЕ блокуючий: не вдалось взяти → таск просто пропускає прогін."""
    key = zlib.crc32(name.encode()) - 2**31  # у діапазон signed int4
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", [key])
        acquired = bool(cur.fetchone()[0])
    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", [key])


# ---------------------------------------------------------------------------
# Хелпери мапінгу сирої відповіді НП → наші поля
# ---------------------------------------------------------------------------


def _dec(value: Any) -> Decimal | None:
    """НП віддає числа рядками, а «немає значення» — як "" або "0"."""
    if value in (None, "", "0", 0):
        return None
    try:
        d = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    return d if d > 0 else None


def _limits(row: dict[str, Any]) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """`ReceivingLimitationsOnDimensions` = {"Width":40,"Height":30,"Length":60} або відсутнє.

    Немає → (None, None, None) → відділення НЕ відсікаємо за габаритами (краще запропонувати
    відділення, ніж лишити покупця з порожнім дропдауном).
    """
    lim = row.get("ReceivingLimitationsOnDimensions") or {}
    if not isinstance(lim, dict):
        return None, None, None
    return _dec(lim.get("Width")), _dec(lim.get("Height")), _dec(lim.get("Length"))


# ---------------------------------------------------------------------------
# Головна задача
# ---------------------------------------------------------------------------


@shared_task(
    name="delivery.tasks.sync_np_refs",
    queue="default",
    acks_late=True,
    soft_time_limit=600,
    time_limit=660,
)
def sync_np_refs(
    *, dry_run: bool = False, client: NovaPoshtaClient | None = None
) -> dict[str, Any]:
    """Тягне області / міста / населені пункти / відділення в нашу БД. Upsert по Ref."""
    run = SyncRun.objects.create(
        kind=SyncRun.Kind.NP_REFS,
        trigger=SyncRun.Trigger.MANUAL if dry_run else SyncRun.Trigger.CRON,
        dry_run=dry_run,
    )
    started = timezone.now()
    np = client or get_sync_client()

    with advisory_lock("sync:np_refs") as acquired:
        if not acquired:
            run.status = SyncRun.Status.SKIPPED
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "finished_at"])
            log.warning("sync_np_refs: інший прогін уже виконується — пропускаємо")
            return {"status": run.status, "run_id": str(run.id)}

        # --- 1. Тягнемо ВСЕ. Будь-яка помилка → FAILED цілком, довідники недоторкані. ---
        try:
            areas = np.fetch_all("Address", "getAreas")
            cities = np.fetch_all("Address", "getCities")
            settlements = np.fetch_all(
                "Address", "getSettlements", limit=NP_SETTLEMENTS_PAGE_LIMIT
            )
            warehouses = np.fetch_all("Address", "getWarehouses")
        except (NovaPoshtaUnavailable, NovaPoshtaError) as exc:
            run.status = SyncRun.Status.FAILED
            run.traceback = str(exc)
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "traceback", "finished_at"])
            log.error("sync_np_refs: НП не віддала довідники: %s", exc)
            raise

        fetched = {
            "areas": len(areas),
            "cities": len(cities),
            "settlements": len(settlements),
            "warehouses": len(warehouses),
        }
        run.rows_read = sum(fetched.values())
        log.info("sync_np_refs: отримано %s", fetched)

        # --- 2. GUARD: різке падіння кількості → НЕ ЧІПАЄМО довідник ---
        guard = _check_guard(settlements=len(settlements), warehouses=len(warehouses))
        run.guard_report = guard
        if not guard["passed"]:
            run.status = SyncRun.Status.ABORTED
            run.finished_at = timezone.now()
            run.stats = fetched
            run.save(update_fields=["status", "finished_at", "stats", "guard_report", "rows_read"])
            log.error(
                "sync_np_refs ABORTED: %s — довідник НЕ ЧІПАЄМО (інакше порожній дропдаун "
                "→ замовлення оформити неможливо)",
                guard["failures"],
            )
            return {"status": run.status, "run_id": str(run.id), "guard": guard}

        # --- 3. Upsert + деактивація — ОДНІЄЮ транзакцією ---
        stats: dict[str, Any] = dict(fetched)
        try:
            with transaction.atomic():
                stats |= _upsert_areas(areas)
                stats |= _upsert_cities(cities, run_id=run.id)
                wh_stats, settlement_to_city = _upsert_warehouses(warehouses, run_id=run.id)
                stats |= wh_stats
                stats |= _upsert_settlements(
                    settlements, run_id=run.id, settlement_to_city=settlement_to_city
                )
                stats |= _deactivate_missing(run_id=run.id)
                stats |= _recount_settlement_warehouses()

                # --- 4. Синтетичний smoke-тест: інакше rollback ---
                _smoke_test()

                if dry_run:
                    transaction.set_rollback(True)
                    log.info("sync_np_refs --dry-run: транзакцію відкочено, БД не змінено")
        except AssertionError as exc:
            run.status = SyncRun.Status.FAILED
            run.traceback = f"smoke-тест не пройдено: {exc}"
            run.finished_at = timezone.now()
            run.stats = stats
            run.save(update_fields=["status", "traceback", "finished_at", "stats"])
            log.error("sync_np_refs: %s — усе відкочено", run.traceback)
            raise

    run.status = SyncRun.Status.SUCCESS
    run.finished_at = timezone.now()
    run.duration_ms = int((run.finished_at - started).total_seconds() * 1000)
    run.stats = stats
    run.save(
        update_fields=["status", "finished_at", "duration_ms", "stats", "guard_report", "rows_read"]
    )
    log.info("sync_np_refs OK за %s мс: %s", run.duration_ms, stats)
    return {"status": run.status, "run_id": str(run.id), "stats": stats, "dry_run": dry_run}


def _check_guard(*, settlements: int, warehouses: int) -> dict[str, Any]:
    """Порівнюємо з тим, що зараз АКТИВНЕ. Порожня БД (перший деплой) → guard пропускає."""
    report: dict[str, Any] = {"min_ratio": float(GUARD_MIN_RATIO), "failures": [], "checks": {}}
    for label, model, got in (
        ("settlements", NPSettlement, settlements),
        ("warehouses", NPWarehouse, warehouses),
    ):
        current = model.objects.filter(is_active=True).count()
        ratio = (Decimal(got) / Decimal(current)) if current else None
        report["checks"][label] = {
            "current_active": current,
            "fetched": got,
            "ratio": float(ratio) if ratio is not None else None,
        }
        if current and ratio is not None and ratio < GUARD_MIN_RATIO:
            report["failures"].append(
                f"{label}: НП віддала {got} з {current} активних (ratio={ratio:.2f})"
            )
    report["passed"] = not report["failures"]
    return report


# --- upsert-и -----------------------------------------------------------------


def _upsert_areas(rows: list[dict[str, Any]]) -> dict[str, int]:
    objs = [
        NPArea(
            ref=r["Ref"],
            name=r.get("Description") or "",
            center_city_ref=r.get("AreasCenter") or "",
        )
        for r in rows
        if r.get("Ref")
    ]
    NPArea.objects.bulk_create(
        objs,
        batch_size=BATCH,
        update_conflicts=True,
        update_fields=["name", "center_city_ref"],
        unique_fields=["ref"],
    )
    return {"areas_upserted": len(objs)}


def _upsert_cities(rows: list[dict[str, Any]], *, run_id: Any) -> dict[str, int]:
    known_areas = set(NPArea.objects.values_list("ref", flat=True))
    objs = [
        NPCity(
            ref=r["Ref"],
            area_id=r["Area"],
            name=r.get("Description") or "",
            settlement_type=r.get("SettlementTypeDescription") or "",
            is_active=True,
            last_seen_run=run_id,
        )
        for r in rows
        if r.get("Ref") and r.get("Area") in known_areas
    ]
    NPCity.objects.bulk_create(
        objs,
        batch_size=BATCH,
        update_conflicts=True,
        update_fields=["area", "name", "settlement_type", "is_active", "last_seen_run"],
        unique_fields=["ref"],
    )
    return {"cities_upserted": len(objs), "cities_skipped_no_area": len(rows) - len(objs)}


def _upsert_warehouses(
    rows: list[dict[str, Any]], *, run_id: Any
) -> tuple[dict[str, int], dict[str, str]]:
    """Upsert відділень + побічний продукт: карта SettlementRef → CityRef.

    ⚠️ getWarehouses віддає 54 поля на рядок — проєктуємо до ~15, інакше 178 МБ сирих даних.
    ⚠️ Зберігаємо ВСІ, включно з не-Working: показує їх фільтр `.active()`. Викидати закриті
       з БД не можна — на них посилаються старі замовлення.
    """
    known_cities = set(NPCity.objects.values_list("ref", flat=True))
    settlement_to_city: dict[str, str] = {}
    objs: list[NPWarehouse] = []

    for r in rows:
        ref = r.get("Ref")
        city_ref = r.get("CityRef") or ""
        if not ref or not city_ref:
            continue
        settlement_ref = r.get("SettlementRef") or ""
        if settlement_ref:
            settlement_to_city.setdefault(settlement_ref, city_ref)

        w, h, length = _limits(r)
        objs.append(
            NPWarehouse(
                ref=ref,
                # FK лишаємо NULL, якщо міста ще немає: db_constraint=False, робочий ключ —
                # денормалізований city_ref (саме він іде в API НП).
                city_id=city_ref if city_ref in known_cities else None,
                city_ref=city_ref,
                settlement_ref=settlement_ref,
                number=str(r.get("Number") or "")[:12],
                description=(r.get("Description") or "")[:300],
                short_address=(r.get("ShortAddress") or "")[:200],
                category=r.get("CategoryOfWarehouse") or NPWarehouse.Category.BRANCH,
                type_ref=r.get("TypeOfWarehouse") or "",
                place_max_weight_kg=_dec(r.get("PlaceMaxWeightAllowed")),
                max_width_cm=w,
                max_height_cm=h,
                max_length_cm=length,
                latitude=_dec(r.get("Latitude")),
                longitude=_dec(r.get("Longitude")),
                status=r.get("WarehouseStatus") or "",
                is_active=True,
                last_seen_run=run_id,
            )
        )

    NPWarehouse.objects.bulk_create(
        objs,
        batch_size=BATCH,
        update_conflicts=True,
        update_fields=[
            "city",
            "city_ref",
            "settlement_ref",
            "number",
            "description",
            "short_address",
            "category",
            "type_ref",
            "place_max_weight_kg",
            "max_width_cm",
            "max_height_cm",
            "max_length_cm",
            "latitude",
            "longitude",
            "status",
            "is_active",
            "last_seen_run",
        ],
        unique_fields=["ref"],
    )
    return {"warehouses_upserted": len(objs)}, settlement_to_city


def _upsert_settlements(
    rows: list[dict[str, Any]], *, run_id: Any, settlement_to_city: dict[str, str]
) -> dict[str, int]:
    known_areas = set(NPArea.objects.values_list("ref", flat=True))
    # 🔴 getSettlements.Area — ІНШИЙ простір UUID, ніж getAreas.Ref (перевірено 17.07.2026):
    #    settlements віддають "AddressGeneral" area-ref (…-4b33-11e4…), а FK у нас — на
    #    getAreas.Ref (…-9b87-11de…). getCities.Area збігається з getAreas, getSettlements — ні.
    #    Тому область резолвимо за назвою (AreaDescription → NPArea.name); ref лишаємо як
    #    швидкий шлях на випадок, якщо колись співпаде. Без цього ВСІ ~27k населених
    #    відсікались і автокомпліт міст був порожній.
    area_ref_by_name = {
        name.casefold(): ref for ref, name in NPArea.objects.values_list("ref", "name")
    }
    # Фолбек (в): НП без жодного відділення — беремо CityRef за (Area, назва).
    city_by_area_name = {
        (area, name.casefold()): ref
        for ref, area, name in NPCity.objects.values_list("ref", "area_id", "name")
    }

    objs: list[NPSettlement] = []
    without_city = 0
    for r in rows:
        ref = r.get("Ref")
        raw_area = r.get("Area")
        area = (
            raw_area
            if raw_area in known_areas
            else area_ref_by_name.get((r.get("AreaDescription") or "").casefold())
        )
        if not ref or not area:
            continue
        name = r.get("Description") or ""
        type_code = r.get("SettlementTypeDescription") or ""

        # 🔴 SettlementRef ≠ CityRef. Три джерела канонічного CityRef — див. шапку модуля.
        city_ref = (
            r.get("DeliveryCity")
            or settlement_to_city.get(ref)
            or city_by_area_name.get((area, name.casefold()))
            or ""
        )
        if not city_ref:
            without_city += 1

        objs.append(
            NPSettlement(
                ref=ref,
                delivery_city_ref=city_ref,
                area_id=area,
                name=name,
                present=_present(r),
                settlement_type_code=_type_code(type_code),
                is_active=True,
                last_seen_run=run_id,
            )
        )

    NPSettlement.objects.bulk_create(
        objs,
        batch_size=BATCH,
        update_conflicts=True,
        # warehouses_count НЕ чіпаємо тут — його рахує _recount_settlement_warehouses().
        update_fields=[
            "delivery_city_ref",
            "area",
            "name",
            "present",
            "settlement_type_code",
            "is_active",
            "last_seen_run",
        ],
        unique_fields=["ref"],
    )
    return {
        "settlements_upserted": len(objs),
        "settlements_without_city_ref": without_city,
    }


_TYPE_SHORT = {
    "місто": "м.",
    "село": "с.",
    "селище": "с-ще",
    "селище міського типу": "смт",
    "смт": "смт",
}


def _type_code(type_description: str) -> str:
    return _TYPE_SHORT.get(type_description.casefold(), type_description)[:16]


def _present(r: dict[str, Any]) -> str:
    """Рядок дропдауна: «м. Ужгород, Закарпатська обл.» — саме по ньому йде trigram-пошук."""
    name = r.get("Description") or ""
    code = _type_code(r.get("SettlementTypeDescription") or "")
    area = r.get("AreaDescription") or ""
    region = r.get("RegionsDescription") or ""

    head = f"{code} {name}".strip()
    tail = [p for p in (region, f"{area} обл." if area else "") if p]
    return ", ".join([head, *tail])[:255]


def _deactivate_missing(*, run_id: Any) -> dict[str, int]:
    """🔴 Зниклі — is_active=False, НІКОЛИ не DELETE."""
    out = {}
    for label, model in (
        ("cities", NPCity),
        ("settlements", NPSettlement),
        ("warehouses", NPWarehouse),
    ):
        n = (
            model.objects.filter(is_active=True)
            .exclude(last_seen_run=run_id)
            .update(is_active=False)
        )
        out[f"{label}_deactivated"] = n
        if n:
            log.info("sync_np_refs: деактивовано %s %s (зникли з відповіді НП)", n, label)
    return out


def _recount_settlement_warehouses() -> dict[str, int]:
    """`NPSettlement.warehouses_count` — НАШ лічильник.

    Поле `Warehouses` з відповіді НП брехливе (317 для Ужгорода проти реальних 178) —
    використовувати його як лічильник ЗАБОРОНЕНО. Рахуємо самі; воно керує порядком
    автокомпліта.
    """
    counts = dict(
        NPWarehouse.objects.filter(is_active=True, status=NPWarehouse.STATUS_WORKING)
        .exclude(settlement_ref="")
        .values_list("settlement_ref")
        .annotate(n=Count("ref"))
    )
    to_update = []
    for st in NPSettlement.objects.filter(
        Q(ref__in=counts.keys()) | Q(warehouses_count__gt=0)
    ).only("ref", "warehouses_count"):
        new = counts.get(st.ref, 0)
        if st.warehouses_count != new:
            st.warehouses_count = new
            to_update.append(st)
    NPSettlement.objects.bulk_update(to_update, ["warehouses_count"], batch_size=BATCH)
    return {"settlements_recounted": len(to_update)}


def _smoke_test() -> None:
    """Довідник, у якому немає Ужгорода або Києва, — битий довідник."""
    for label, city_ref in (("Ужгород", UZHHOROD_CITY_REF), ("Київ", KYIV_CITY_REF)):
        if not NPWarehouse.objects.filter(
            city_ref=city_ref, is_active=True, status=NPWarehouse.STATUS_WORKING
        ).exists():
            raise AssertionError(f"після синку немає жодного активного відділення в {label}")


# ---------------------------------------------------------------------------
# Прибирання
# ---------------------------------------------------------------------------


@shared_task(name="delivery.tasks.purge_delivery_quotes", queue="default")
def purge_delivery_quotes(days: int = QUOTE_RETENTION_DAYS) -> dict[str, int]:
    """DeliveryQuote — аналітика, не кеш. Ретеншн 30 днів (INTEGRATIONS §1.8)."""
    cutoff = timezone.now() - timedelta(days=days)
    deleted = 0
    while True:
        ids = list(
            DeliveryQuote.objects.filter(created_at__lt=cutoff).values_list("id", flat=True)[
                :10_000
            ]
        )
        if not ids:
            break
        # Батчами: звичайний DELETE на мільйонах рядків = довга транзакція і bloat.
        deleted += DeliveryQuote.objects.filter(id__in=ids).delete()[0]
    log.info("purge_delivery_quotes: видалено %s рядків, старших за %s днів", deleted, days)
    return {"deleted": deleted}
