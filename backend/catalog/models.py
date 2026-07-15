"""
Каталог — моделі.

Зона відповідальності (ARCHITECTURE.md §1):
    Category, Brand, Country, Product, ProductImage, EAV-атрибути, PriceHistory,
    денормалізація (×2 мови), фасети, пошук.

⚠️ ЦІЛЬОВА БАЗА — PostgreSQL 14. Що з Django 5 тут НЕ ПРАЦЮЄ і чим замінено:

  1. UniqueConstraint(nulls_distinct=False) → потребує PG15. Замінено ДВОМА частковими
     unique-обмеженнями (ADR-016): див. ProductAttributeValue.Meta і ProductImage.Meta.

  2. GeneratedField → на PG14 лише STORED і лише з IMMUTABLE-виразів. Тому:
       • ціна          — рахує SQL-функція complex_price_uah() (ADR-005), не колонка;
       • volume_m3     — ВИКИНУТО як GeneratedField (ADR-021), рахує effective_dims();
       • search_vector — звичайна SearchVectorField, наповнює rebuild_product_denorm;
       • історія цін   — PG-тригер (ADR-006), бо масовий UPDATE обходить Python.

  3. MERGE → PG15. Живемо на INSERT ... ON CONFLICT.

⚠️ ВІДХІД ВІД DATA_MODEL §0 — індекси по ПЕРЕКЛАДНИХ колонках (slug_uk, search_vector_uk,
   name_uk/_ru) оголошені ТУТ, у Meta, а не в окремій міграції після initial.
   ЧОМУ: Meta — єдине джерело стану моделі для автодетектора. Індекс/обмеження, що живе лише
   в міграції, автодетектор вважає зайвим і на наступному `makemigrations` ГЕНЕРУЄ МІГРАЦІЮ НА
   ЙОГО ВИДАЛЕННЯ (перевірено на PG14: 0004_remove_category_uniq_cat_slug_in_parent_and_more),
   а `makemigrations --check` у CI падає назавжди. Обіцяна §0 фрагільність не підтвердилась:
   Meta зберігає лише РЯДОК "search_vector_uk", а резолвиться він у системних перевірках —
   тобто вже після modeltranslation.ready().
   Ціна відходу: перше створення GIN іде звичайним CREATE INDEX (на порожній таблиці — нуль
   секунд). Правило §0 «індекси на ЖИВІЙ базі — тільки AddIndexConcurrently + atomic=False»
   лишається в силі для всіх НАСТУПНИХ індексів.

⚠️ НАСЛІДОК: catalog/0001_initial створює GIN з opclasses=["gin_trgm_ops"], тобто ВИМАГАЄ
   розширення pg_trgm. Міграція catalog/0001_initial МУСИТЬ мати в dependencies
   ("core", "0001_extensions") — інакше `migrate` на чистій БД впаде з
   `operator class "gin_trgm_ops" does not exist`.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils.text import slugify

from catalog.enums import Availability, Condition, ProductSource, SourceCurrency
from catalog.services.naming import normalize_model_name
from catalog.validators import validate_icon
from core.models import SEOMixin, TimeStampedModel

log = logging.getLogger(__name__)

__all__ = [
    "Attribute",
    "AttributeGroup",
    "AttributeOption",
    "Availability",
    "Brand",
    "Category",
    "Condition",
    "Country",
    "PriceHistory",
    "Product",
    "ProductAttributeValue",
    "ProductImage",
    "ProductSource",
    "RelatedGroup",
    "RelatedGroupItem",
    "SourceCurrency",
    "Unit",
    "VariantGroup",
    "VariantItem",
]


# ---------------------------------------------------------------------------
# 2.1 Category
# ---------------------------------------------------------------------------
class Category(SEOMixin, TimeStampedModel):
    """Дерево категорій: self-FK + матеріалізований path (ADR-001).

    Без MPTT/treebeard/ltree: дерево — ~59 вузлів глибиною 2, змінюється раз на місяць.
    `path__startswith` дає всіх нащадків одним індексованим запитом — саме на цьому
    будується масове вмикання Hotline на категорію разом з підкатегоріями.
    """

    # Службова категорія-смітник для товарів, чиєї категорії ще немає в дереві.
    UNMAPPED_EXTERNAL_ID: ClassVar[str] = "__unmapped__"

    external_id = models.CharField(
        "ID з прайсу",
        max_length=32,
        unique=True,
        help_text="ID категорії з прайсу / старого сайту, БЕЗ префікса 'c'. Напр. 5609730",
    )
    # ⚠️ unique=True вже створює індекс — db_index=True тут НЕ додавати (індекс-дубль).

    parent = models.ForeignKey(
        "self",
        verbose_name="Батьківська категорія",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    path = models.CharField(max_length=255, db_index=True, editable=False)
    # матеріалізований шлях з external_id: "87727179" або "87727179/5609730"
    depth = models.PositiveSmallIntegerField(default=0, editable=False)
    sort_order = models.PositiveIntegerField("Порядок", default=0, db_index=True)

    name = models.CharField("Назва", max_length=160)  # [tr]
    slug = models.SlugField("Slug", max_length=180)  # [tr] декоративний: роут іде по external_id
    description = models.TextField("Опис", blank=True)  # [tr]

    icon = models.FileField(
        "Міні-емблема", upload_to="categories/icons/", blank=True, validators=[validate_icon]
    )  # мегаменю (INPUTS §4)
    image = models.ImageField("Плитка", upload_to="categories/tiles/", blank=True)

    is_active = models.BooleanField("Активна", default=True, db_index=True)
    show_in_megamenu = models.BooleanField("Показувати в мегаменю", default=True)
    is_service = models.BooleanField(default=False, editable=False)
    # True тільки для службової "__unmapped__" — щоб її не показувати і не рахувати.

    # --- ВІРТУАЛЬНА КАТЕГОРІЯ (INPUTS §5 Q6) ---
    virtual_tokens = ArrayField(
        models.CharField(max_length=80),
        verbose_name="Токени віртуальної категорії",
        default=list,
        blank=True,
    )
    # Непорожньо => категорія НЕ має власних товарів через FK. Її лістинг =
    #   Product.objects.filter(filter_tokens__contains=virtual_tokens, is_active=True)
    # «Уцінка» = virtual_tokens=["cond:2"]; так само «Акції» (["promo:1"]), «Топ продажів» —
    # БЕЗ жодної нової моделі й без дублювання товару у дві категорії.
    # ⚠️ Уцінка — ОЗНАКА товару (Product.condition), а не місце в дереві: той самий холодильник
    #   лишається у «Холодильники» (де його шукають і фільтрують) і паралельно видно в «Уцінка».
    #   Hotline-тег <condition> бере саме Product.condition — категорія тут ні до чого.

    # --- дефолти габаритів УПАКОВКИ (3-й рівень фолбеку effective_dims, ADR-021) ---
    # Використовуються, ЛИШЕ коли Product.package_* порожні (немає рядка в характеристиках).
    default_weight_kg = models.DecimalField(
        "Вага упаковки за замовч., кг", max_digits=7, decimal_places=3, null=True, blank=True
    )
    default_width_cm = models.DecimalField(
        "Ширина упаковки за замовч., см", max_digits=6, decimal_places=1, null=True, blank=True
    )
    default_height_cm = models.DecimalField(
        "Висота упаковки за замовч., см", max_digits=6, decimal_places=1, null=True, blank=True
    )
    default_depth_cm = models.DecimalField(
        "Глибина упаковки за замовч., см", max_digits=6, decimal_places=1, null=True, blank=True
    )

    # --- Hotline (ADR-013) ---
    # ⚠️ РОЗБІЖНІСТЬ У DOCS, знята міграційним агентом: DATA_MODEL §2.1 має тут
    #    `hotline_category_name = CharField`, INTEGRATIONS §2.5 + HOTLINE §350 + ROADMAP §132 —
    #    FK на feeds.HotlineCategory. Обрано FK (ROADMAP прямо арбітрує цей конфлікт):
    #    рядок ламається від першої описки → категорія МОВЧКИ випадає з фіда, а FK на
    #    імпортований рубрикатор дає автокомпліт в адмінці й перевірку на рівні БД.
    #    Рядкове посилання ("feeds.HotlineCategory") — щоб не заводити import catalog→feeds
    #    (feeds уже читає catalog у сервісах фіда).
    hotline_category = models.ForeignKey(
        "feeds.HotlineCategory",
        verbose_name="Категорія Hotline",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="site_categories",
        limit_choices_to={"is_leaf": True, "is_active": True},
        help_text="LEAF-вузол рубрикатора Hotline. Порожньо → товари категорії у фід НЕ йдуть "
        "(лічильник no_hotline_category у FeedArtifact.skipped_reasons).",
    )
    hotline_enabled_default = models.BooleanField(
        "Нові товари — на Hotline",
        default=False,
        help_text="Успадковується НОВИМ товаром при створенні.",
    )
    # ⚠️ Успадковується лише в INSERT-частині upsert-а і НЕ входить в update_fields — інакше
    #   кожен прогін синку затирав би ручне вимкнення hotline_enabled на конкретному товарі,
    #   і категорія «протікала» б у фід нескінченно.

    products_count = models.PositiveIntegerField(default=0, editable=False)  # денорм, Celery

    class Meta:
        ordering = ("sort_order", "id")
        verbose_name = "Категорія"
        verbose_name_plural = "Категорії"
        indexes = [
            models.Index(fields=["parent", "sort_order"], name="cat_parent_idx"),
            models.Index(fields=["is_active", "depth"], name="cat_active_depth_idx"),
            models.Index(fields=["path"], name="cat_path_btree"),  # для path__startswith
        ]
        constraints = [
            # ⚠️ Унікальність slug В МЕЖАХ БАТЬКА, а не глобально: slug декоративний (роут іде по
            #    external_id), а дублі назв підкатегорій («Аксесуари», «Інше» під різними
            #    батьками) — це норма, а не помилка. Автосуфікс — у _ensure_unique_slug().
            #
            # ⚠️ ВІДХІД ВІД DATA_MODEL §0 (свідомий, див. шапку модуля): посилання на перекладну
            #    колонку slug_uk стоїть у Meta, а не в окремій міграції. Причина: Meta — ЄДИНЕ
            #    джерело стану моделі для автодетектора. Якщо тримати обмеження лише в міграції,
            #    наступний `makemigrations` бачить розбіжність «у стані є, в Meta немає» і
            #    ГЕНЕРУЄ МІГРАЦІЮ, ЯКА ЙОГО ВИДАЛЯЄ (перевірено: 0004_remove_...). `makemigrations
            #    --check` у CI при цьому падає назавжди. Порядок ініціалізації тут не страждає:
            #    Meta зберігає лише РЯДОК "slug_uk", а резолвиться він у системних перевірках —
            #    тобто вже після modeltranslation.ready().
            models.UniqueConstraint(fields=["parent", "slug_uk"], name="uniq_cat_slug_in_parent"),
        ]

    def __str__(self) -> str:
        return self.name

    # -- дерево --------------------------------------------------------------
    def build_path(self) -> str:
        if self.parent_id is None:
            return self.external_id
        return f"{self.parent.path}/{self.external_id}"

    def clean(self) -> None:
        if self.parent_id and self.parent_id == self.pk:
            raise ValidationError({"parent": "Категорія не може бути власним батьком."})
        if self.parent_id and self.pk and self.parent.path.startswith(f"{self.path}/"):
            raise ValidationError({"parent": "Не можна перемістити категорію у власного нащадка."})

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Перерахунок path/depth, каскад по нащадках, автосуфікс slug у межах батька."""
        self._ensure_unique_slug()

        old_path = (
            type(self).objects.filter(pk=self.pk).values_list("path", flat=True).first()
            if self.pk
            else None
        )
        self.path = self.build_path()
        self.depth = self.path.count("/")

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            uf = set(update_fields) | {"path", "depth", "slug"}
            if hasattr(self, "slug_uk"):
                uf.add("slug_uk")
            kwargs["update_fields"] = uf

        super().save(*args, **kwargs)

        if old_path and old_path != self.path:
            self._recompute_descendants(old_path)

    def _ensure_unique_slug(self) -> None:
        """Автосуфікс: «aksesuary» → «aksesuary-2», якщо у того самого батька такий уже є.

        Працюємо зі `slug_uk`: slug — вічний ключ URL, а UK — мова-джерело. getattr з фолбеком —
        на випадок, коли modeltranslation ще не встиг пропатчити модель (виклик з міграції).
        """
        has_tr = hasattr(self, "slug_uk")
        lookup = "slug_uk" if has_tr else "slug"

        base = (getattr(self, "slug_uk", None) if has_tr else None) or self.slug
        if not base:
            base = slugify(getattr(self, "name_uk", None) or self.name)
        if not base:
            return

        candidate, n = base, 1
        siblings = type(self).objects.filter(parent_id=self.parent_id).exclude(pk=self.pk)
        while siblings.filter(**{lookup: candidate}).exists():
            n += 1
            candidate = f"{base}-{n}"

        self.slug = candidate
        if has_tr:
            self.slug_uk = candidate

    def _recompute_descendants(self, old_path: str) -> None:
        """Дерево — десятки вузлів, тому чесний цикл дешевший і зрозуміліший за рекурсивний CTE."""
        prefix = f"{old_path}/"
        for child in type(self).objects.filter(path__startswith=prefix).order_by("path"):
            new_path = self.path + child.path[len(old_path) :]
            type(self).objects.filter(pk=child.pk).update(path=new_path, depth=new_path.count("/"))

    def descendants(self, include_self: bool = True) -> models.QuerySet[Category]:
        """Усі нащадки одним індексованим запитом — база для set_hotline_for_category()."""
        cond = Q(path__startswith=f"{self.path}/")
        if include_self:
            cond |= Q(pk=self.pk)
        return type(self).objects.filter(cond)


