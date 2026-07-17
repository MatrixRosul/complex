"""
Доставка — моделі.

Зона відповідальності (ARCHITECTURE.md §1):
    NPArea, NPCity, NPSettlement, NPWarehouse, quote-сервіс, circuit breaker

Джерела: DATA_MODEL.md §6, INTEGRATIONS.md §1.

Ключові рішення, зафіксовані тут:
  • Довідники НП живуть у НАШІЙ БД і оновлюються нічним cron (`delivery.tasks.sync_np_refs`,
    03:00 — CELERY_BEAT_SCHEDULE). API НП НЕ викликається на критичному шляху checkout;
    єдиний виняток — розрахунок вартості, і той за circuit breaker'ом (ADR-020).
  • Зниклі об'єкти НІКОЛИ не видаляються — тільки `is_active=False` (на них посилаються
    старі замовлення). Тому в кожній синхронізованій моделі є `last_seen_run`.
  • 🔴 `SettlementRef ≠ CityRef` (INTEGRATIONS §1.4). Зберігаємо ОБИДВА:
    `NPSettlement.ref` (SettlementRef) і `NPSettlement.delivery_city_ref` (CityRef — КАНОН
    для CitySender/CityRecipient і для `getWarehouses.CityRef`).
  • 🔴 Габарити й вага для НП беруться з ХАРАКТЕРИСТИК товару («Габарити упаковки (ВхШхГ)»,
    «Вага в упаковці»), а НЕ з габаритів самого виробу — це різні числа (ADR-021).
    Ланцюг фолбеків рахує ЄДИНА функція `delivery/services/dims.py::effective_dims()`:
    характеристики → Product.package_* → Category.default_* → SiteSettings.default_*.
    Місто відправника — Ужгород (`SiteSettings.np_sender_city_ref`).


⚠️ ЦІЛЬОВА БАЗА — PostgreSQL 14 (не 15/16). Міграції МУСЯТЬ застосуватись на PG14.
Що з Django 5 тут НЕ ПРАЦЮЄ і чим це замінювати:

  1. UniqueConstraint(..., nulls_distinct=False)  →  потребує PG15. На PG14 NULL-и завжди
     розрізняються, тому «унікальність з урахуванням NULL» робиться ДВОМА частковими
     обмеженнями або унікальним індексом по COALESCE.

  2. GeneratedField  →  на PG14 можливі ЛИШЕ STORED-колонки (db_persist=True) і ЛИШЕ з
     IMMUTABLE-виразів. Тому `NPSettlement.warehouses_count` — звичайне поле, яке перераховує
     синк (потрібен підзапит по іншій таблиці — це не IMMUTABLE), а не GeneratedField.

  3. MERGE (SQL) → PG15. Використовуємо INSERT ... ON CONFLICT (він у PG14 є).

🔴 УВАГА МІГРАЦІЙНОМУ АГЕНТУ:
   `NPSettlement` і `NPWarehouse` мають GIN-індекси з `opclasses=["gin_trgm_ops"]`.
   Оператор-клас `gin_trgm_ops` існує лише після `TrigramExtension()`, яка за ADR-022 стоїть
   у `catalog/0001_initial`. Тому `delivery/0001_initial` МУСИТЬ отримати
       dependencies = [("catalog", "0001_initial")]
   інакше на чистій БД (і в CI, і в test_nisa, який клонується з template1) міграція впаде з
   `operator class "gin_trgm_ops" does not exist`.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import Q
from django.db.models.functions import Greatest, Least

# ---------------------------------------------------------------------------
# Константи НП
# ---------------------------------------------------------------------------

#: Коефіцієнт об'ємної ваги НП: 1 м³ = 250 кг; ціна = max(фактична вага, об'ємна вага).
#: Заміряно live (INTEGRATIONS §1.7): Ужгород→Київ, 2 кг БЕЗ габаритів = 97.5 грн,
#: з габаритами 50×50×60 = 539.5 грн. Різниця 5.5×. Тому `OptionsSeat` шлеться ЗАВЖДИ,
#: навіть коли габарити дефолтні.
NP_VOLUMETRIC_FACTOR = Decimal("250")


class ServiceType(models.TextChoices):
    """ServiceType для `getDocumentPrice` / `InternetDocument.save`.

    🔴 МУСИТЬ відповідати обраній точці видачі (INTEGRATIONS §1.7): обрали поштомат, а послали
    `WarehouseWarehouse` → недорахували ~10 грн на КОЖНОМУ замовленні (97.5 vs 107.5).
    Мапінг робить `NPWarehouse.service_type`.
    """

    WAREHOUSE_WAREHOUSE = "WarehouseWarehouse", "Відділення → Відділення"
    WAREHOUSE_POSTOMAT = "WarehousePostomat", "Відділення → Поштомат"
    WAREHOUSE_DOORS = "WarehouseDoors", "Відділення → Двері (кур'єр)"


class DimsSource(models.TextChoices):
    """Звідки взялись габарити, які пішли в НП (ADR-021).

    Дублює семантику `OrderItem.dims_source`. Категорія, де >20% товарів на "default", —
    це категорія, де магазин СИСТЕМНО недоплачує за доставку (INTEGRATIONS §1.7).
    """

    SPEC = "spec", "Характеристики товару"
    PRODUCT = "product", "Поля товару (ручний override)"
    CATEGORY = "category", "Дефолт категорії"
    DEFAULT = "default", "Глобальний дефолт"


# ---------------------------------------------------------------------------
# Довідники НП (нічний синк, INTEGRATIONS §1.4–1.5)
# ---------------------------------------------------------------------------


class NPArea(models.Model):
    """Область. 25 рядків, змінюється раз на ніколи."""

    ref = models.CharField("Ref НП", max_length=36, primary_key=True)
    name = models.CharField("Назва", max_length=120)
    center_city_ref = models.CharField("Ref обласного центру", max_length=36, blank=True)

    class Meta:
        verbose_name = "Область (НП)"
        verbose_name_plural = "Області (НП)"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class NPCity(models.Model):
    """Місто в термінах НП. `ref` == DeliveryCity ref — КАНОНІЧНИЙ для API НП.

    ~11 100 рядків. Саме цей ref іде в `CityRecipient` і в `getWarehouses.CityRef`.
    """

    ref = models.CharField("CityRef (канон)", max_length=36, primary_key=True)
    area = models.ForeignKey(NPArea, on_delete=models.CASCADE, related_name="cities")
    name = models.CharField("Назва", max_length=200)
    settlement_type = models.CharField("Тип населеного пункту", max_length=60, blank=True)

    is_active = models.BooleanField("Активне", default=True, db_index=True)
    last_seen_run = models.UUIDField("Останній прогін", null=True, blank=True, db_index=True)
    # ⚡ Зникло з відповіді НП → is_active=False, НІКОЛИ не DELETE:
    #    на цей ref посилаються вже створені Order.np_city_ref.

    class Meta:
        verbose_name = "Місто (НП)"
        verbose_name_plural = "Міста (НП)"
        indexes = [models.Index(fields=["area", "name"], name="np_city_area_idx")]

    def __str__(self) -> str:
        return self.name


class NPSettlement(models.Model):
    """Населений пункт — те, що покупець шукає в автокомпліті. ~26 800 рядків.

    🔴 ПАСТКА (INTEGRATIONS §1.4): `Ref` (SettlementRef) і `DeliveryCity` (CityRef) — ДВА РІЗНІ
    UUID на той самий Ужгород. Канонічний для API НП — `delivery_city_ref`.
    Зберігаємо обидва: по `ref` мапиться відповідь `searchSettlements`, по `delivery_city_ref`
    робиться `getWarehouses` і `getDocumentPrice`.
    """

    ref = models.CharField("SettlementRef", max_length=36, primary_key=True)
    delivery_city_ref = models.CharField(
        "DeliveryCity ref (канон для API)", max_length=36, db_index=True
    )
    area = models.ForeignKey(NPArea, on_delete=models.CASCADE, related_name="settlements")

    name = models.CharField("Назва", max_length=200)
    present = models.CharField("Рядок для дропдауна", max_length=255)
    # "м. Ужгород, Закарпатська обл." — саме по цьому полю йде trigram-автокомпліт.
    settlement_type_code = models.CharField("Код типу (м./с./смт)", max_length=16, blank=True)

    warehouses_count = models.PositiveIntegerField("Відділень (наших)", default=0, editable=False)
    # ⚡ НАШ лічильник, а НЕ поле `Warehouses` з відповіді НП.
    #   INTEGRATIONS §1.4: НП віддає `Warehouses: 317` для Ужгорода, а реальний
    #   getWarehouses(CityRef=Ужгород) → totalCount: 178. Поле НП брехливе — використовувати
    #   його як лічильник ЗАБОРОНЕНО. Перераховується наприкінці sync_np_refs з NPWarehouse
    #   (COUNT(*) WHERE is_active). Потрібне для ORDER BY автокомпліта (INTEGRATIONS §1.6):
    #   similarity(present, q) DESC, warehouses_count DESC.
    #   ⚠️ Не GeneratedField: PG14 дозволяє лише STORED з IMMUTABLE-виразу, а тут — підзапит
    #      по іншій таблиці.

    is_active = models.BooleanField("Активний", default=True, db_index=True)
    last_seen_run = models.UUIDField("Останній прогін", null=True, blank=True, db_index=True)

    class Meta:
        verbose_name = "Населений пункт (НП)"
        verbose_name_plural = "Населені пункти (НП)"
        indexes = [
            # Автокомпліт міста — з ВЛАСНОЇ БД, <5 мс (INTEGRATIONS §1.6).
            # 🔴 Потребує pg_trgm → див. шапку файла (залежність від catalog/0001_initial).
            GinIndex(fields=["present"], opclasses=["gin_trgm_ops"], name="np_settl_trgm"),
            models.Index(fields=["area", "name"], name="np_settl_area_idx"),
            models.Index(fields=["is_active", "-warehouses_count"], name="np_settl_active_idx"),
        ]

    def __str__(self) -> str:
        return self.present or self.name


class NPWarehouseQuerySet(models.QuerySet["NPWarehouse"]):
    """Фільтри, без яких чекаут ламається."""

    def active(self) -> NPWarehouseQuerySet:
        """Тільки працюючі. `WarehouseStatus != "Working"` = закрите відділення."""
        return self.filter(is_active=True, status=NPWarehouse.STATUS_WORKING)

    def fits_package(
        self,
        *,
        width_cm: Decimal | float,
        height_cm: Decimal | float,
        length_cm: Decimal | float,
        weight_kg: Decimal | float,
    ) -> NPWarehouseQuerySet:
        """🔴 Найчастіший баг чекаутів: холодильник, запропонований у поштомат.

        `ReceivingLimitationsOnDimensions` ({"Width":40,"Height":30,"Length":60}) НП віддає
        переважно для ПОШТОМАТІВ; у звичайних відділень його зазвичай немає.
        NULL = обмеження невідоме → відділення НЕ відсікаємо (краще запропонувати відділення,
        ніж лишити покупця з порожнім дропдауном).

        Порівняння — З УРАХУВАННЯМ ПОВОРОТУ коробки: коробка влазить у комірку, якщо її
        габарити, відсортовані за спаданням, поелементно ≤ відсортованих габаритів комірки.
        Наївне «ширина з шириною» відсікало б посилки, які реально влазять на бік.
        Сортування габаритів комірки робиться в SQL через GREATEST/LEAST (три колонки —
        середнє = sum - max - min).
        """
        pkg_max, pkg_mid, pkg_min = sorted(
            (Decimal(str(width_cm)), Decimal(str(height_cm)), Decimal(str(length_cm))),
            reverse=True,
        )

        limits_sum = (
            models.F("max_width_cm") + models.F("max_height_cm") + models.F("max_length_cm")
        )
        # Обмеження задане ЧАСТКОВО (не всі три виміри) — перевірити коректно неможливо,
        # тому такі точки не відсікаємо (той самий принцип, що й для NULL).
        has_all_dims = Q(
            max_width_cm__isnull=False,
            max_height_cm__isnull=False,
            max_length_cm__isnull=False,
        )

        return (
            self.annotate(
                _cell_max=Greatest("max_width_cm", "max_height_cm", "max_length_cm"),
                _cell_min=Least("max_width_cm", "max_height_cm", "max_length_cm"),
            )
            .annotate(_cell_mid=limits_sum - models.F("_cell_max") - models.F("_cell_min"))
            .filter(
                (
                    ~has_all_dims
                    | Q(
                        _cell_max__gte=pkg_max,
                        _cell_mid__gte=pkg_mid,
                        _cell_min__gte=pkg_min,
                    )
                )
                & (
                    Q(place_max_weight_kg__isnull=True)
                    | Q(place_max_weight_kg__gte=Decimal(str(weight_kg)))
                )
            )
        )


class NPWarehouse(models.Model):
    """Відділення / поштомат / пункт видачі. ~53 000 рядків.

    ⚠️ `getWarehouses` віддає 54 поля на рядок — проєктуємо до ~15, інакше 178 МБ сирих даних
       (INTEGRATIONS §1.4).
    ⚠️ В Ужгороді: Postomat 148, Branch 20, DropOff 10 — поштоматів у 7 разів більше.
       У UI НЕ змішувати в один плоский список: групувати за `category`.
    """

    STATUS_WORKING = "Working"

    class Category(models.TextChoices):
        BRANCH = "Branch", "Відділення"
        POSTOMAT = "Postomat", "Поштомат"
        DROPOFF = "DropOff", "Пункт видачі"
        FULFILLMENT = "Fulfillment", "Фулфілмент"
        # НП додала "Fulfillment" (11 символів) у CategoryOfWarehouse — з'явилось 07.2026.
        # Не клієнтський пункт видачі, але зберігаємо чесно; фільтр видачі бере лише
        # Branch/Postomat/DropOff.

    ref = models.CharField("Ref НП", max_length=36, primary_key=True)

    city = models.ForeignKey(
        NPCity,
        on_delete=models.SET_NULL,
        related_name="warehouses",
        null=True,
        blank=True,
        db_constraint=False,
    )
    # db_constraint=False: порядок завантаження довідників не гарантує, що місто вже в БД
    # у момент upsert відділень. FK тут — зручність для JOIN-ів і адмінки, не інваріант;
    # робочий ключ — денормалізований `city_ref` (саме він іде в API НП).
    city_ref = models.CharField("CityRef", max_length=36, db_index=True)
    settlement_ref = models.CharField("SettlementRef", max_length=36, db_index=True, blank=True)

    number = models.CharField("Номер", max_length=12)
    description = models.CharField("Опис", max_length=300)
    # "Відділення №1: вул. Волонтерів, 16" — те, що бачить покупець у дропдауні.
    short_address = models.CharField("Коротка адреса", max_length=200, blank=True)

    category = models.CharField("Тип", max_length=20, choices=Category.choices, db_index=True)
    type_ref = models.CharField("TypeOfWarehouse ref", max_length=36, blank=True)

    # --- обмеження прийому (ReceivingLimitationsOnDimensions) ---
    # 🔴 Саме через них фільтруються ПОШТОМАТИ: холодильник у комірку 40×30×60 не влізе.
    #    NULL = НП обмеження не віддала (типово для звичайних відділень) → не обмежуємо.
    #    Перевірка — NPWarehouse.objects.fits_package(...), з урахуванням повороту коробки.
    place_max_weight_kg = models.DecimalField(
        "Макс. вага місця, кг", max_digits=8, decimal_places=2, null=True, blank=True
    )
    max_width_cm = models.DecimalField(
        "Макс. ширина, см", max_digits=6, decimal_places=1, null=True, blank=True
    )  # поштомат — типово 40
    max_height_cm = models.DecimalField(
        "Макс. висота, см", max_digits=6, decimal_places=1, null=True, blank=True
    )  # 30
    max_length_cm = models.DecimalField(
        "Макс. довжина, см", max_digits=6, decimal_places=1, null=True, blank=True
    )  # 60

    latitude = models.DecimalField("Широта", max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(
        "Довгота", max_digits=9, decimal_places=6, null=True, blank=True
    )

    status = models.CharField("WarehouseStatus", max_length=20, blank=True)
    # Зберігаємо всі, показуємо тільки "Working" — інакше пропонуємо закриті відділення.
    is_active = models.BooleanField("Активне", default=True, db_index=True)
    last_seen_run = models.UUIDField("Останній прогін", null=True, blank=True, db_index=True)
    # 🔴 CRITICAL (INTEGRATIONS §1.5): відділення НП закриваються і переносяться регулярно.
    #    Ref зниклого просто не приходить у відповіді. Без last_seen_run рядок лишався б
    #    is_active=True НАЗАВЖДИ → покупець обирає неіснуюче відділення → CheckConstraint
    #    на Order проходить, замовлення створюється, А ТТН НЕ ВИБИВАЄТЬСЯ.
    #    Після повного успішного прогону:
    #        qs.exclude(last_seen_run=run.id).update(is_active=False)   # ніколи не DELETE

    objects = NPWarehouseQuerySet.as_manager()

    class Meta:
        verbose_name = "Відділення (НП)"
        verbose_name_plural = "Відділення (НП)"
        ordering = ("city_ref", "category", "number")
        indexes = [
            models.Index(fields=["city_ref", "category", "is_active"], name="np_wh_city_idx"),
            # 🔴 Потребує pg_trgm (див. шапку файла): пошук відділення за адресою в дропдауні.
            GinIndex(fields=["description"], opclasses=["gin_trgm_ops"], name="np_wh_trgm"),
        ]

    def __str__(self) -> str:
        return self.description or f"{self.get_category_display()} №{self.number}"

    @property
    def service_type(self) -> str:
        """ServiceType, який МУСИТЬ піти в getDocumentPrice саме для цієї точки (§1.7)."""
        if self.category == self.Category.POSTOMAT:
            return ServiceType.WAREHOUSE_POSTOMAT
        return ServiceType.WAREHOUSE_WAREHOUSE


# ---------------------------------------------------------------------------
# Розрахунок вартості
# ---------------------------------------------------------------------------


class DeliveryQuote(models.Model):
    """⚡ ТІЛЬКИ аналітика. Гарячий кеш — Redis (db1, TTL 24 год).

    Пишемо ЛИШЕ на cache-miss і з семплюванням 10% (INTEGRATIONS §1.8): `/delivery/quote` —
    публічний неавторизований ендпоінт, і патерн «публічний запит → INSERT у БД» — це вектор
    забивання диска. `purge_delivery_quotes` — 30 днів.

    ⚡ `unique(cache_key)` СВІДОМО ЗНЯТО (було у v1): при семплюванні рядок не унікальний за
    побудовою, а unique-індекс на публічно-керованому ключі — ще й точка блокувань.
    """

    id = models.BigAutoField(primary_key=True)

    cache_key = models.CharField("Ключ кешу", max_length=140, db_index=True)
    # 🔴 Ключ МУСИТЬ включати cost_declared і тиждень тарифів:
    #    f"{city_ref}:{service_type}:{weight}:{volume}:{cost_declared}:{YYYYWW}"
    #    Оголошена вартість = ціна товару, а вона змінюється 4×/добу. Ключ без неї віддавав би
    #    застарілу вартість доставки НЕСКІНЧЕННО.

    city_ref = models.CharField("CityRef отримувача", max_length=36)
    service_type = models.CharField("ServiceType", max_length=24, choices=ServiceType.choices)

    weight_kg = models.DecimalField("Вага (ефективна), кг", max_digits=7, decimal_places=3)
    volume_m3 = models.DecimalField("Об'єм (ефективний), м³", max_digits=10, decimal_places=5)
    cost_declared = models.DecimalField("Оголошена вартість", max_digits=12, decimal_places=2)

    cost_delivery = models.DecimalField("Вартість доставки", max_digits=10, decimal_places=2)
    cost_redelivery = models.DecimalField(
        "Зворотна доставка грошей", max_digits=10, decimal_places=2, null=True, blank=True
    )
    # RedeliveryCalculate {"CargoType":"Money"} — накладений платіж, ~50 грн.

    dims_source = models.CharField(
        "Джерело габаритів", max_length=10, choices=DimsSource.choices, blank=True
    )
    # ⚡ З effective_dims(). Метрика покриття: категорія, де >20% розрахунків на "default", —
    #    це категорія, де магазин системно недоплачує за доставку (INTEGRATIONS §1.7).

    raw = models.JSONField("Сира відповідь НП", default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Розрахунок доставки"
        verbose_name_plural = "Розрахунки доставки"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["city_ref", "-created_at"], name="quote_city_idx")]

    def __str__(self) -> str:
        return f"{self.city_ref} · {self.service_type} · {self.cost_delivery} грн"
