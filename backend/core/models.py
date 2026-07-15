"""
Ядро — моделі.

Зона відповідальності (ARCHITECTURE.md §1):
    TimeStampedModel, SEOMixin, SingletonModel, SiteSettings, WorkingHours.

⚠️ ЦІЛЬОВА БАЗА — PostgreSQL 14 (не 15/16). Міграції МУСЯТЬ застосуватись на PG14.
Що з Django 5 тут НЕ ПРАЦЮЄ і чим це замінювати:

  1. UniqueConstraint(..., nulls_distinct=False)  →  потребує PG15. На PG14 NULL-и завжди
     розрізняються, тому «унікальність з урахуванням NULL» робиться ДВОМА частковими
     обмеженнями (ADR-016) — див. catalog.ProductAttributeValue.

  2. GeneratedField  →  на PG14 можливі ЛИШЕ STORED-колонки і ЛИШЕ з IMMUTABLE-виразів.
     Ані now(), ані наша complex_price_uah() (яка бере курс ззовні), ані search_vector зі
     стемером під це не підходять. Тому:
         ціна          → SQL-функція complex_price_uah() в UPDATE-ах проєкції (SYNC.md §3);
         search_vector → звичайна SearchVectorField + оновлення в rebuild_product_denorm;
         історія цін   → PG-тригер (ADR-006), а не Python.

  3. MERGE (SQL) → PG15. Використовуємо INSERT ... ON CONFLICT (він у PG14 є).

Розширення (pg_trgm, btree_gin, unaccent), TS-конфіги `uk` / `ru_complex`, функція
complex_price_uah() і sequence order_number_seq створюються міграцією
core/migrations/0001_extensions.py (ADR-022): init-скрипт образу postgres виконується
рівно один раз і рівно в $POSTGRES_DB, тому тестова база CI з нього не отримує нічого.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, ClassVar, Self

from django.contrib.postgres.fields import ArrayField
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import models, transaction

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Абстрактні базові моделі
# ---------------------------------------------------------------------------
class TimeStampedModel(models.Model):
    """Базовий клас усіх моделей, крім чистих журналів (PriceHistory, SyncLogEntry…).

    ⚠️ updated_at МАЄ db_index: по ньому ходять sitemap, ISR-ревалідація і heal-задачі
    (heal_denorm шукає `denorm_at < updated_at`). Без індексу це seq scan по всьому
    каталогу кожні 5 хвилин.
    """

    created_at = models.DateTimeField("Створено", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("Оновлено", auto_now=True, db_index=True)

    class Meta:
        abstract = True


class SEOMixin(models.Model):
    """SEO-поля. Усі три — перекладні (реєструються у <app>/translation.py)."""

    seo_title = models.CharField("SEO-заголовок", max_length=200, blank=True)  # [tr]
    seo_description = models.CharField("SEO-опис", max_length=400, blank=True)  # [tr]
    og_image = models.ImageField("OG-зображення", upload_to="seo/", blank=True)  # [tr]

    class Meta:
        abstract = True


class SingletonModel(models.Model):
    """Синглтон: рядок з pk=1 існує завжди і рівно один.

    get_solo() кешується в Redis, інвалідація — у save(). Рядок створюється лінивим
    get_or_create, тому окрема data-міграція не потрібна.
    """

    CACHE_KEY: ClassVar[str] = "core:singleton"
    CACHE_TTL: ClassVar[int] = 60 * 60

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete(self.CACHE_KEY)

    def delete(self, *args: Any, **kwargs: Any) -> None:
        raise ValidationError("Синглтон не видаляється.")

    @classmethod
    def get_solo(cls) -> Self:
        obj = cache.get(cls.CACHE_KEY)
        if obj is None:
            obj, _ = cls.objects.get_or_create(pk=1)
            cache.set(cls.CACHE_KEY, obj, cls.CACHE_TTL)
        return obj

    @classmethod
    def invalidate(cls) -> None:
        cache.delete(cls.CACHE_KEY)


# ---------------------------------------------------------------------------
# Глобальні налаштування магазину (DATA_MODEL §7)
# ---------------------------------------------------------------------------
class SiteSettings(SingletonModel):
    """Єдиний рядок налаштувань магазину."""

    CACHE_KEY: ClassVar[str] = "core:sitesettings"

    class Rounding(models.TextChoices):
        NONE = "none", "Без округлення (до копійки)"
        UNIT = "unit", "До гривні"
        TEN = "ten", "До 10 грн"
        NINE = "nine", "До 9 (…99, …999)"

    # --- ціноутворення (ADR-005) ---
    usd_rate = models.DecimalField(
        "Курс USD", max_digits=10, decimal_places=4, default=Decimal("41.5000")
    )
    usd_rate_updated_at = models.DateTimeField("Курс оновлено", null=True, blank=True)
    usd_rate_auto_nbu = models.BooleanField("Тягнути курс з НБУ", default=False)
    price_rounding = models.CharField(
        "Округлення ціни", max_length=6, choices=Rounding.choices, default=Rounding.UNIT
    )
    # ⚠️ price_rounding — це аргумент `rule` SQL-функції complex_price_uah(). Формула ціни
    #    існує РІВНО в одному місці (ADR-005), тому міняти рядки цих choices можна тільки
    #    разом з функцією (core/migrations/0001_extensions.py). Python-двійник
    #    catalog/services/pricing.py існує лише для preview і покритий parity-тестом.

    # --- габарити: ОСТАННІЙ рівень фолбеку effective_dims() (ADR-021) ---
    # Ланцюг: характеристики товару → Product.package_* → Category.default_* → сюди.
    default_weight_kg = models.DecimalField(
        "Вага за замовчуванням, кг", max_digits=7, decimal_places=3, default=Decimal("5")
    )
    default_width_cm = models.DecimalField(
        "Ширина за замовчуванням, см", max_digits=6, decimal_places=1, default=Decimal("40")
    )
    default_height_cm = models.DecimalField(
        "Висота за замовчуванням, см", max_digits=6, decimal_places=1, default=Decimal("40")
    )
    default_depth_cm = models.DecimalField(
        "Глибина за замовчуванням, см", max_digits=6, decimal_places=1, default=Decimal("40")
    )

    # --- синхронізація ---
    sync_enabled = models.BooleanField("Синхронізація увімкнена", default=True)  # kill-switch
    sync_hours = ArrayField(
        models.PositiveSmallIntegerField(),
        verbose_name="Години запуску синку прайсу",
        default=list,
        blank=True,
        help_text="Напр. 8, 12, 16, 20. Порожньо — розклад beat не чіпаємо.",
    )
    # ⚠️ ДЖЕРЕЛО ПРАВДИ розкладу — PeriodicTask (django-celery-beat, DatabaseScheduler):
    #    beat читає ТІЛЬКИ його. Це поле — людський тумблер в адмінці; core.tasks
    #    .apply_sync_schedule() проєктує його в CrontabSchedule задачі "sync-prices".
    #    Порожній список = не чіпати beat взагалі (розклад правиться напряму в PeriodicTask).
    sync_alert_emails = ArrayField(
        models.EmailField(), verbose_name="E-mail для алертів", default=list, blank=True
    )
    telegram_chat_id = models.CharField(
        "Telegram chat_id для алертів", max_length=40, blank=True
    )  # основний канал алертів (ADR-019), email — резервний

    # --- контакти / самовивіз ---
    phones = ArrayField(
        models.CharField(max_length=20), verbose_name="Телефони", default=list, blank=True
    )
    email = models.EmailField("E-mail", blank=True)
    address = models.CharField("Адреса магазину (самовивіз)", max_length=250, blank=True)  # [tr]
    # ⚠️ Адреса магазину = адреса самовивозу (Ужгород). Структурований варіант з
    #    координатами й графіком — cms.PickupPoint (на нього посилається Order.pickup_point);
    #    тут — те, що показуємо в шапці, підвалі й на «Контактах».
    map_embed_url = models.URLField("URL карти (embed)", blank=True)
    social_links = models.JSONField("Соцмережі", default=dict, blank=True)

    # --- доставка ---
    np_sender_city_ref = models.CharField(
        "CityRef відправника (НП)",
        max_length=36,
        default="e221d627-391c-11dd-90d9-001a92567626",  # Ужгород
    )
    free_shipping_from = models.DecimalField(
        "Безкоштовна доставка від, грн", max_digits=10, decimal_places=2, null=True, blank=True
    )
    courier_uzhhorod_price_note = models.CharField(
        "Примітка про кур'єра по Ужгороду", max_length=120, blank=True
    )  # [tr]

    # --- оплата ---
    payment_provider = models.CharField(
        "Платіжний провайдер", max_length=10, default="liqpay"
    )  # ADR-015/ADR-025: на старті єдиний провайдер — LiqPay
    installment_badge_text = models.CharField(
        "Текст бейджа «Оплата частинами»", max_length=120, blank=True
    )  # [tr]
    installment_provider = models.CharField(
        "Банки-партнери", max_length=40, blank=True
    )  # "Monobank, Privatbank" → <payment type="installment"> у фіді Hotline
    installment_max_period = models.PositiveSmallIntegerField("Макс. платежів", default=24)
    # Product.installment_max_payments перекриває це значення для конкретного товару.
    installment_min_payment = models.DecimalField(
        "Мін. платіж, грн", max_digits=10, decimal_places=2, default=Decimal("300")
    )

    # --- Hotline ---
    hotline_enabled = models.BooleanField("Фід Hotline увімкнено", default=False)
    hotline_firm_id = models.CharField("Hotline firm id", max_length=20, blank=True)
    hotline_firm_name = models.CharField("Hotline firm name", max_length=120, blank=True)
    hotline_feed_generated_at = models.DateTimeField("Фід згенеровано", null=True, blank=True)

    # --- ШІ ---
    ai_monthly_budget_usd = models.DecimalField(
        "Бюджет ШІ на місяць, $", max_digits=8, decimal_places=2, default=Decimal("50")
    )
    ai_spent_this_month_usd = models.DecimalField(
        "Витрачено цього місяця, $", max_digits=8, decimal_places=2, default=Decimal("0")
    )

    # --- SEO ---
    default_seo_title = models.CharField(
        "SEO-заголовок за замовчуванням", max_length=200, blank=True
    )  # [tr]
    default_seo_description = models.CharField(
        "SEO-опис за замовчуванням", max_length=400, blank=True
    )  # [tr]
    default_og_image = models.ImageField(
        "OG-зображення за замовчуванням", upload_to="seo/", blank=True
    )

    class Meta:
        verbose_name = "Налаштування магазину"
        verbose_name_plural = "Налаштування магазину"

    def __str__(self) -> str:
        return "Налаштування магазину"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """DIFF-механізм: recalc запускається ЛИШЕ на реальну зміну курсу / округлення.

        У v1 post_save на синглтоні спрацьовував на будь-якому збереженні (правка телефону,
        SEO-опису) і запускав важкий перерахунок усього каталогу + писав сміттєвий
        UsdRateChange(old == new).

        Зміна price_rounding перераховує ВЕСЬ каталог (scope="all"), не лише USD-товари:
        інакше половина каталогу лишиться в старому округленні до наступного синку.

        `_changed_by_id` виставляє адмінка (ModelAdmin.save_model) — для аудиту UsdRateChange.
        Рядок UsdRateChange створює сама задача recalc_prices (це модель застосунку sync,
        core про sync не знає).
        """
        old = type(self).objects.filter(pk=1).first()
        super().save(*args, **kwargs)

        if old is None:  # перше створення синглтона — перераховувати нічого
            return

        rate_changed = old.usd_rate != self.usd_rate
        rounding_changed = old.price_rounding != self.price_rounding
        if not (rate_changed or rounding_changed):
            return

        scope = "all" if rounding_changed else "usd"
        payload = {
            "scope": scope,
            "old_rate": str(old.usd_rate),
            "new_rate": str(self.usd_rate),
            "old_rounding": old.price_rounding,
            "new_rounding": self.price_rounding,
            "user_id": getattr(self, "_changed_by_id", None),
        }

        def _schedule() -> None:
            # Локальний імпорт: core не залежить від catalog на рівні модуля.
            from catalog.tasks import recalc_prices

            recalc_prices.delay(**payload)

        transaction.on_commit(_schedule)
        # Гарантія проти втраченої on_commit-задачі — beat heal_usd_prices (ADR-011):
        # він добирає товари, де usd_rate_used != SiteSettings.usd_rate.


class WorkingHours(models.Model):
    """Графік роботи (INPUTS §4: ПН–ПТ 09:00–20:00, СБ–НД 10:00–17:00)."""

    WEEKDAYS = [
        (0, "Понеділок"),
        (1, "Вівторок"),
        (2, "Середа"),
        (3, "Четвер"),
        (4, "П'ятниця"),
        (5, "Субота"),
        (6, "Неділя"),
    ]

    weekday = models.PositiveSmallIntegerField("День тижня", choices=WEEKDAYS, unique=True)
    is_day_off = models.BooleanField("Вихідний", default=False)
    open_time = models.TimeField("Відкриття", null=True, blank=True)
    close_time = models.TimeField("Закриття", null=True, blank=True)

    class Meta:
        ordering = ("weekday",)
        verbose_name = "Графік роботи"
        verbose_name_plural = "Графік роботи"
        constraints = [
            # Робочий день без годин — це «магазин працює, але невідомо коли».
            models.CheckConstraint(
                condition=models.Q(is_day_off=True)
                | (models.Q(open_time__isnull=False) & models.Q(close_time__isnull=False)),
                name="wh_open_day_needs_hours",
            ),
        ]

    def __str__(self) -> str:
        if self.is_day_off:
            return f"{self.get_weekday_display()}: вихідний"
        return f"{self.get_weekday_display()}: {self.open_time:%H:%M}–{self.close_time:%H:%M}"