# ---------------------------------------------------------------------------
# 2.2 Brand / Country
# ---------------------------------------------------------------------------
# ⚠️ Видалення Brand / Country / AttributeOption ЗАБОРОНЕНЕ в адмінці
#    (has_delete_permission → False). Замість нього — дія «Злити з…»
#    (catalog/services/merge.py::merge_brands): товари переїжджають на dst, aliases зливаються,
#    src видаляється, denorm перебудовується, кеш фасетів інвалідується.
#    ЧОМУ: Product.brand має on_delete=SET_NULL — просте видалення дубля обнулило б бренд у
#    сотень товарів і мовчки вибило б їх з фасета «Виробник».
class Brand(TimeStampedModel):
    name = models.CharField("Назва", max_length=120, unique=True)  # НЕ [tr]: власна назва
    slug = models.SlugField("Slug", max_length=140, unique=True)  # → токен "brand:gorenje"
    logo = models.ImageField("Логотип", upload_to="brands/", blank=True)
    aliases = ArrayField(
        models.CharField(max_length=120),
        verbose_name="Синоніми",
        default=list,
        blank=True,
        help_text='["SHARP", "sharp", "Шарп"] — усе, що приходить з прайсів.',
    )
    is_active = models.BooleanField("Активний", default=True)
    needs_review = models.BooleanField("Потребує перевірки", default=False, db_index=True)
    # True => створено автоматично синком; контент-менеджер має злити дублі.

    class Meta:
        ordering = ("name",)
        verbose_name = "Бренд"
        verbose_name_plural = "Бренди"
        indexes = [GinIndex(fields=["aliases"], name="brand_aliases_gin")]

    def __str__(self) -> str:
        return self.name


