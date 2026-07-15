"""
Синхронізація — моделі.

Зона відповідальності (ARCHITECTURE.md §1):
    PriceSource, PriceSheet, SpecSheet, SupplierOffer, SyncRun, SyncLogEntry,
    ProductPriceSnapshot, UsdRateChange

Джерело правди: DATA_MODEL.md §3, SYNC.md §0–§7.

Головна ідея архітектури (SYNC.md §1):

    Прайс Complex (priority=100)  ─▶ SupplierOffer(price_source=complex)  ─┐
    Прайс Alpha (priority=200) ─▶ SupplierOffer(price_source=alpha) ─┴─▶ project_offers()
                                                                             │
                                                                             ▼
                                                                       UPDATE Product

    Синк пише ТІЛЬКИ в SupplierOffer. У Product дані потрапляють одним UPDATE-ом проєкції,
    де переможець обирається за PriceSource.priority. Кожне джерело живе у власній пісочниці:
    upsert і деактивація йдуть у межах (price_source, sku) — перетинів немає ЗА ПОБУДОВОЮ.

⚠️ ЦІЛЬОВА БАЗА — PostgreSQL 14 (не 15/16). Міграції МУСЯТЬ застосуватись на PG14.
Що з Django 5 тут НЕ ПРАЦЮЄ і чим це замінювати:

  1. UniqueConstraint(..., nulls_distinct=False)  →  потребує PG15. На PG14 NULL-и завжди
     розрізняються, тому «унікальність з урахуванням NULL» робиться ДВОМА частковими
     обмеженнями або унікальним індексом по COALESCE.
     Тут це не знадобилось: усі unique-ключі (price_source, sku), (price_source, tab_name)
     складаються лише з NOT NULL-колонок.

  2. GeneratedField  →  на PG14 можливі ЛИШЕ STORED-колонки (db_persist=True) і ЛИШЕ з
     IMMUTABLE-виразів. Ціна залежить від курсу (SiteSettings) і націнки (PriceSource) —
     це не IMMUTABLE. Тому Product.price рахує SQL-функція complex_price_uah() в UPDATE-ах
     проєкції (SYNC.md §3), а не згенерована колонка.

  3. MERGE (SQL) → PG15. Використовуємо INSERT ... ON CONFLICT (він у PG14 є) —
     саме на ньому тримається bulk_create(update_conflicts=True) для SupplierOffer.

Будь-який такий обхід — задокументувати коментарем БІЛЯ ПОЛЯ, а не тільки тут.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from catalog.models import Availability, SourceCurrency
from core.models import TimeStampedModel

# ---------------------------------------------------------------------------
# §3.1 Джерела прайсів
# ---------------------------------------------------------------------------


class PriceSource(TimeStampedModel):
    """
    Джерело прайсу: власний склад Complex або сторонній постачальник.

    Мультипостачальники закладені НЕ «на майбутнє»: без цієї моделі + SupplierOffer
    два прайси з тим самим артикулом взаємно деактивують товари (SYNC.md §1).
    """

    class Kind(models.TextChoices):
        GOOGLE_SHEET = "gsheet", "Google Sheets"
        XML_FEED = "xml", "XML-фід постачальника"
        CSV_FILE = "csv", "CSV/XLSX"

    code = models.SlugField("Код", max_length=60, unique=True)  # "complex-main", "supplier-alpha"
    # ⚠️ unique=True вже створює індекс — db_index=True поруч НЕ додавати (індекси-дублі).
    name = models.CharField("Назва", max_length=160)
    kind = models.CharField("Тип", max_length=10, choices=Kind.choices, default=Kind.GOOGLE_SHEET)

    is_primary = models.BooleanField("Власний склад Complex", default=False)
    is_active = models.BooleanField("Активне", default=True)
    priority = models.SmallIntegerField("Пріоритет", default=100)
    # МЕНШИЙ priority виграє. Реалізується в project_offers() (SYNC.md §3):
    #   DISTINCT ON (sku) ... ORDER BY sku, ps.priority, ps.is_primary DESC, o.id

    # --- дефолти, коли даних немає в прайсі ---
    default_availability = models.CharField(
        "Наявність за замовчуванням",
        max_length=16,
        choices=Availability.choices,
        default=Availability.IN_STOCK,
    )
    # ⚠️ Колонка «К-сть» — ОПЦІЙНА (SYNC.md §1.5.1: клієнтський формат листа її не має).
    #    Немає колонки qty → availability = default_availability.
    default_lead_days = models.PositiveSmallIntegerField(
        "Термін під замовлення (днів)", null=True, blank=True
    )
    price_markup_percent = models.DecimalField(
        "Націнка, %", max_digits=5, decimal_places=2, default=Decimal("0")
    )
    overwrite_names = models.BooleanField("Дозволити перезапис назв товарів", default=False)
    # Тумблер-виняток із SYNC.md §0: за замовчуванням Product.name — власність адмінки,
    # синк пише його ТІЛЬКИ при створенні товару.

    spreadsheet_id = models.CharField("ID Google-таблиці", max_length=120, blank=True)
    feed_url = models.URLField("URL фіда", blank=True)

    # --- ЗАПОБІЖНИКИ (SYNC.md §2, крок 6) ---
    guard_min_rows_ratio = models.DecimalField(
        "Мін. частка рядків від живих товарів",
        max_digits=3,
        decimal_places=2,
        default=Decimal("0.50"),
    )
    guard_max_deactivate_pct = models.DecimalField(
        "Макс. % деактивації за прогін", max_digits=5, decimal_places=2, default=Decimal("20.00")
    )
    guard_max_median_price_pct = models.DecimalField(
        "Макс. зсув медіанної ціни, %", max_digits=5, decimal_places=2, default=Decimal("30.00")
    )

    class Meta:
        verbose_name = "Джерело прайсу"
        verbose_name_plural = "Джерела прайсів"
        ordering = ("priority", "id")
        constraints = [
            models.CheckConstraint(
                condition=~Q(default_availability=Availability.ON_ORDER)
                | Q(default_lead_days__isnull=False),
                name="src_on_order_needs_lead_days",
            ),
            # Рівно ОДИН primary-source. На PG14 це звичайний частковий unique-індекс
            # (UniqueConstraint + condition) — nulls_distinct тут не потрібен взагалі.
            models.UniqueConstraint(
                fields=["is_primary"],
                condition=Q(is_primary=True),
                name="uniq_primary_source",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"

    def clean(self) -> None:
        # fail-fast у формі адмінки: інакше bulk_create падає з IntegrityError на КОЖНОМУ
        # батчі, і прогін = FAILED цілком, з діагностикою рівня «порушено constraint».
        if self.default_availability == Availability.ON_ORDER and self.default_lead_days is None:
            raise ValidationError({"default_lead_days": "Обов'язкове для «Під замовлення»"})


class PriceSheet(TimeStampedModel):
    """
    Лист (вкладка) прайсу. Один лист = одна валюта.

    column_map лежить НА ЛИСТІ, а не на джерелі — саме тому два різні формати
    (робочий 9-колонковий і клієнтський 8-колонковий, SYNC.md §1.5.1) описуються
    двома PriceSheet без жодного рядка коду.
    """

    price_source = models.ForeignKey(
        PriceSource, on_delete=models.CASCADE, related_name="sheets", verbose_name="Джерело"
    )
    tab_name = models.CharField("Назва вкладки", max_length=120)  # "UAH" / "USD"
    currency = models.CharField("Валюта", max_length=3, choices=SourceCurrency.choices)

    column_map = models.JSONField("Карта колонок", default=dict)
    # {"sku":"Артикул","name":"Найменування","price":"Ціна","qty":"К-сть","currency":"Валюта",
    #  "category":"Категорія","brand":"Бренд","country":"Країна виробництва","photo":"Фото",
    #  "mpn":"Артикул виробника",   ← ОПЦІЙНИЙ ключ
    #  "old_price":"Стара ціна"}    ← ОПЦІЙНИЙ ключ
    # ⚠️ "qty" — теж ОПЦІЙНИЙ ключ (див. PriceSource.default_availability).
    #
    # ⚠️ ЗНАЧЕННЯ може бути:
    #      "Бренд"                      — один заголовок;
    #      ["Бренд", "Виробник"]        — СИНОНІМИ (перший знайдений виграє);
    #      "#8"                         — ПОЗИЦІЙНО, 8-ма колонка (1-based) — для колонок,
    #                                     у яких заголовок — сміття (див. SpecSheet «Основна»).
    # Незалежно від column_map працює вбудований словник синонімів (sheets.COLUMN_SYNONYMS):
    # реальні листи UAH і USD називають ОДНУ Й ТУ САМУ колонку по-різному
    # («Бренд» vs «Виробник», «Країна виробництва» vs «Країна_виробник»), і ламатись на цьому
    # прогін не має. Зміна заголовків у таблиці правиться в адмінці, БЕЗ деплою.

    # --- ГЕОМЕТРІЯ ЛИСТА (SYNC.md §1.5.2) ---
    # РЕАЛЬНИЙ лист (звірено з таблицею замовника 14.07.2026, OAuth-доступ):
    #   рядок 1    ЗАГОЛОВКИ                                    → header_row
    #   рядок 2    A2 = дата зрізу (Excel serial, напр. 46211)  → метадані, НЕ дані
    #   рядок 3    перша секція («Аксесуари до техніки»)        → рядок-секція
    #   рядок 4    перший рядок даних                           → data_start_row
    # ⚠️ КУРСУ USD у прайсі НЕМАЄ (він жив в ІНШОМУ файлі, «для клієнтів») → rate_cell
    #    порожній, курс береться з адмінки (SiteSettings). Поле лишається: якщо колонка
    #    з курсом колись з'явиться, її вмикають в адмінці, без деплою.
    # Усі чотири поля редагуються в адмінці: постачальник таблиці може вставити рядок
    # і вся геометрія з'їде — це має правитись без деплою.
    header_row = models.PositiveSmallIntegerField("Рядок заголовків", default=1)
    data_start_row = models.PositiveSmallIntegerField("Перший рядок даних", default=4)
    rate_cell = models.CharField("Комірка з курсом USD", max_length=8, default="", blank=True)
    date_cell = models.CharField("Комірка з датою зрізу", max_length=8, default="A2", blank=True)

    is_active = models.BooleanField("Активний", default=True)
    sort_order = models.PositiveSmallIntegerField("Порядок", default=0)
    # Переможець при дублі sku МІЖ листами одного джерела (SYNC.md §2, крок 5):
    # виграє лист з меншим sort_order.

    class Meta:
        verbose_name = "Лист прайсу"
        verbose_name_plural = "Листи прайсу"
        ordering = ("sort_order", "id")
        constraints = [
            models.UniqueConstraint(fields=["price_source", "tab_name"], name="uniq_sheet_tab"),
            models.CheckConstraint(
                condition=Q(data_start_row__gt=F("header_row")),
                name="sheet_data_after_header",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.price_source_id}/{self.tab_name} ({self.currency})"


class SpecSheet(TimeStampedModel):
    """
    Таблиця характеристик — ОКРЕМИЙ файл від прайсу (INPUTS §3.3, SYNC.md §1.5.3).

    Геометрія: фіксовані колонки A..I, а з колонки J (10-та) ідуть ПОВТОРЮВАНІ ТРІЙКИ:
        Назва_Характеристики | Одиниця_виміру | Значення_Характеристики
    Трійки читаються ПОЗИЦІЙНО (їхні заголовки повторюються — шукати за іменем не можна),
    до першої порожньої назви.

    ⚡ Одиниця виміру лежить в ОКРЕМІЙ колонці трійки — тому Unit у каталозі є окремою
    нормалізованою таблицею, а не суфіксом у назві характеристики.
    """

    price_source = models.ForeignKey(
        PriceSource, on_delete=models.CASCADE, related_name="spec_sheets", verbose_name="Джерело"
    )
    spreadsheet_id = models.CharField("ID Google-таблиці", max_length=120)
    tab_name = models.CharField("Назва вкладки", max_length=120)  # напр. "Гривнева"

    column_map = models.JSONField("Карта колонок", default=dict)
    # {"sku":"Артикул","name":"Найменування","country":"Країна-виробник",
    #  "package_dims":"Розміри в упаковці","image":"Зображення","category":"Категорія",
    #  "brand":"Виробник","mpn":"Артикул виробника",
    #  "spec_triplet_start":"Назва_Характеристики"}
    #  ↑ "spec_triplet_start" — це ЛИШЕ ЗВІРКА заголовка колонки spec_triplet_start_col:
    #    не збігся → прогін FAILED, голосно. Самі трійки читаються позиційно.

    header_row = models.PositiveSmallIntegerField("Рядок заголовків", default=1)
    data_start_row = models.PositiveSmallIntegerField("Перший рядок даних", default=2)
    spec_triplet_start_col = models.PositiveSmallIntegerField(
        "Перша колонка трійок (1-based)", default=10
    )
    # 10 = колонка J (INPUTS §3.3). Позиційна константа, редагується в адмінці —
    # додали колонку в таблицю → змінили число, а не код.

    is_active = models.BooleanField("Активний", default=True)

    class Meta:
        verbose_name = "Таблиця характеристик"
        verbose_name_plural = "Таблиці характеристик"
        constraints = [
            models.UniqueConstraint(
                fields=["price_source", "spreadsheet_id", "tab_name"],
                name="uniq_spec_sheet_tab",
            ),
            models.CheckConstraint(
                condition=Q(data_start_row__gt=F("header_row")),
                name="specsheet_data_after_header",
            ),
            models.CheckConstraint(
                condition=Q(spec_triplet_start_col__gte=1),
                name="specsheet_triplet_col_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.spreadsheet_id}/{self.tab_name}"


# ---------------------------------------------------------------------------
# §3.2 SupplierOffer — ключова таблиця (DATA_MODEL §3.2)
# ---------------------------------------------------------------------------


class SupplierOffer(models.Model):
    """
    Пропозиція КОНКРЕТНОГО постачальника по КОНКРЕТНОМУ sku.
    Синк пише ТІЛЬКИ сюди. У Product дані потрапляють через project_offers().

    ЩО ЛАМАЛОСЬ БЕЗ ЦІЄЇ ТАБЛИЦІ (три бомби в одній):
      (а) sku глобально унікальний на Product → два прайси мапляться в ОДИН рядок;
      (б) ON CONFLICT (sku) DO UPDATE не має перевірки priority → виграє той, хто синкнувся
          ОСТАННІМ: прайс постачальника (націнка 15%, «під замовлення 14 днів») перезаписував
          складський товар Complex «в наявності» — прямі збитки, тихо;
      (в) price_source / last_seen_run на Product — ОДИНИЧНІ колонки: після перезапису прогін
          Complex робив filter(price_source=complex) і цього товару ВЖЕ НЕ БАЧИВ → товар ніколи не
          деактивувався; дзеркально — прогін постачальника деактивував товари Complex.

    Тут же лежить сира форензика (raw): чим саме був рядок прайсу в момент прогону.
    """

    id = models.BigAutoField(primary_key=True)
    price_source = models.ForeignKey(
        PriceSource, on_delete=models.CASCADE, related_name="offers", verbose_name="Джерело"
    )
    sku = models.CharField("Артикул", max_length=64, db_index=True)
    sku_is_surrogate = models.BooleanField("Артикул сурогатний", default=False)
    # ↑ True → артикула в прайсі НЕМАЄ («Уточнюється»), sku згенеровано з нормалізованої назви
    #   (services.surrogate_sku). Прапорець їде в Product через проєкцію: саме за ним
    #   adopt_surrogate_skus() згодом упізнає товар, якому замовник нарешті вписав артикул.

    # --- сирі дані прайсу (як прийшло) ---
    name = models.CharField("Найменування", max_length=255, blank=True)
    base_price = models.DecimalField("Ціна в прайсі", max_digits=12, decimal_places=2)
    base_old_price = models.DecimalField(
        "Стара ціна в прайсі", max_digits=12, decimal_places=2, null=True, blank=True
    )
    source_currency = models.CharField("Валюта", max_length=3, choices=SourceCurrency.choices)
    stock_qty = models.IntegerField("Кількість", null=True, blank=True)
    availability = models.CharField("Наявність", max_length=16, choices=Availability.choices)
    order_lead_days = models.PositiveSmallIntegerField(
        "Термін під замовлення (днів)", null=True, blank=True
    )

    category_ext_id = models.CharField("ID категорії з прайсу", max_length=32, blank=True)
    brand_raw = models.CharField("Виробник (сирий)", max_length=160, blank=True)
    country_raw = models.CharField("Країна (сира)", max_length=160, blank=True)
    mpn = models.CharField("Артикул виробника", max_length=64, blank=True)
    photo_urls = ArrayField(
        models.URLField(max_length=1000), default=list, blank=True, verbose_name="Фото"
    )
    raw = models.JSONField("Сирий рядок", default=dict, blank=True)

    # --- службові ---
    is_active = models.BooleanField("Активний", default=True, db_index=True)
    # False = оффер зник з прайсу. НЕ ВИДАЛЯЄМО: потрібен для аудиту й для проєкції
    # (щоб було видно, ЧОМУ товар деактивовано).
    last_seen_run = models.UUIDField("Останній прогін", null=True, blank=True, db_index=True)
    sheet = models.CharField("Лист", max_length=60, blank=True)  # "UAH" / "USD"
    row_number = models.IntegerField("Рядок у таблиці", null=True, blank=True)
    first_seen_at = models.DateTimeField("Перша поява", auto_now_add=True)
    updated_at = models.DateTimeField("Оновлено", auto_now=True)

    class Meta:
        verbose_name = "Пропозиція постачальника"
        verbose_name_plural = "Пропозиції постачальників"
        constraints = [
            # Ключ upsert-а:
            # bulk_create(update_conflicts=True, unique_fields=["price_source", "sku"], ...)
            models.UniqueConstraint(fields=["price_source", "sku"], name="uniq_offer_source_sku"),
            models.CheckConstraint(condition=Q(base_price__gt=0), name="offer_price_positive"),
        ]
        indexes = [
            # деактивація в межах джерела: WHERE price_source = X AND last_seen_run <> run
            models.Index(fields=["price_source", "last_seen_run"], name="offer_run_idx"),
            # проєкція: DISTINCT ON (sku) ... WHERE is_active
            models.Index(fields=["sku", "is_active"], name="offer_sku_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.sku} @ source={self.price_source_id}"


# ---------------------------------------------------------------------------
# §3.4 Журнал прогонів
# ---------------------------------------------------------------------------


class SyncRun(models.Model):
    """
    Журнал прогону.

    id — UUID, бо він тече далі: у PostgreSQL-тригер price_history_trg через
    `SET LOCAL complex.run_id`, у Product.last_seen_run і в SupplierOffer.last_seen_run.
    """

    class Kind(models.TextChoices):
        SHEETS_PRICES = "sheets_prices", "Прайс з Google Sheets"
        SHEETS_SPECS = "sheets_specs", "Характеристики з Google Sheets"
        NP_REFS = "np_refs", "Довідники Нової Пошти"
        HOTLINE_FEED = "hotline_feed", "Генерація фіда Hotline"
        PRICE_RECALC = "price_recalc", "Перерахунок цін"
        ROLLBACK = "rollback", "Відкат прогону"

    class Status(models.TextChoices):
        RUNNING = "running", "Виконується"
        SUCCESS = "success", "Успішно"
        PARTIAL = "partial", "Успішно з помилками"
        FAILED = "failed", "Помилка"
        ABORTED = "aborted", "Зупинено запобіжником"
        SKIPPED = "skipped", "Пропущено (вже виконується)"

    class Trigger(models.TextChoices):
        CRON = "cron", "За розкладом"
        MANUAL = "manual", "Вручну з адмінки"
        API = "api", "API"
        HEAL = "heal", "Самозагоювання"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField("Тип", max_length=20, choices=Kind.choices, db_index=True)
    price_source = models.ForeignKey(
        PriceSource,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="runs",
        verbose_name="Джерело",
    )
    trigger = models.CharField(
        "Тригер", max_length=10, choices=Trigger.choices, default=Trigger.CRON
    )
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sync_runs",
        verbose_name="Запустив",
    )
    dry_run = models.BooleanField("Пробний прогін", default=False)
    forced = models.BooleanField("Застосувати попри попередження", default=False)
    rolled_back_run = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rollbacks",
        verbose_name="Відкочений прогін",
    )

    status = models.CharField(
        "Статус", max_length=10, choices=Status.choices, default=Status.RUNNING, db_index=True
    )
    started_at = models.DateTimeField("Початок", auto_now_add=True, db_index=True)
    finished_at = models.DateTimeField("Кінець", null=True, blank=True)
    duration_ms = models.PositiveIntegerField("Тривалість, мс", null=True, blank=True)

    # --- лічильники ---
    rows_read = models.IntegerField("Прочитано рядків", default=0)
    rows_expected = models.IntegerField("Очікувалось рядків", default=0)
    # З spreadsheets.get — ЖОРСТКА звірка повноти (SYNC.md §2, крок 2): прочитали менше,
    # ніж є в таблиці → FAILED. Інакше «половина прайсу» тихо деактивує половину каталогу.
    products_created = models.IntegerField("Створено товарів", default=0)
    products_updated = models.IntegerField("Оновлено товарів", default=0)
    products_deactivated = models.IntegerField("Знято з наявності", default=0)
    rows_skipped = models.IntegerField("Пропущено рядків", default=0)
    rows_failed = models.IntegerField("Невалідних рядків", default=0)
    rows_duplicated = models.IntegerField("Дублів sku", default=0)
    images_queued = models.IntegerField("Фото в черзі", default=0)
    specs_filled = models.IntegerField("Заповнено характеристик", default=0)
    categories_missing = ArrayField(
        models.CharField(max_length=32),
        default=list,
        blank=True,
        verbose_name="Відсутні категорії",
    )

    guard_report = models.JSONField("Звіт запобіжників", default=dict, blank=True)
    # {"rows": 4100, "alive": 9800, "ratio": 0.42, "min_ratio": 0.5,
    #  "deactivate_pct": 58.2, "median_price_delta_pct": 0.0}
    #  ↑ ЧОМУ прогін ABORTED — з цифрами, а не «спрацював запобіжник».
    raw_snapshot_key = models.CharField("Сирий знімок у R2", max_length=200, blank=True)

    stats = models.JSONField("Статистика", default=dict, blank=True)
    traceback = models.TextField("Traceback", blank=True)

    class Meta:
        verbose_name = "Прогін синхронізації"
        verbose_name_plural = "Прогони синхронізації"
        ordering = ("-started_at",)
        indexes = [
            models.Index(fields=["kind", "-started_at"], name="run_kind_idx"),
            models.Index(fields=["status", "-started_at"], name="run_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.started_at:%Y-%m-%d %H:%M} [{self.status}]"


class SyncLogEntry(models.Model):
    """
    По-рядковий журнал прогону.

    Ретеншн (SYNC.md §3.4): WARN/ERROR — завжди; CREATED / DEACTIVATED / DUPLICATE_SKU —
    завжди; PRICE_CHANGED — НЕ пишемо взагалі (це робота тригера catalog.PriceHistory);
    решту INFO — не пишемо. purge_sync_logs — 90 днів, БАТЧАМИ по 10k з комітами
    (звичайний DELETE на мільйонах рядків = довга транзакція, bloat, autovacuum-шторм).
    """

    class Level(models.TextChoices):
        INFO = "info", "Інфо"
        WARN = "warn", "Попередження"
        ERROR = "error", "Помилка"

    class Action(models.TextChoices):
        CREATED = "created", "Створено"
        UPDATED = "updated", "Оновлено"
        DEACTIVATED = "deactivated", "Знято з наявності"
        SKIPPED = "skipped", "Пропущено"
        INCOMPLETE = "incomplete", "Неповний товар (сховано з сайту)"
        # ↑ Рядок-чернетка: немає артикула та/або фото. Це НОРМАЛЬНИЙ стан даних, а не збій:
        #   рівень WARN, НЕ ERROR, і прогін від нього не стає PARTIAL.
        SKU_ADOPTED = "sku_adopted", "Артикул з'явився — товар упізнано"
        # ↑ Сурогатний AUTO-ключ замінено на справжній артикул У ТОМУ САМОМУ товарі.
        #   Рядок у журналі — єдиний доказ, що дубль НЕ створився.
        DUPLICATE_SKU = "duplicate_sku", "Дубль артикула"
        LOW_PRIORITY = "low_priority", "SKU належить пріоритетнішому джерелу"
        CATEGORY_MISSING = "category_missing", "Немає категорії"
        CATEGORY_REMAPPED = "category_remapped", "Категорію підібрано"
        BRAND_CREATED = "brand_created", "Створено бренд"
        ATTR_CREATED = "attr_created", "Створено характеристику"
        SPEC_FILLED = "spec_filled", "Заповнено характеристики"
        IMAGE_FAILED = "image_failed", "Фото не завантажилось"
        IMAGE_PURGED = "image_purged", "Прибрано застаріле фото"
        PRICE_CHANGED = "price_changed", "Змінилась ціна"
        ROW_INVALID = "row_invalid", "Невалідний рядок"

    id = models.BigAutoField(primary_key=True)
    run = models.ForeignKey(
        SyncRun, on_delete=models.CASCADE, related_name="entries", verbose_name="Прогін"
    )
    level = models.CharField("Рівень", max_length=6, choices=Level.choices, default=Level.INFO)
    action = models.CharField("Дія", max_length=20, choices=Action.choices)
    sheet = models.CharField("Лист", max_length=60, blank=True)
    row_number = models.IntegerField("Рядок", null=True, blank=True)
    sku = models.CharField("Артикул", max_length=64, blank=True, db_index=True)
    product = models.ForeignKey(
        "catalog.Product",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sync_log_entries",
        verbose_name="Товар",
    )
    message = models.TextField("Повідомлення", blank=True)
    payload = models.JSONField("Дані", default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Запис журналу"
        verbose_name_plural = "Журнал синхронізації"
        ordering = ("-created_at", "-id")
        indexes = [
            # «показати тільки помилки прогону» — головний запит адмінки
            models.Index(fields=["run", "level"], name="log_run_level_idx"),
            models.Index(fields=["sku", "-created_at"], name="log_sku_idx"),
        ]

    def __str__(self) -> str:
        return f"[{self.level}] {self.action} {self.sku}".strip()


# ---------------------------------------------------------------------------
# §3.3 ProductPriceSnapshot — ВІДКАТ (SYNC.md §5)
# ---------------------------------------------------------------------------


class DataIssue(models.Model):
    """
    ПРОБЛЕМИ В ДАНИХ ПРАЙСУ — у вигляді, який можна віддати замовнику.

    Навіщо окрема модель, якщо є `SyncLogEntry`: журнал синку — інструмент РОЗРОБНИКА
    (сотні рядків, коди, payload). Замовник його не відкриє ніколи. Його слова:

        «І може таке бути, що я десь провтикав і два товари будуть з одним артикулом.
         Якщо тобі це відобразиться десь, скажи плз, я виправлю»

    Йому потрібен СПИСОК: що саме зламано, В ЯКОМУ ЛИСТІ, В ЯКОМУ РЯДКУ і що зробити.
    Звідси `sheet` + `row_number` як обов'язкові поля і `message` людською мовою, а не код.
    Звідси ж CSV-експорт (`manage.py data_issues --csv`): файл кидається в месенджер,
    відкривається в Google Sheets — і замовник іде правити рядок за рядком.

    Пишеться ПОЗА транзакцією запису (як і SyncLogEntry) — тому виживає в dry-run:
    саме заради цього списку dry-run і запускають.
    """

    class Kind(models.TextChoices):
        DUPLICATE_SKU = "duplicate_sku", "Дубль артикула"
        MISSING_SKU = "missing_sku", "Немає артикула"
        MISSING_PHOTO = "missing_photo", "Немає фото"
        MISSING_CATEGORY = "missing_category", "Немає категорії"
        # ↑ Колонка «Категорія» ПОРОЖНЯ (або плейсхолдер «Уточнюється») — НЕ плутати з
        #   UNKNOWN_CATEGORY: там ID Є, але його немає в довіднику. Тут ID НЕМАЄ ВЗАГАЛІ.
        #   На РЕАЛЬНОМУ прайсі (14.07.2026) саме це — 353 товари, які тихо осідали в
        #   «__unmapped__» і НЕ потрапляли в жоден звіт замовнику: рядок валідний, sku є,
        #   фото є — просто категорія порожня. Найбільша прогалина звіту DataIssue.
        UNKNOWN_CATEGORY = "unknown_category", "Невідома категорія"
        SPEC_NOT_FOUND = "spec_not_found", "Немає в таблиці характеристик"
        NAME_CHANGED = "name_changed", "Назву змінено в прайсі (на сайті стара)"
        # ↑ Назва товару — ВЛАСНІСТЬ АДМІНКИ (SYNC.md §0): синк пише її лише при СТВОРЕННІ
        #   товару, далі не чіпає, якщо на джерелі не ввімкнено `overwrite_names`. Так
        #   задумано — інакше кожен прогін затирав би ручні правки контент-менеджера.
        #   АЛЕ мовчати про розбіжність не можна: замовник перейменував товар у прайсі,
        #   на сайті лишилась стара назва — і він про це не дізнається НІЯК. Тепер
        #   дізнається: рядок у звіті з обома назвами й підказкою, що робити.

    id = models.BigAutoField(primary_key=True)
    run = models.ForeignKey(
        SyncRun, on_delete=models.CASCADE, related_name="issues", verbose_name="Прогін"
    )
    kind = models.CharField("Тип проблеми", max_length=20, choices=Kind.choices, db_index=True)
    sheet = models.CharField("Лист", max_length=60, blank=True)
    row_number = models.IntegerField("Рядок у таблиці", null=True, blank=True)
    # ↑ Лист + номер рядка — НЕ «для повноти». Без них замовник не знайде рядок у своїй
    #   таблиці, і весь звіт перетворюється на «десь щось не так».
    sku = models.CharField("Артикул", max_length=64, blank=True, db_index=True)
    name = models.CharField("Назва товару", max_length=255, blank=True)
    message = models.TextField("Що зробити", blank=True)
    payload = models.JSONField("Деталі", default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Проблема в даних"
        verbose_name_plural = "Проблеми в даних (для замовника)"
        ordering = ("kind", "sheet", "row_number", "id")
        indexes = [models.Index(fields=["run", "kind"], name="issue_run_kind_idx")]

    def __str__(self) -> str:
        where = f"{self.sheet}:{self.row_number}" if self.row_number else self.sheet
        return f"[{self.get_kind_display()}] {where} {self.sku} {self.name}".strip()


class ProductPriceSnapshot(models.Model):
    """
    Знімок стану товарів ПЕРЕД проєкцією прогону. COPY 10k рядків = мілісекунди.

    Навіщо: прогін може ПРОЙТИ всі запобіжники і все одно зіпсувати каталог.
    Класика — «зсунули колонку К-сть»: рядків стільки ж, guard мовчить, медіанна ціна та сама,
    а 10 000 товарів стали OUT_OF_STOCK. Без знімка відкоту не існує в принципі.

    Відкат (SYNC.md §5) чіпає ЛИШЕ товари, у яких Product.synced_at досі дорівнює мітці цього
    прогону — тобто тих, кого після прогону ніхто не змінював (ні менеджер, ні наступний синк).
    Ретеншн — 30 днів (purge_snapshots).
    """

    id = models.BigAutoField(primary_key=True)
    run = models.ForeignKey(
        SyncRun, on_delete=models.CASCADE, related_name="snapshots", verbose_name="Прогін"
    )
    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.CASCADE,
        db_constraint=False,
        related_name="price_snapshots",
        verbose_name="Товар",
    )
    # db_constraint=False: знімок пишеться масовим INSERT ... SELECT з catalog_product —
    # перевіряти FK на кожному з 10k рядків немає сенсу (джерело рядків і є Product).
    # Каскад лишається логічним (Django), фізичного FK у БД немає.

    old_base_price = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    old_price = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    old_old_price = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    old_source_currency = models.CharField(max_length=3, blank=True)
    old_markup_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    old_usd_rate_used = models.DecimalField(max_digits=10, decimal_places=4, null=True)
    old_availability = models.CharField(max_length=16, blank=True)
    old_order_lead_days = models.PositiveSmallIntegerField(null=True)
    old_stock_qty = models.IntegerField(null=True)
    old_synced_at = models.DateTimeField(null=True)

    class Meta:
        verbose_name = "Знімок цін (для відкату)"
        verbose_name_plural = "Знімки цін (для відкату)"
        indexes = [models.Index(fields=["run", "product"], name="snap_run_idx")]

    def __str__(self) -> str:
        return f"snapshot run={self.run_id} product={self.product_id}"


# ---------------------------------------------------------------------------
# §3.5 UsdRateChange
# ---------------------------------------------------------------------------


class UsdRateChange(TimeStampedModel):
    """
    Історія зміни курсу USD і правила округлення.

    ⚠️ Рядок пишеться ТІЛЬКИ якщо значення реально змінилось (diff у SiteSettings.save()).
    Інакше post_save на синглтоні спрацьовує на БУДЬ-ЯКОМУ збереженні (правка телефону,
    SEO-опису) — і запускає важкий recalc усього каталогу + сміттєвий рядок з old == new.
    """

    old_rate = models.DecimalField("Старий курс", max_digits=10, decimal_places=4, null=True)
    new_rate = models.DecimalField("Новий курс", max_digits=10, decimal_places=4, null=True)
    old_rounding = models.CharField("Старе округлення", max_length=6, blank=True)
    new_rounding = models.CharField("Нове округлення", max_length=6, blank=True)
    # Округлення теж тригерить recalc, причому scope="all" (а не тільки USD-товари):
    # інакше половина каталогу лишиться в старому округленні до наступного синку.
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="usd_rate_changes",
        verbose_name="Змінив",
    )
    products_recalculated = models.IntegerField("Перераховано товарів", default=0)
    run = models.ForeignKey(
        SyncRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rate_changes",
        verbose_name="Прогін",
    )

    class Meta:
        verbose_name = "Зміна курсу USD"
        verbose_name_plural = "Зміни курсу USD"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.old_rate} → {self.new_rate}"
