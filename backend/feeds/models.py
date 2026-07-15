"""
Фіди — моделі.

Зона відповідальності (ARCHITECTURE.md §1):
    FeedArtifact, генератор Hotline XML

Джерела: DATA_MODEL.md §8, INTEGRATIONS.md §2, docs/research/HOTLINE.md, ADR-013 / ADR-026.

────────────────────────────────────────────────────────────────────────────
ЧОМУ ПОТРІБНА ІСТОРІЯ АРТЕФАКТІВ, А НЕ ПРОСТО ФАЙЛ НА ДИСКУ
────────────────────────────────────────────────────────────────────────────
Фід віддається за ПОСТІЙНИМ URL `https://complex.ua/feeds/hotline.xml` (Hotline: без авторизації,
без редіректів, без JS). Тобто «поточна версія» — це стан, а не файл: генерація 4×/добу
переписує один і той самий шлях. `FeedArtifact` дає:
  • `is_current` — яка саме генерація зараз лежить за постійним URL (рівно одна на kind,
    гарантовано частковим unique-обмеженням);
  • історію — з чим порівняти, коли Hotline каже «у вас зникло 800 товарів»;
  • `skipped_reasons` — ЧОМУ товар не потрапив у фід (без цього діагностика зводиться до
    «у фіді 200 товарів замість 3000, і невідомо чому»).

🔴 Фід публікується 7 днів. Не оновили — публікація ПРИПИНЯЄТЬСЯ і товари зникають з майданчика.
   Тому beat: `generate_hotline_feed` (6/14/22) + `heal_hotline_feed` (щогодини): якщо
   `is_current` старший 24 год АБО файлу на диску немає — регенерація + алерт.
   Файл лежить у named volume (не в шарі контейнера!), інакше `compose up -d` його знищує.

────────────────────────────────────────────────────────────────────────────
HotlineCategory — рубрикатор Hotline
────────────────────────────────────────────────────────────────────────────
🔴 КООРДИНАЦІЯ З АГЕНТОМ `catalog`: за INTEGRATIONS §2.5 зв'язок з рубрикатором робиться
   FK `catalog.Category.hotline_category → feeds.HotlineCategory` (nullable, автокомпліт
   в адмінці), а НЕ рядком `hotline_category_name`, який лишився в DATA_MODEL §2.1 з ранньої
   версії. Рядок ламається від першої ж описки в назві, а зв'язок з Hotline будується по
   ТОЧНІЙ назві листової категорії — тобто описка = категорія мовчки випадає з фіда.
   Якщо в `catalog` уже є CharField — його треба замінити на FK окремою міграцією.


⚠️ ЦІЛЬОВА БАЗА — PostgreSQL 14 (не 15/16). Міграції МУСЯТЬ застосуватись на PG14.

  1. UniqueConstraint(..., nulls_distinct=False) → PG15. `uniq_current_feed` зроблено
     ЧАСТКОВИМ unique (`condition=Q(is_current=True)`) — це працює на PG14 (ADR-016).

  2. GeneratedField → на PG14 лише STORED з IMMUTABLE-виразу. `size_bytes` / `items_count`
     пише генератор після atomic-write файлу, а не БД.

  3. MERGE (SQL) → PG15. Upsert рубрикатора — INSERT ... ON CONFLICT (path).
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from core.models import TimeStampedModel


class FeedArtifact(TimeStampedModel):
    """Одна ЗГЕНЕРОВАНА версія фіда."""

    class Kind(models.TextChoices):
        HOTLINE_XML = "hotline_xml", "Hotline XML"

    kind = models.CharField("Тип", max_length=16, choices=Kind.choices, db_index=True)

    file = models.FileField("Файл", upload_to="feeds/")
    # Named volume (віддає Caddy) + копія в R2. Запис — atomic write + rename: бот Hotline
    # не має спіймати напівзаписаний файл.

    items_count = models.IntegerField("Товарів у фіді", default=0)
    skipped_count = models.IntegerField("Пропущено", default=0)
    skipped_reasons = models.JSONField("Причини пропуску", default=dict, blank=True)
    # {"no_hotline_category": 12, "inactive": 4, "no_price": 1, "no_image": 3, "out_of_stock": 41}
    # ⚡ БЕЗ ключа "no_mpn" — див. no_mpn_count нижче.

    no_mpn_count = models.IntegerField("Без артикула виробника", default=0)
    # 🔴 ЛІЧИЛЬНИК, а НЕ причина скіпу (ADR-013, INTEGRATIONS §2.3).
    #   У v1 порожній `mpn` був умовою скіпу, а `mpn` НІЧИМ не заповнювався (колонки «Артикул
    #   виробника» немає ні в прайсі, ні в SpecSheet) → на старті фід був би ПОРОЖНІЙ на 100%.
    #   За специфікацією Hotline `<code>` умовно обов'язковий: фід без нього валідний, товари
    #   просто гірше мержаться з картками. Тому: `<code>` віддаємо лише якщо mpn != "",
    #   а цей лічильник показує розмір проблеми і живить фільтр в адмінці.

    default_dims_count = models.IntegerField("Товарів на дефолтних габаритах", default=0)
    # ⚡ INTEGRATIONS §1.7: категорія, де >20% товарів на дефолтних габаритах, — це категорія,
    #   де магазин СИСТЕМНО недоплачує за доставку. Метрика має бути виміряна, а не «на око».

    size_bytes = models.BigIntegerField("Розмір, байт", default=0)
    generated_at = models.DateTimeField("Згенеровано", auto_now_add=True, db_index=True)
    duration_ms = models.PositiveIntegerField("Тривалість, мс", null=True, blank=True)

    is_current = models.BooleanField("Поточний", default=False, db_index=True)
    # ⚡ Рівно ОДИН current на kind — гарантія частковим unique (нижче). Саме ця версія лежить
    #   за постійним URL. Решта — історія (для порівняння і відкату).

    run = models.ForeignKey(
        "sync.SyncRun",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="feed_artifacts",
    )
    # SyncRun.Kind.HOTLINE_FEED — генерація фіда це теж прогін (лог, тривалість, помилки).
    error = models.TextField("Помилка", blank=True)

    class Meta:
        verbose_name = "Артефакт фіда"
        verbose_name_plural = "Артефакти фідів"
        ordering = ("-generated_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["kind"],
                condition=Q(is_current=True),
                name="uniq_current_feed",
            ),
            # ⚡ ЧАСТКОВИЙ unique (PG14-сумісний, ADR-016). Гарантує, що «поточна версія» — одна.
            # ⚠️ Наслідок для коду: перемикання поточного артефакту — ДВОКРОКОВЕ і в одній
            #    транзакції (частковий unique-індекс НЕ може бути DEFERRABLE):
            #        FeedArtifact.objects.filter(kind=k, is_current=True).update(is_current=False)
            #        new.is_current = True; new.save(update_fields=["is_current"])
            #    Інакше другий INSERT впаде з IntegrityError. Робити ТІЛЬКИ через
            #    feeds/services/publish.py::set_current().
        ]
        indexes = [models.Index(fields=["kind", "-generated_at"], name="feed_kind_idx")]

    def __str__(self) -> str:
        mark = " (поточний)" if self.is_current else ""
        return f"{self.get_kind_display()} · {self.items_count} товарів{mark}"


class HotlineCategory(TimeStampedModel):
    """Вузол рубрикатора Hotline (`hotline_tree_uk.csv`, ~1 223 рядки, 4 рівні).

    Синк: `feeds.tasks.sync_hotline_tree` раз на тиждень тягне
    `https://hotline.ua/download/hotline/hotline_tree_uk.csv`.

    🔴 ПАСТКИ ФАЙЛА (INTEGRATIONS §2.5, перевірено live):
      • кодування — **windows-1251**, не UTF-8 → `body.decode("cp1251")`;
      • це НЕ CSV: дерево з відступами, і **кількість провідних `;` = глибина**;
      • ID категорій у файлі **немає** — тільки назви. Зв'язок з фідом робиться по ТОЧНІЙ
        назві ЛИСТОВОЇ категорії. Звідси `path` як ключ upsert'у і `is_leaf`.

    У `<category><name>` фіда йде назва З РУБРИКАТОРА, а `parentId` будується з ДЕРЕВА
    РУБРИКАТОРА — не з нашої маркетингової структури категорій.
    """

    path = models.CharField("Шлях", max_length=500, unique=True)
    # "Побутова техніка/Велика побутова техніка/Холодильники" — ключ upsert'у.
    # unique=True вже створює індекс — db_index=True НЕ додавати (індекси-дублі).
    name = models.CharField("Назва", max_length=200)
    # ⚠️ НЕ перекладна: це рядок ЧУЖОГО каталогу, який має збігатися байт-у-байт.
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children"
    )
    depth = models.PositiveSmallIntegerField("Глибина", default=0)
    is_leaf = models.BooleanField("Листова", default=False, db_index=True)
    # ⚡ Тільки листові можна ставити в Category.hotline_category: фід з нелистовою категорією
    #   Hotline не приймає.

    last_seen_run = models.UUIDField("Останній прогін", null=True, blank=True, db_index=True)
    is_active = models.BooleanField("Активна", default=True, db_index=True)
    # Зникла з рубрикатора → is_active=False, НЕ DELETE: на неї посилаються наші категорії
    # (FK з catalog), і мовчазне видалення вибило б їхні товари з фіда.

    class Meta:
        verbose_name = "Категорія Hotline"
        verbose_name_plural = "Рубрикатор Hotline"
        ordering = ("path",)
        indexes = [
            models.Index(fields=["is_leaf", "is_active"], name="hl_cat_leaf_idx"),
            models.Index(fields=["parent", "name"], name="hl_cat_parent_idx"),
        ]

    def __str__(self) -> str:
        return self.path