class Country(TimeStampedModel):
    code = models.CharField("Код", max_length=2, unique=True)  # ISO-3166-1 alpha-2
    name = models.CharField("Назва", max_length=100)  # [tr]
    slug = models.SlugField("Slug", max_length=120, unique=True)  # → токен "country:polshcha"
    aliases = ArrayField(
        models.CharField(max_length=100), verbose_name="Синоніми", default=list, blank=True
    )
    flag = models.FileField("Прапор", upload_to="countries/", blank=True)
    needs_review = models.BooleanField("Потребує перевірки", default=False, db_index=True)

    class Meta:
        ordering = ("code",)
        verbose_name = "Країна"
        verbose_name_plural = "Країни"
        indexes = [GinIndex(fields=["aliases"], name="country_aliases_gin")]

    def __str__(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# 2.3 Product — центральна модель
# ---------------------------------------------------------------------------
class Product(SEOMixin, TimeStampedModel):
    """Товар.

    id: BigAutoField. Legacy-товари імпортуються з явним pk = старий 10-значний Prom-ID.
    ⚠️ Після імпорту ОБОВ'ЯЗКОВО:
       SELECT setval('catalog_product_id_seq',
                     (SELECT COALESCE(MAX(id),0)+1 FROM catalog_product), false);
       — саме так, а НЕ константою 3e9: legacy-ID цілком можуть бути більші за неї.
    """

    # --- ідентифікація ---
    sku = models.CharField("Артикул", max_length=64, unique=True)
    # ↑ ЄДИНИЙ ключ синхронізації. NOT NULL. Для ручних товарів — "MAN-000123".
    #   unique=True вже дає індекс → db_index тут не потрібен.
    sku_is_surrogate = models.BooleanField(
        "Артикул сурогатний",
        default=False,
        db_index=True,
        help_text="У прайсі в колонці «Артикул» стоїть «Уточнюється» → синк згенерував "
        "сурогатний ключ AUTO-xxxxxxxxxxxx з нормалізованої назви. Щойно замовник впише "
        "справжній артикул, синк оновить sku ЦЬОМУ Ж товару (зіставлення за name_normalized), "
        "а не створить другий.",
    )
    # ⚠️ 237 з 836 рядків реального прайсу не мають артикула (SYNC.md §4.3). Мовчки пропускати
    #   їх не можна — це РЕАЛЬНІ рядки; створювати без ключа теж не можна — нічим оновлювати.

    is_incomplete = models.BooleanField(
        "Неповний товар (чернетка прайсу)",
        default=False,
        db_index=True,
        help_text="Немає артикула та/або фото в прайсі. Такий товар СХОВАНО з сайту "
        "(is_active=False): це рядок-чернетка, який замовник ще заповнює. Щойно в прайсі "
        "з'являться артикул І фото — наступний синк зніме прапорець і покаже товар САМ.",
    )
    # ⚠️ ПРАВИЛО ВИДИМОСТІ (SYNC.md §4.3), слова замовника (14.07.2026):
    #     «Це бо товари не додані в базу, у них нема ні фото ні артикула, нічого крім ціни.
    #      Роби одразу, щоб товар не відображався.»
    #   Критерій повноти = АРТИКУЛ + ФОТО + ЦІНА. Характеристики в нього НЕ входять:
    #     «навіть якщо пусті характеристики, хай карта товару буде пуста з ціною»
    #   — товар без характеристик показується (test_product_without_specs_is_still_created).
    #
    # ⚠️ ЦЕ НЕ ДУБЛЬ is_active. is_active — рішення ЛЮДИНИ (сховати товар руками);
    #   is_incomplete — стан ДАНИХ. Синк гасить is_active лише поки товар неповний і сам
    #   вмикає його назад при переході «неповний → повний». Товар, вимкнений руками в адмінці,
    #   синк не вмикає (проєкція чіпає is_active ТІЛЬКИ на переході стану повноти).
    mpn = models.CharField("Артикул виробника", max_length=64, blank=True, db_index=True)
    # ↑ Hotline <code>. ОПЦІЙНИЙ і НЕ причина скіпу у фіді (ADR-013): у прайсі такої колонки
    #   немає взагалі, тому правило «скіпати товар без mpn» дало б ПОРОЖНІЙ фід.
    barcode = models.CharField("Штрихкод (EAN)", max_length=32, blank=True)

    name = models.CharField("Назва", max_length=255)  # [tr]
    slug = models.SlugField("Slug", max_length=280)  # [tr] декоративний (роут по id)
    description = models.TextField("Опис", blank=True)  # [tr]
    # ⚠️ ОПИС — rich HTML з ВБУДОВАНИМИ зображеннями (INPUTS §2, скрін 171: робот-пилосос з фото
    #   між абзацами). Редактор — TipTap в unfold, картинки → cms.EditorImage.
    #   Опис НЕ фільтрується і НЕ парситься в характеристики; він іде лише в search_vector (вага D).
    #   bleach-allowlist: h2,h3,h4,p,ul,ol,li,strong,em,br,table,tr,td,th,img,figure,figcaption,a
    #   img[src] — ТІЛЬКИ з нашого R2-домену (інакше mixed content і чужий трекінг).
    short_description = models.CharField("Короткий опис", max_length=500, blank=True)  # [tr]

    name_normalized = models.CharField(max_length=255, blank=True, editable=False)
    # ⚠️ РЕЗЕРВНИЙ КЛЮЧ зіставлення прайсу з таблицею характеристик (INPUTS §3.5).
    #   Основний ключ — sku (артикул є в ОБОХ таблицях), але скрипт Артура зшивав таблиці по
    #   назві, і в даних лишились рядки з порожнім/битим артикулом → потрібен фолбек.
    #   Алгоритм — catalog/services/naming.py::normalize_model_name() (порт скрипта 1:1).
    #   Заповнюється в save()/парсері, НІКОЛИ не редагується руками. НЕ [tr]: це ключ, не текст.

    category = models.ForeignKey(
        Category, verbose_name="Категорія", on_delete=models.PROTECT, related_name="products"
    )
    brand = models.ForeignKey(
        Brand, verbose_name="Бренд", null=True, blank=True, on_delete=models.SET_NULL
    )
    country = models.ForeignKey(
        Country, verbose_name="Країна", null=True, blank=True, on_delete=models.SET_NULL
    )

    source_category_ext_id = models.CharField(max_length=32, blank=True, db_index=True)
    # ⚠️ ID категорії з прайсу — синк пише ЗАВЖДИ (це дані прайсу, а не ручний контент).
    #   Без нього товари, що осіли в «__unmapped__», ніколи не переїдуть у категорію, створену
    #   пізніше (post_save на Category → remap_unmapped_products.delay(external_id)).

    # --- ЦІНА (двошарова, ADR-005) ---
    source_currency = models.CharField(
        "Валюта прайсу",
        max_length=3,
        choices=SourceCurrency.choices,
        default=SourceCurrency.UAH,
        db_index=True,
    )
    base_price = models.DecimalField("Ціна з прайсу", max_digits=12, decimal_places=2)
    markup_percent = models.DecimalField("Націнка, %", max_digits=5, decimal_places=2, default=0)
    # ⚠️ ДЕНОРМАЛІЗОВАНА ефективна націнка (пишеться проєкцією з PriceSource; ручні = 0).
    #   Без неї recalc не може дотягнутись до price_source__price_markup_percent в ОДНОМУ
    #   UPDATE: F()-вираз фізично не ходить по FK. Саме на цьому у v1 recalc «губив» націнку.
    price = models.DecimalField("Ціна, грн", max_digits=12, decimal_places=2)
    # ⚠️ db_index свідомо НЕМАЄ: композит (is_active, category, price) його покриває, а окремий
    #   btree по price не використається ніколи.
    base_old_price = models.DecimalField(
        "Стара ціна (у валюті прайсу)", max_digits=12, decimal_places=2, null=True, blank=True
    )
    old_price = models.DecimalField(
        "Стара ціна, грн", max_digits=12, decimal_places=2, null=True, blank=True
    )
    # ⚠️ old_price — ПОХІДНА: рахується тією ж complex_price_uah() з base_old_price. Було: old_price
    #   у грн назавжди → після зростання курсу old_price ставав НИЖЧИМ за price, і картка
    #   показувала «знижку» вгору. CheckConstraint prod_old_price_gt_price це тепер ловить.
    usd_rate_used = models.DecimalField(
        "Курс, за яким рахували", max_digits=10, decimal_places=4, null=True, blank=True
    )
    price_updated_at = models.DateTimeField("Ціну оновлено", null=True, blank=True)

    price_locked = models.BooleanField("Ціна зафіксована", default=False, db_index=True)
    # ⚠️ Блокує ТІЛЬКИ base_price/price/source_currency/usd_rate_used/markup_percent/*_old_price.
    #   НЕ блокує наявність і НЕ виключає товар з прогону. У v1 «locked» означало «синк цей
    #   товар не бачить» → він не деактивувався і не оновлював наявність, тобто фіксація ціни
    #   тихо вибивала товар з нормального життєвого циклу.
    availability_locked = models.BooleanField("Наявність зафіксована", default=False, db_index=True)
    # менеджер вручну поставив «Під замовлення» → синк не перезапише.

    # --- НАЯВНІСТЬ ---
    availability = models.CharField(
        "Наявність",
        max_length=16,
        choices=Availability.choices,
        default=Availability.OUT_OF_STOCK,
        db_index=True,
    )
    order_lead_days = models.PositiveSmallIntegerField(
        "Строк постачання, днів", null=True, blank=True
    )
    stock_qty = models.IntegerField("К-сть на складі", null=True, blank=True)

    # --- ГАБАРИТИ УПАКОВКИ (ADR-021, INPUTS §3.4) ---
    package_weight_kg = models.DecimalField(
        "Вага в упаковці, кг", max_digits=7, decimal_places=3, null=True, blank=True
    )
    package_width_cm = models.DecimalField(
        "Ширина упаковки, см", max_digits=6, decimal_places=1, null=True, blank=True
    )
    package_height_cm = models.DecimalField(
        "Висота упаковки, см", max_digits=6, decimal_places=1, null=True, blank=True
    )
    package_depth_cm = models.DecimalField(
        "Глибина упаковки, см", max_digits=6, decimal_places=1, null=True, blank=True
    )
    package_dims_source = models.CharField(
        max_length=10, choices=ProductSource.choices, blank=True, editable=False
    )
    # ⚠️ ЗВІДКИ ЦЕ БЕРЕТЬСЯ: НЕ вводиться руками. Це ДЕНОРМАЛІЗОВАНА ПРОЄКЦІЯ з таблиці
    #   характеристик (INPUTS §3.4):
    #     «Габарити упаковки (ВхШхГ) (см): 171,5 x 56 x 35» → height=171.5, width=56, depth=35
    #     «Вага в упаковці (кг): 61.5»                      → package_weight_kg=61.5
    #   Парсер: роздільник "x"/"х"/"×" (КИРИЛИЧНА «х» — реальний випадок!), десяткова КОМА.
    #   Порядок у джерелі — ВхШхГ (Висота × Ширина × Глибина). НЕ переставляти.
    #   Заповнює sync_specs через Attribute.role = PACKAGE_DIMS / PACKAGE_WEIGHT — тобто БЕЗ
    #   хардкоду назв колонок: замовник переназве характеристику → правимо alias в адмінці.
    #
    # ⚠️ ГАБАРИТИ САМОГО ТОВАРУ («Висота (мм): 1635», «Вага (кг): 58») денормалізації НЕ мають:
    #   вони лишаються звичайними характеристиками в EAV і рендеряться в блоці «Габаритні
    #   розміри». У Нову Пошту йде ТІЛЬКИ упаковка. Саме тому старі імена (weight_kg/height_cm)
    #   були небезпечно двозначними: їх легко заповнити вагою товару і недоплатити за доставку
    #   (виміряно live: Ужгород→Київ, 2 кг без габаритів = 97.5 грн, з габаритами 50×50×60 =
    #   539.5 грн — різниця 5.5×).
    #
    # ⚠️ GeneratedField(volume_m3) ВИКИНУТО. Причини: (1) на PG14 він мусив би бути
    #   IMMUTABLE-STORED і давав би NULL, щойно порожній хоч один вимір — тобто НЕ збігався б з
    #   тим, що реально пішло в НП (там працює фолбек Category.default_* → SiteSettings);
    #   (2) Django не вміє ALTER виразу згенерованої колонки — зміна формули = DROP+ADD COLUMN.
    #   Об'єм рахує ЄДИНА функція delivery/services/dims.py::effective_dims(product).

    # --- ГАРАНТІЯ (теж проєкція з характеристик, Attribute.role=WARRANTY_MONTHS) ---
    warranty_months = models.PositiveSmallIntegerField("Гарантія, міс", null=True, blank=True)
    # Тип гарантії ЗАВЖДИ «від виробника» (написано в шапці прайсу, INPUTS §1), тому окремого
    # поля warranty_type немає: у фід іде <guarantee type="manufacturer" period="12"/>.

    # --- ТУМБЛЕРИ ---
    is_active = models.BooleanField("Активний", default=True, db_index=True)
    installment_available = models.BooleanField("Оплата частинами", default=False)
    installment_max_payments = models.PositiveSmallIntegerField(
        "Макс. платежів", null=True, blank=True
    )  # override SiteSettings.installment_max_period; NULL = брати глобальний
    hotline_enabled = models.BooleanField("Розміщувати на Hotline", default=False, db_index=True)
    is_featured = models.BooleanField("Рекомендований", default=False)

    condition = models.PositiveSmallIntegerField(
        "Стан", choices=Condition.choices, default=Condition.NEW, db_index=True
    )
    condition_note = models.CharField("Примітка до стану", max_length=300, blank=True)  # [tr]
    # «Вітринний зразок, подряпина на боковій панелі» — показуємо на картці біля бейджа «Уцінка».
    # Токен "cond:2" є у filter_tokens, тому фасет «Стан» працює всередині будь-якої категорії.

    # --- ДЖЕРЕЛО ДАНИХ ---
    source = models.CharField(
        "Джерело", max_length=16, choices=ProductSource.choices, default=ProductSource.SHEET
    )
    price_source = models.ForeignKey(
        "sync.PriceSource",
        verbose_name="Прайс-джерело",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="products",
    )  # SET_NULL (не PROTECT): видалення прайсу не має блокуватись товарами
    winning_offer = models.ForeignKey(
        "sync.SupplierOffer",
        verbose_name="Оффер-переможець",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # ⚠️ locked_fields (ArrayField) ВИКИНУТО повністю: воно обіцяло гарантію, якої не існує —
    #   bulk_create(update_conflicts=True) має СТАТИЧНИЙ update_fields і per-row винятків не
    #   підтримує в принципі. Замінено двома явними прапорцями: price_locked + availability_locked.

    # --- СЛУЖБОВІ ДЛЯ СИНКУ ---
    last_seen_run = models.UUIDField(null=True, blank=True, db_index=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    # --- ДЕНОРМАЛІЗАЦІЯ (перебудовує rebuild_product_denorm) ---
    specs_json = models.JSONField(default=list, blank=True)  # [tr] → specs_json_uk / specs_json_ru
    # [{"g":"Основні","gs":1,"code":"obiem","n":"Загальний об'єм","u":"л","v":"331","vn":331.0,"s":3}]
    #   g/gs = група та її порядок (AttributeGroup) — блок рендериться згрупованою таблицею;
    #   n = назва БЕЗ одиниці, u = одиниця, v = значення (рядок, «як показувати»), vn = число.
    # ⚠️ ПРАВИЛО РЕНДЕРУ (INPUTS §2, жорстка вимога замовника):
    #     ліва колонка  = n                  → «Висота»
    #     права колонка = f"{v} {u}".strip()  → «284 мм»       читається «Висота: 284 мм»
    #   НЕ «Висота (мм): 284». Одиниця клеїться до ЗНАЧЕННЯ, а не до назви. Фронт НІКОЛИ не
    #   конкатенує n + u. Той самий формат — у порівнянні, у фіді Hotline і в search_vector (C).
    # ⚠️ specs_json_ru — це ПЕРЕКЛАД ПІДПИСІВ, а не інша структура: n/u/v беруться з *_ru
    #   (Attribute.name_ru / Unit.name_ru / AttributeOption.value_ru), а code/gs/vn — ті самі.

    filter_tokens = ArrayField(models.CharField(max_length=80), default=list, blank=True)
    # ⚠️ ЄДИНЕ джерело І фільтрації, І лічильників фасетів (ADR-008). Токени:
    #   "brand:gorenje" | "country:sloveniia" | "avail:in_stock" | "cond:2" | "installment:1"
    #   "kamer:2" (option) | "obiem:300-399" (БАКЕТ range, ADR-007) | "morozylka:1" (bool)
    # ⚠️ ТОКЕНИ — БЕЗ МОВИ (тільки slug: AttributeOption.slug / Brand.slug, які НЕ [tr]). Одна
    #   колонка на обидві мови, один GIN, один набір лічильників; перемикання uk↔ru не змінює
    #   вибірку і не подвоює фасети. Мовним є лише ПІДПИС фасета (Attribute.name_ru,
    #   AttributeOption.value_ru) — його API бере з EAV/кешу.
    # ⚠️ Мультивибір робиться через `&&` (overlap = OR у межах групи), а НЕ через `@>`:
    #   filter_tokens @> ['brand:bosch','brand:gorenje'] означає «І Bosch, І Gorenje одночасно»
    #   → завжди нуль результатів. Між групами — AND.
    # ⚠️ ФІЛЬТРУЄМО ТІЛЬКИ ХАРАКТЕРИСТИКИ. Опис у filter_tokens не потрапляє ніколи.

    specs_num = models.JSONField(default=dict, blank=True)
    # {"obiem": 331.0, "shyryna": 60.0} — ТІЛЬКИ payload + вхід для expression-індексів під
    # filter_widget=range_slider (їх створює manage.py sync_range_indexes).
    # ⚠️ GIN(jsonb_path_ops) ЗНЯТО (ADR-007): для нерівностей (>=, <=) PostgreSQL не витягує з
    #   jsonpath жодної клаузи (тільки accessors_chain = constant) → нуль quals → Seq Scan з
    #   детоастингом jsonb на кожному рядку. Індекс був мертвим вантажем на найтовщій таблиці.
    main_image_url = models.URLField(max_length=500, blank=True)

    search_vector = SearchVectorField(null=True, editable=False)  # [tr] → _uk / _ru

    denorm_dirty = models.BooleanField(default=True, db_index=True)
    denorm_at = models.DateTimeField(null=True, blank=True, db_index=True)
    # ⚠️ Сигнал ставить dirty=True СИНХРОННО (в тій самій транзакції); Celery-задача знімає.
    #   beat heal_denorm кожні 5 хв добирає dirty → втрата on_commit-задачі більше не фатальна
    #   (ADR-011). Без цього товар, чия задача загубилась між COMMIT і .delay(), лишався б
    #   назавжди без specs_json/filter_tokens/main_image_url: він є в БД, але не знаходиться
    #   фільтрами і йде у фід без фото — і про це ніхто не дізнається.

    class Meta:
        verbose_name = "Товар"
        verbose_name_plural = "Товари"
        indexes = [
            models.Index(fields=["is_active", "category", "availability"], name="prod_list_idx"),
            models.Index(fields=["is_active", "category", "price"], name="prod_price_idx"),
            models.Index(fields=["hotline_enabled", "is_active"], name="prod_hotline_idx"),
            models.Index(fields=["source_currency", "price_locked"], name="prod_usd_recalc_idx"),
            models.Index(fields=["price_source", "availability"], name="prod_deactivate_idx"),
            models.Index(
                fields=["denorm_dirty"],
                condition=Q(denorm_dirty=True),
                name="prod_dirty_idx",
            ),  # partial: heal_denorm
            models.Index(
                fields=["name_normalized"],
                condition=~Q(name_normalized=""),
                name="prod_name_norm_idx",
            ),  # partial: фолбек-матчинг характеристик по назві
            GinIndex(fields=["filter_tokens"], name="prod_filter_gin"),
            # --- індекси по ПЕРЕКЛАДНИХ колонках (їх створює modeltranslation) ---
            # ⚠️ ВІДХІД ВІД DATA_MODEL §0 (свідомий): вони тут, у Meta, а не в окремій міграції.
            #    Причина — та сама, що й у Category.Meta: Meta є єдиним джерелом стану моделі, і
            #    індекс, який живе лише в міграції, автодетектор вважає зайвим і генерує міграцію
            #    на його ВИДАЛЕННЯ (перевірено). Ціна відходу — перше створення GIN іде звичайним
            #    CREATE INDEX. На порожній таблиці (CI, перший деплой) це нуль секунд.
            #    БУДЬ-ЯКИЙ НОВИЙ індекс, що додається на ЖИВУ базу пізніше, все одно робиться
            #    окремою міграцією через AddIndexConcurrently + atomic=False (DATA_MODEL §0).
            GinIndex(fields=["search_vector_uk"], name="prod_fts_uk_gin"),
            GinIndex(fields=["search_vector_ru"], name="prod_fts_ru_gin"),
            # trigram — опечатки і «схожі назви» (fallback, коли FTS дав 0 результатів).
            # Потрібні ОБИДВІ мови: у v1 був лише uk, тобто RU-пошук з одруківкою не працював.
            GinIndex(fields=["name_uk"], opclasses=["gin_trgm_ops"], name="prod_name_uk_trgm"),
            GinIndex(fields=["name_ru"], opclasses=["gin_trgm_ops"], name="prod_name_ru_trgm"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(price__gte=0), name="prod_price_nonneg"),
            models.CheckConstraint(condition=Q(base_price__gte=0), name="prod_base_price_nonneg"),
            models.CheckConstraint(
                condition=~Q(availability=Availability.ON_ORDER) | Q(order_lead_days__isnull=False),
                name="prod_on_order_needs_lead_days",
            ),
            models.CheckConstraint(
                condition=Q(old_price__isnull=True) | Q(old_price__gt=F("price")),
                name="prod_old_price_gt_price",
            ),
            models.CheckConstraint(
                condition=~Q(source_currency=SourceCurrency.USD) | Q(usd_rate_used__isnull=False),
                name="prod_usd_needs_rate",
            ),
        ]
        # ⚠️ Constraint'и — ДРУГА лінія оборони, не перша. Усі ці перевірки ДУБЛЮЮТЬСЯ в парсері
        #    (SYNC.md §4), бо CheckConstraint + bulk_create(batch_size=500) = один битий рядок
        #    валить ВЕСЬ батч з IntegrityError, а з ним — усю транзакцію прогону.

    def __str__(self) -> str:
        return f"{self.sku} — {self.name}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """name_normalized рахується ЗАВЖДИ з UK-назви (мова-джерело прайсу)."""
        source_name = getattr(self, "name_uk", None) or self.name
        self.name_normalized = normalize_model_name(source_name)

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            uf = set(update_fields)
            if uf & {"name", "name_uk"}:
                uf.add("name_normalized")
            kwargs["update_fields"] = uf

        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# 2.4 PriceHistory
# ---------------------------------------------------------------------------
class PriceHistory(models.Model):
    """Append-only. Наповнюється PostgreSQL-ТРИГЕРОМ, не Python (ADR-006).

    ⚠️ НЕ успадковує TimeStampedModel: це чистий журнал. updated_at йому не потрібен, а роль
    created_at грає changed_at (його пише тригер через now()).

    ⚠️ Логування історії в Python неможливе за визначенням: масовий qs.update() у recalc_prices
    і bulk_create(update_conflicts=True) у синку його обходять. Тригер покриває всі три шляхи —
    синк, recalc і ручну правку в адмінці — без жодного шансу забути.

    Контекст передається на початку транзакції:
        SET LOCAL complex.price_reason = 'sync';
        SET LOCAL complex.run_id = '<uuid>';
    Тригер читає їх через current_setting(..., true) — тобто без помилки, якщо їх не виставили
    (тоді reason = 'manual').

    ⚠️ Схема колонок ЖОРСТКО пов'язана з тілом функції complex_log_price()
    (catalog/migrations/_0002_price_history_trigger.py). Перейменування будь-якого поля тут =
    мовчазна поломка тригера на рівні runtime. Міняти — ТІЛЬКИ разом з міграцією.
    """

    class Reason(models.TextChoices):
        SYNC = "sync", "Синхронізація прайсу"
        RECALC = "recalc", "Перерахунок (курс/округлення/націнка)"
        MANUAL = "manual", "Ручна правка"
        ROLLBACK = "rollback", "Відкат прогону"
        IMPORT = "import", "Імпорт"

    id = models.BigAutoField(primary_key=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="price_history")
    price = models.DecimalField(max_digits=12, decimal_places=2)
    base_price = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    source_currency = models.CharField(max_length=3, blank=True)
    usd_rate_used = models.DecimalField(max_digits=10, decimal_places=4, null=True)
    markup_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    availability = models.CharField(max_length=16, blank=True)
    reason = models.CharField(max_length=10, choices=Reason.choices, default=Reason.MANUAL)
    run = models.ForeignKey(
        "sync.SyncRun", null=True, blank=True, on_delete=models.SET_NULL, db_constraint=False
    )
    # ⚠️ db_constraint=False: тригер пише run_id сирим uuid-ом з current_setting(). FK-перевірка
    #   на рівні тригера означала б звернення до sync_syncrun на КОЖНОМУ UPDATE ціни (10k рядків
    #   за прогін) — зайва блокування й зайва латентність.
    changed_at = models.DateTimeField(db_index=True)

    class Meta:
        verbose_name = "Історія ціни"
        verbose_name_plural = "Історія цін"
        indexes = [models.Index(fields=["product", "-changed_at"], name="ph_product_idx")]
        # Ретеншн — 2 роки, чистка батчами (catalog.tasks.purge_price_history).
        # При обсягах > 5М рядків — партиціонування по місяцях.

    def __str__(self) -> str:
        return f"{self.product_id}: {self.price} ({self.changed_at:%Y-%m-%d %H:%M})"


# ---------------------------------------------------------------------------
# 2.5 Атрибути — EAV + денормалізовані проєкції
# ---------------------------------------------------------------------------
class AttributeGroup(TimeStampedModel):
    """Підзаголовки в блоці «Характеристики»: «Основні:», «Габаритні розміри:» (INPUTS §2).

    Порядок груп — sort_order; усередині групи — Attribute.sort_order.
    """

    code = models.SlugField("Код", max_length=60, unique=True)  # "main", "dimensions"
    name = models.CharField("Назва", max_length=120)  # [tr] «Основні», «Габаритні розміри»
    sort_order = models.PositiveSmallIntegerField("Порядок", default=0)
    is_active = models.BooleanField("Активна", default=True)

    class Meta:
        ordering = ("sort_order", "id")
        verbose_name = "Група характеристик"
        verbose_name_plural = "Групи характеристик"

    def __str__(self) -> str:
        return self.name


class Unit(TimeStampedModel):
    """Одиниця виміру — НОРМАЛІЗОВАНА таблиця (~25 рядків), а не CharField.

    ЧОМУ:
      (1) одиниця перекладна («міс»→«мес», «год»→«ч», «об/хв»→«об/мин») і мусить перекластись
          РІВНО ОДИН РАЗ на весь каталог — інакше в RU-версії поїде «12 міс»;
      (2) у таблиці характеристик одиниця лежить в ОКРЕМІЙ колонці трійки (INPUTS §3.3), тобто
          вона вже є окремою сутністю в джерелі — гріх її розчиняти в рядку;
      (3) її треба клеїти до ЗНАЧЕННЯ при рендері («284 мм»), а не до назви («Висота (мм)»).

    Ми перекладаємо СЛОВНИК, а не товари: Attribute.name (~600), AttributeOption.value (~6000),
    Unit.name (~25) перекладаються один раз і застосовуються до всіх 10 000 товарів.
    """

    code = models.SlugField("Код", max_length=32, unique=True)  # "mm", "kg", "l", "month"
    name = models.CharField("Назва", max_length=32)  # [tr] «мм»/«мм»; «міс»/«мес»
    aliases = ArrayField(
        models.CharField(max_length=32),
        verbose_name="Синоніми",
        default=list,
        blank=True,
        help_text='["мм", "mm", "мілім", "миллиметр"] — усе, що прилітає з колонки '
        "«Одиниця_виміру».",
    )
    needs_review = models.BooleanField("Потребує перевірки", default=False, db_index=True)

    class Meta:
        ordering = ("code",)
        verbose_name = "Одиниця виміру"
        verbose_name_plural = "Одиниці виміру"
        indexes = [GinIndex(fields=["aliases"], name="unit_aliases_gin")]

    def __str__(self) -> str:
        return self.name


class Attribute(TimeStampedModel):
    """Характеристика.

    ⚠️ КЛЮЧОВЕ ПРАВИЛО: атрибути й опції, створені АВТОМАТИЧНО (синком або ШІ), отримують
    is_filterable=False, filter_widget=NONE, needs_review=True. Вони одразу видно в картці й у
    порівнянні, але у ФІЛЬТРИ не потрапляють, поки контент-менеджер їх не схвалить. Це прямий
    фікс брудних фасетів старого сайту (Sharp vs SHARP, сирі кВт·год у фільтрах).

    ⚠️ Фільтруються ТІЛЬКИ характеристики (INPUTS §2). Ані description, ані short_description у
    фасети не потрапляють ніколи — навіть якщо в описі написано «No Frost». Хоче замовник
    фільтр — має з'явитись Attribute.
    """

    class ValueType(models.TextChoices):
        OPTION = "option", "Варіант зі списку"
        NUMBER = "number", "Число"
        BOOL = "bool", "Так/Ні"
        STRING = "string", "Текст"

    class FilterWidget(models.TextChoices):
        NONE = "none", "Не фільтрувати"
        CHECKBOX = "checkbox", "Чекбокси"
        SELECT = "select", "Випадаючий список"
        RANGE_BUCKETS = "range_buckets", "Діапазон (бакети)"  # ДЕФОЛТ для NUMBER (ADR-007)
        RANGE_SLIDER = "range_slider", "Діапазон (слайдер)"  # escape hatch, потребує index

    class Role(models.TextChoices):
        NONE = "", "—"
        PACKAGE_DIMS = "package_dims", "Габарити упаковки (ВхШхГ)"
        PACKAGE_WEIGHT = "package_weight", "Вага в упаковці"
        WARRANTY_MONTHS = "warranty_months", "Гарантійний термін"
        COLOR = "color", "Колір (для кружечків-варіантів)"

    code = models.SlugField("Код", max_length=80, unique=True)  # "obiem"
    name = models.CharField("Назва", max_length=160)  # [tr] «Загальний об'єм»
    unit = models.ForeignKey(
        Unit,
        verbose_name="Одиниця",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="attributes",
    )
    # ⚠️ Одиниця — FK на словник, а не CharField. У НАЗВІ атрибута одиниці бути НЕ МОЖЕ:
    #   name="Висота" + unit="мм", а НЕ name="Висота (мм)". Якщо синк побачив «Висота (мм)» —
    #   парсер відриває дужку в unit (services/specs.py::split_unit_from_name) і логує WARN.
    group = models.ForeignKey(
        AttributeGroup, verbose_name="Група", on_delete=models.PROTECT, related_name="attributes"
    )
    value_type = models.CharField("Тип значення", max_length=10, choices=ValueType.choices)

    role = models.CharField("Роль", max_length=16, choices=Role.choices, blank=True, default="")
    # ⚠️ Ким ця характеристика працює, КРІМ себе самої. Саме через role sync_specs заповнює
    #   Product.package_* і Product.warranty_months — БЕЗ хардкоду рядків «Габарити упаковки» у
    #   коді. Замовник переназве характеристику в таблиці → міняємо alias в адмінці, а не деплой.

    aliases = ArrayField(
        models.CharField(max_length=160),
        verbose_name="Синоніми",
        default=list,
        blank=True,
        help_text='["Общий объем", "Обсяг загальний", "Об\'єм, л"] — нормалізація сирих назв.',
    )

    is_filterable = models.BooleanField("Фільтрувати", default=False, db_index=True)  # ДЕФОЛТ False
    filter_widget = models.CharField(
        "Віджет фільтра", max_length=14, choices=FilterWidget.choices, default=FilterWidget.NONE
    )
    range_buckets = models.JSONField("Бакети діапазонів", default=list, blank=True)
    # [[0,199],[200,299],[300,399],[400,null]] → токени "obiem:0-199", …, "obiem:400-plus".
    # Автогенерація по перцентилях: manage.py suggest_buckets --attr=obiem  (ADR-007).
    is_comparable = models.BooleanField("У порівнянні", default=True)
    show_in_card = models.BooleanField("У картці", default=True)
    sort_order = models.PositiveSmallIntegerField("Порядок", default=100)

    categories = models.ManyToManyField(
        Category,
        verbose_name="Категорії",
        blank=True,
        related_name="attributes",
        help_text="У яких категоріях показувати/фільтрувати. Порожньо = у всіх.",
    )

    hotline_param_name = models.CharField(
        "Назва параметра Hotline", max_length=160, blank=True
    )  # → <param name="…">
    needs_review = models.BooleanField("Потребує перевірки", default=False, db_index=True)

    class Meta:
        ordering = ("group__sort_order", "sort_order", "id")
        verbose_name = "Характеристика"
        verbose_name_plural = "Характеристики"
        indexes = [GinIndex(fields=["aliases"], name="attr_aliases_gin")]
        constraints = [
            models.CheckConstraint(
                # Літерал, а не FilterWidget.RANGE_BUCKETS: тіло class Meta не бачить простір
                # імен зовнішнього класу (class-body не створює замикання).
                condition=~Q(filter_widget="range_buckets") | ~Q(range_buckets=[]),
                name="attr_buckets_required_for_range",
            ),
            models.UniqueConstraint(fields=["role"], condition=~Q(role=""), name="uniq_attr_role"),
            # ⚠️ Рівно ОДНА характеристика може бути джерелом габаритів упаковки / ваги / гарантії.
            #    Дві «Габарити упаковки» в таблиці = недетермінований розмір посилки в НП
            #    (і систематична недоплата за доставку).
        ]

    def __str__(self) -> str:
        return self.name


class AttributeOption(TimeStampedModel):
    attribute = models.ForeignKey(
        Attribute, verbose_name="Характеристика", on_delete=models.CASCADE, related_name="options"
    )
    value = models.CharField("Значення", max_length=160)  # [tr] «Сріблястий»/«Серебристый»
    slug = models.SlugField("Slug", max_length=180)  # "sriblyastyi" → токен "color:sriblyastyi"
    # ⚠️ slug — НЕ [tr]. Він будується з UK-значення і є ВІЧНИМ ключем токена й URL фільтра.
    #   Перекладається тільки `value`. Тому /catalog/?color=sriblyastyi однаковий в обох мовах,
    #   і перемикання мови не змінює результат фільтра.
    aliases = ArrayField(
        models.CharField(max_length=160), verbose_name="Синоніми", default=list, blank=True
    )
    sort_order = models.PositiveSmallIntegerField("Порядок", default=0)
    needs_review = models.BooleanField("Потребує перевірки", default=False, db_index=True)

    # --- для перемикача-кружечка (VariantGroup.widget=SWATCHES, скріни 346/347) ---
    swatch_hex = models.CharField("Колір (HEX)", max_length=7, blank=True)  # "#C0C0C0"
    swatch_image = models.ImageField(
        "Текстура", upload_to="swatches/", blank=True
    )  # нерж. сталь, дерево

    class Meta:
        ordering = ("sort_order", "id")
        verbose_name = "Варіант характеристики"
        verbose_name_plural = "Варіанти характеристик"
        constraints = [
            models.UniqueConstraint(fields=["attribute", "slug"], name="uniq_attr_option")
        ]
        indexes = [GinIndex(fields=["aliases"], name="attropt_aliases_gin")]

    def __str__(self) -> str:
        return self.value


class ProductAttributeValue(TimeStampedModel):
    """EAV — ДЖЕРЕЛО ІСТИНИ для характеристик.

    Multi-value: товар з двома кольорами = два рядки з різними `option` — часткові unique це
    дозволяють. Скалярні значення (NUMBER/STRING/BOOL): option=NULL, унікальність по
    (product, attribute).

    Чому EAV І денормалізація одночасно: картка/порівняння/фід читають specs_json_{lang}
    (1 рядок замість JOIN на 60), фільтри й лічильники — filter_tokens (ADR-008), а EAV лишається
    тим, що редагується, схвалюється і зливається (JSONB не має FK-цілісності).
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="attr_values")
    attribute = models.ForeignKey(Attribute, on_delete=models.CASCADE, related_name="values")

    option = models.ForeignKey(
        AttributeOption, null=True, blank=True, on_delete=models.PROTECT, related_name="values"
    )
    # ⚠️ PROTECT, а НЕ CASCADE: CASCADE знищив би УСІ характеристики товарів при видаленні дубля
    #   опції. Плюс заборона delete в адмінці + дія merge_into() (див. коментар біля Brand).

    value_string = models.CharField("Текстове значення", max_length=500, blank=True)  # [tr]
    value_number = models.DecimalField(
        "Числове значення", max_digits=14, decimal_places=4, null=True, blank=True
    )
    value_bool = models.BooleanField("Так/Ні", null=True)
    raw_value = models.CharField(max_length=500, blank=True)  # як прийшло з прайсу (аудит)
    sort_order = models.PositiveSmallIntegerField(default=0)
    source = models.CharField(
        max_length=10, choices=ProductSource.choices, default=ProductSource.SHEET
    )  # хто заповнив: sheet / manual / ai

    class Meta:
        verbose_name = "Значення характеристики"
        verbose_name_plural = "Значення характеристик"
        constraints = [
            # ⚠️ ДВА часткові unique замість UniqueConstraint(nulls_distinct=False): той вимагає
            #    PG15, а цільова БД — PG14 (ADR-016). На PG14 NULL-и завжди РОЗРІЗНЯЮТЬСЯ, тому
            #    один unique по (product, attribute, option) не дав би жодної гарантії для
            #    скалярів (option IS NULL) — можна було б завести десять «Висот» на товарі.
            models.UniqueConstraint(
                fields=["product", "attribute", "option"],
                condition=Q(option__isnull=False),
                name="uniq_pav_option",
            ),
            models.UniqueConstraint(
                fields=["product", "attribute"],
                condition=Q(option__isnull=True),
                name="uniq_pav_scalar",
            ),
        ]
        indexes = [
            models.Index(fields=["product", "attribute"], name="pav_card_idx"),
            models.Index(fields=["attribute", "value_number", "product"], name="pav_range_idx"),
            # ⚠️ (attribute, value_number, product) — index-only scan для адмінських звітів і для
            #    suggest_buckets. ФАСЕТИ ЙОГО НЕ ВИКОРИСТОВУЮТЬ: і фільтрація, і лічильники
            #    рахуються з Product.filter_tokens (ADR-008) — одне джерело, узгоджене за
            #    побудовою. pav_facet_idx (attribute, option, product) з v1 ВИКИНУТО: мінус один
            #    індекс на найтовщій таблиці.
        ]

    def __str__(self) -> str:
        return f"{self.product_id}: {self.attribute_id}"


# ---------------------------------------------------------------------------
# 2.6 ProductImage
# ---------------------------------------------------------------------------
class ProductImage(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")

    source = models.CharField(
        max_length=10, choices=ProductSource.choices, default=ProductSource.SHEET, db_index=True
    )
    # ⚠️ БЕЗ цього поля неможливо відрізнити фото з прайсу від ручного → будь-яке автоприбирання
    #   зносило б роботу контент-менеджера. Прибирання застарілих фото (постачальник замінив
    #   URL) робить sync.services.prune_stale_images(): фото з `source=sheet`, у яких
    #   last_seen_run != поточний прогін. РУЧНІ ФОТО НЕ ЧІПАЮТЬСЯ НІКОЛИ, файли зі сховища
    #   видаляються ПІСЛЯ коміту (on_commit), а не в транзакції.
    last_seen_run = models.UUIDField(null=True, blank=True, db_index=True)

    source_url = models.URLField(max_length=1000, blank=True)
    source_url_hash = models.CharField(max_length=64, blank=True, db_index=True)  # sha256(url)
    etag = models.CharField(max_length=255, blank=True)  # conditional GET → 304
    content_hash = models.CharField(max_length=64, blank=True)  # sha256(bytes) — дедуп по вмісту

    file = models.ImageField(upload_to="products/orig/%Y/%m/")
    file_large = models.ImageField(upload_to="products/lg/%Y/%m/", blank=True)  # 1600px webp
    file_card = models.ImageField(upload_to="products/md/%Y/%m/", blank=True)  # 700px webp
    file_thumb = models.ImageField(upload_to="products/sm/%Y/%m/", blank=True)  # 300px webp
    # ⚠️ Імена файлів — ЛАТИНИЦЯ: {url_hash[:16]}_md.webp. Вимога Hotline: кирилиця в імені
    #   файла = товар БЕЗ ФОТО у фіді (тобто товар, який ніхто не побачить).

    alt = models.CharField(max_length=255, blank=True)  # [tr]
    position = models.PositiveSmallIntegerField(default=0)
    is_main = models.BooleanField(default=False)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)

    downloaded_at = models.DateTimeField(null=True, blank=True, db_index=True)
    failed_reason = models.CharField(max_length=255, blank=True, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ("position", "id")
        verbose_name = "Фото товару"
        verbose_name_plural = "Фото товарів"
        constraints = [
            models.UniqueConstraint(
                fields=["product", "source_url_hash"],
                condition=~Q(source_url_hash=""),
                name="uniq_product_image_url",
            ),
            # ⚠️ ЧАСТКОВИЙ (condition!) — і це не косметика: без condition два РУЧНІ фото одного
            #   товару (source_url="", отже однаковий порожній hash) давали б IntegrityError на
            #   другому → контент-менеджер фізично не може залити галерею.
            #   nulls_distinct=False тут теж не годиться (PG15, ADR-016).
            models.UniqueConstraint(
                fields=["product"], condition=Q(is_main=True), name="uniq_main_image"
            ),
        ]
        indexes = [models.Index(fields=["product", "source", "last_seen_run"], name="img_sync_idx")]
        # ⚠️ uniq_main_image — partial unique index, який НЕ МОЖЕ бути DEFERRABLE. Тому зміна
        #    головного фото ЗАВЖДИ двокрокова і робиться ТІЛЬКИ через
        #    services/images.py::set_main_image(product, image):
        #      images.filter(is_main=True).exclude(pk=image.pk).update(is_main=False)
        #      → image.is_main = True    (усе в одній транзакції)
        #    В ProductAdmin обов'язково перевизначити save_formset — інакше inline-формсет, що
        #    зберігає рядки по одному в довільному порядку, впаде з IntegrityError приблизно
        #    завжди.

    def __str__(self) -> str:
        return f"{self.product_id} #{self.position}"


# ---------------------------------------------------------------------------
# 2.7 VariantGroup / RelatedGroup
# ---------------------------------------------------------------------------
class VariantGroup(TimeStampedModel):
    """Група варіантів одного товару.

    ⚠️ Вісь варіанта — БУДЬ-ЯКА характеристика, не лише діагональ. На референсах замовника є і
    перемикач діагоналей (50" / 55" / 65", скрін 384), і перемикач КОЛЬОРУ кружечками (скріни
    346/347). Тому вісь задається через axis_attribute (FK на Attribute), а спосіб відображення —
    через widget.
    """

    class Widget(models.TextChoices):
        BUTTONS = "buttons", "Кнопки (діагональ, об'єм)"
        SWATCHES = "swatches", "Кружечки (колір)"

    name = models.CharField("Назва", max_length=200)  # [tr] «Samsung QLED Q60D»
    axis_attribute = models.ForeignKey(
        Attribute,
        verbose_name="Вісь перемикача",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="variant_groups",
    )  # Attribute(«Діагональ екрану») АБО Attribute(«Колір виробу»)
    widget = models.CharField(
        "Віджет", max_length=16, choices=Widget.choices, default=Widget.BUTTONS
    )
    is_active = models.BooleanField("Активна", default=True)

    class Meta:
        verbose_name = "Група варіантів"
        verbose_name_plural = "Групи варіантів"

    def __str__(self) -> str:
        return self.name


class VariantItem(models.Model):
    """Членство товару в групі варіантів.

    ⚠️ Винесено з Product (де у v1 були variant_group/variant_label/variant_sort), бо:
      (1) товар може належати ДВОМ осям одночасно (та сама модель ТВ: і діагональ, і колір);
      (2) label мусить бути [tr] («Слонова кістка»/«Слоновая кость») — на Product це означало б
          ще дві колонки заради перемикача;
      (3) кружечкам потрібен swatch_hex, який до Product не має жодного стосунку.

    Перемикач у картці = group.items.filter(product__is_active=True).order_by("sort_order");
    активний елемент — той, чий product == поточний товар.
    """

    group = models.ForeignKey(VariantGroup, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="variant_memberships"
    )
    label = models.CharField("Підпис", max_length=80)  # [tr] '50" (127 см)' / «Чорний»
    swatch_hex = models.CharField(max_length=7, blank=True)  # тільки для widget=SWATCHES
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "id")
        verbose_name = "Варіант товару"
        verbose_name_plural = "Варіанти товару"
        constraints = [
            models.UniqueConstraint(fields=["group", "product"], name="uniq_variant_member")
        ]
        indexes = [models.Index(fields=["group", "sort_order"], name="variant_group_idx")]

    def __str__(self) -> str:
        return self.label


class RelatedGroup(TimeStampedModel):
    """Супутні товари.

    ⚠️ ВЗАЄМНІСТЬ — ВЛАСТИВІСТЬ СТРУКТУРИ, а не робота коду. Контент-менеджер збирає ОДНУ групу з
    3 товарів — усі троє автоматично бачать двох інших. Асиметрія («А бачить Б, Б не бачить А»)
    неможлива в принципі. Саме тому це група + membership (RelatedGroupItem), а не пари FK
    product_a / product_b, які треба було б синхронізувати вручну і які завжди розходяться.
    """

    class Kind(models.TextChoices):
        SET = "set", "Комплект (взаємно)"
        ACCESSORY = "accessory", "Аксесуари"
        CROSS_SELL = "cross_sell", "Разом купують"

    name = models.CharField("Назва", max_length=200)  # [tr]
    kind = models.CharField("Тип", max_length=16, choices=Kind.choices, default=Kind.SET)
    is_active = models.BooleanField("Активна", default=True)
    sort_order = models.PositiveSmallIntegerField("Порядок", default=0)

    class Meta:
        ordering = ("sort_order", "id")
        verbose_name = "Група супутніх товарів"
        verbose_name_plural = "Групи супутніх товарів"

    def __str__(self) -> str:
        return self.name


class RelatedGroupItem(models.Model):
    group = models.ForeignKey(RelatedGroup, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="related_memberships"
    )
    role = models.CharField("Роль у комплекті", max_length=60, blank=True)  # [tr] «Духова шафа»
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "id")
        verbose_name = "Супутній товар"
        verbose_name_plural = "Супутні товари"
        constraints = [
            models.UniqueConstraint(fields=["group", "product"], name="uniq_group_product")
        ]
        indexes = [models.Index(fields=["product", "group"], name="relgroup_product_idx")]

    def __str__(self) -> str:
        return f"{self.group_id}: {self.product_id}"
