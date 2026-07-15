# Complex — Модель даних v2

> З цього документа `models.py` пишеться без домислів. Кожне поле, індекс і constraint — обґрунтовані.
> ⚡ = змінено або додано у v2 (закриває critical/major прогалину).

---

## 0. Наскрізні угоди

* Усі моделі (крім чистих журналів) успадковують `core.TimeStampedModel` → `created_at = DateTimeField(auto_now_add=True)`, `updated_at = DateTimeField(auto_now=True, db_index=True)`.
  ⚠️ `updated_at` **db_index** — по ньому ходять sitemap, ISR-ревалідація і heal-задачі.
* SEO-поля — `core.SEOMixin`: `seo_title` (CharField 200, blank) `[tr]`, `seo_description` (CharField 400, blank) `[tr]`, `og_image` (ImageField, blank) `[tr]`.
* **i18n — `django-modeltranslation`. RU — В MVP, не «на майбутнє» (INPUTS §1).** ⚡
  `[tr]` = дві фізичні колонки `field_uk` / `field_ru`. `LANGUAGES = [("uk", …), ("ru", …)]`, дефолт — `uk`,
  `MODELTRANSLATION_FALLBACK_LANGUAGES = ("uk",)` (порожній `_ru` → віддаємо `_uk`, сторінка ніколи не порожня).
  * Перекладні (обов'язково): **назви категорій і товарів, описи, назви й значення характеристик, одиниці виміру,
    SEO-поля, статичні сторінки/новини/меню/банери**.
  * ⚡ **Денормалізовані проєкції теж перекладні:** `specs_json` → `specs_json_uk` / `specs_json_ru`,
    `search_vector` → `search_vector_uk` / `search_vector_ru`. Інакше російська картка показує українські
    характеристики, а російський пошук не знаходить нічого.
  * ⚡ **`filter_tokens` — БЕЗ мови.** Токен будується зі `slug` (`AttributeOption.slug`, `Brand.slug`) —
    а `slug` **не** `[tr]`. Тому перемикання мови **не змінює результат фільтра** і не подвоює фасети.
    Мовним є лише *підпис* фасета (`Attribute.name_ru`, `AttributeOption.value_ru`), який API бере з EAV/кешу.
  * ⚡ **Хто пише в `_ru`:** ніхто вручну масово. Пише `i18n.TranslationEntry.approve()` (§10) — черга
    машинного перекладу з ручним схваленням. `_ru`-колонка — це **вітрина**, `TranslationEntry` — **джерело правди**
    для черги, аудиту й витрат. Деталі пайплайна — `docs/research/TRANSLATION.md`.
  * ⚡ **Перекладаємо СЛОВНИК, а не товари:** `Attribute.name` (~600), `AttributeOption.value` (~6 000),
    `Unit.name` (~25) перекладаються **один раз** і застосовуються до всіх 10 000 товарів. Саме тому
    характеристики зобов'язані бути нормалізованими (`Attribute` / `AttributeOption` / `Unit`), а не рядками в JSON.
* **Гроші** — `DecimalField(max_digits=12, decimal_places=2)`. Ніяких `float`. Курс — `max_digits=10, decimal_places=4`.
* ⚡ **Ніякого `nulls_distinct=False`** (потребує PG 15; локальна БД — 14). Замість нього — два часткові unique-індекси (ADR-016).
* ⚡ **Індекси/unique по перекладних колонках** (`name_uk`, `slug_uk`, `search_vector_uk`) виносяться **не в `Meta`**, а в окрему міграцію `AddIndex`/`RunSQL` **після** initial. Причина: modeltranslation додає колонки в `AppConfig.ready()`, і посилання на них у `Meta` робить `makemigrations` залежним від порядку ініціалізації.
* ⚡ **Усі індекси, що додаються на живій БД** — через `django.contrib.postgres.operations.AddIndexConcurrently` + `atomic = False` у міграції. Інакше `CREATE INDEX` бере `ACCESS EXCLUSIVE` на `catalog_product` і кладе сайт на час побудови GIN.
* Enum-и — `models.TextChoices`.

### 0.1 Міграція `catalog/0001_initial` — обов'язкові операції ⚡

```python
from django.contrib.postgres.operations import (
    TrigramExtension, BtreeGinExtension, UnaccentExtension,
)

operations = [
    TrigramExtension(),          # pg_trgm  — trigram-пошук, автокомпліт НП
    BtreeGinExtension(),         # btree_gin — композитні GIN
    UnaccentExtension(),         # unaccent — TS-конфіг uk
    migrations.RunSQL(UK_TS_CONFIG_SQL,  reverse_sql="DROP TEXT SEARCH CONFIGURATION IF EXISTS uk;"),
    migrations.RunSQL(RU_TS_CONFIG_SQL,  reverse_sql="DROP TEXT SEARCH CONFIGURATION IF EXISTS ru_complex;"),  # ⚡
    migrations.RunSQL(PRICE_FN_SQL,      reverse_sql="DROP FUNCTION IF EXISTS complex_price_uah;"),
    migrations.RunSQL(PRICE_HISTORY_TRG, reverse_sql="DROP TRIGGER IF EXISTS price_history_trg ON catalog_product;"),
    ...
]
```

**`UK_TS_CONFIG_SQL`:**
```sql
CREATE TEXT SEARCH CONFIGURATION uk (COPY = simple);
ALTER TEXT SEARCH CONFIGURATION uk
    ALTER MAPPING FOR hword, hword_part, word WITH unaccent, simple;
```

**`RU_TS_CONFIG_SQL` ⚡** (RU в MVP → `search_vector_ru` мусить мати свою конфігурацію):
```sql
CREATE TEXT SEARCH CONFIGURATION ru_complex (COPY = russian);   -- вбудований russian словник є в PG
ALTER TEXT SEARCH CONFIGURATION ru_complex
    ALTER MAPPING FOR hword, hword_part, word WITH unaccent, russian_stem;
```
> Ім'я **`ru_complex`**, а не `ru`: `ru` — це вже алiас у деяких збірках PG, і `CREATE` впаде.
> `search_vector_uk` будується з конфігом `uk`, `search_vector_ru` — з `ru_complex`. Обидва — в `rebuild_product_denorm`.

**Ваги FTS (однакові для обох мов):**
`A` = `name`, `B` = бренд + категорія, `C` = **характеристики** (`specs_json_{lang}` → назва + значення + одиниця),
`D` = `description` (текст без HTML-тегів). Опис **шукається**, але **не фільтрується** (INPUTS §2).

**`PRICE_FN_SQL` — ЄДИНЕ джерело формули ціни (ADR-005):**
```sql
CREATE OR REPLACE FUNCTION complex_price_uah(
    base numeric, currency text, rate numeric, markup numeric, rule text
) RETURNS numeric
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE v numeric;
BEGIN
    IF base IS NULL THEN RETURN NULL; END IF;
    v := base * (1 + COALESCE(markup, 0) / 100.0);
    IF currency = 'USD' THEN
        IF rate IS NULL OR rate <= 0 THEN RETURN NULL; END IF;
        v := v * rate;
    END IF;
    RETURN CASE rule
        WHEN 'none' THEN round(v, 2)
        WHEN 'unit' THEN round(v, 0)
        WHEN 'ten'  THEN round(v / 10, 0) * 10
        WHEN 'nine' THEN CASE WHEN v < 100 THEN ceil(v) 
                              ELSE greatest(round(v / 10, 0) * 10 - 1, 9) END
        ELSE round(v, 0)
    END;
END $$;
```
> Python-двійник `catalog/services/pricing.py::compute_uah_price()` існує **лише для preview/адмінки**.
> Обов'язковий тест `test_price_parity_python_vs_sql` — fuzz по сітці `base × rate × markup × rule`.

**`PRICE_HISTORY_TRG` — історія цін (ADR-006):**
```sql
CREATE OR REPLACE FUNCTION complex_log_price() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.price IS NOT DISTINCT FROM OLD.price
                        AND NEW.availability = OLD.availability THEN
        RETURN NULL;
    END IF;
    INSERT INTO catalog_pricehistory
        (product_id, price, base_price, source_currency, usd_rate_used,
         markup_percent, availability, reason, run_id, changed_at)
    VALUES (NEW.id, NEW.price, NEW.base_price, NEW.source_currency, NEW.usd_rate_used,
            NEW.markup_percent, NEW.availability,
            COALESCE(current_setting('complex.price_reason', true), 'manual'),
            NULLIF(current_setting('complex.run_id', true), '')::uuid,
            now());
    RETURN NULL;
END $$;

CREATE TRIGGER price_history_trg
AFTER INSERT OR UPDATE OF price, availability ON catalog_product
FOR EACH ROW EXECUTE FUNCTION complex_log_price();
```
> Контекст передається на початку транзакції: `SET LOCAL complex.price_reason = 'sync'; SET LOCAL complex.run_id = '<uuid>';`
> Тригер покриває `bulk_create(update_conflicts)`, `qs.update()` **і** ручну правку в адмінці.

**`order_number_seq`** (ADR-014): `CREATE SEQUENCE IF NOT EXISTS order_number_seq START 1;`

---

## 1. `core`

```python
class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    class Meta:
        abstract = True


class SEOMixin(models.Model):
    seo_title       = models.CharField(max_length=200, blank=True)   # [tr]
    seo_description = models.CharField(max_length=400, blank=True)   # [tr]
    og_image        = models.ImageField(upload_to="seo/", blank=True)  # [tr]
    class Meta:
        abstract = True


class SingletonModel(models.Model):
    """pk=1 завжди; get_solo() з кешем у Redis (інвалідація в save())."""
    class Meta:
        abstract = True
```

---

## 2. `catalog`

### 2.1 Category

```python
class Category(SEOMixin, TimeStampedModel):
    external_id = models.CharField(
        max_length=32, unique=True,
        help_text="ID категорії з прайсу / старого сайту, БЕЗ префікса 'c'. Напр. 5609730")
    # ⚠️ unique=True вже створює індекс — НЕ додавати db_index=True (gap: індекси-дублі)

    parent = models.ForeignKey("self", null=True, blank=True,
                               on_delete=models.PROTECT, related_name="children")
    path   = models.CharField(max_length=255, db_index=True, editable=False)
    # матеріалізований шлях з external_id: "87727179" або "87727179/5609730"
    depth      = models.PositiveSmallIntegerField(default=0, editable=False)
    sort_order = models.PositiveIntegerField(default=0, db_index=True)

    name        = models.CharField(max_length=160)          # [tr]
    slug        = models.SlugField(max_length=180)          # [tr]  декоративний
    description = models.TextField(blank=True)              # [tr]

    icon  = models.FileField(upload_to="categories/icons/", blank=True, validators=[validate_icon])
    image = models.ImageField(upload_to="categories/tiles/", blank=True)

    is_active        = models.BooleanField(default=True, db_index=True)
    show_in_megamenu = models.BooleanField(default=True)
    is_service       = models.BooleanField(default=False, editable=False)
    # ⚡ True тільки для службової "__unmapped__" — щоб її не показувати і не рахувати

    # --- ⚡ ВІРТУАЛЬНА КАТЕГОРІЯ (ADR-023, INPUTS §5 Q6) ---
    virtual_tokens = ArrayField(models.CharField(max_length=80), default=list, blank=True)
    # Непорожньо => категорія НЕ має власних товарів через FK. Її лістинг =
    #   Product.objects.filter(filter_tokens__contains=virtual_tokens, is_active=True)
    # "Уцінка" = virtual_tokens=["cond:2"]. Так само робляться "Акції" (["promo:1"]),
    #   "Топ продажів" тощо — БЕЗ жодної нової моделі й без дублювання товару в дві категорії.
    # ⚠️ Уцінка — це ОЗНАКА товару (Product.condition), а не місце в дереві: той самий холодильник
    #   лишається у "Холодильники" (де його шукають і фільтрують) і паралельно видно в "Уцінка".
    #   Hotline-тег <condition> бере саме Product.condition — категорія тут ні до чого.

    # --- дефолти габаритів УПАКОВКИ (фолбек калькулятора НП, ADR-021) ---
    # Використовуються, лише коли Product.package_* порожні (немає рядка в таблиці характеристик).
    default_weight_kg = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)
    default_width_cm  = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    default_height_cm = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    default_depth_cm  = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)

    # --- Hotline ---
    hotline_category_name    = models.CharField(max_length=200, blank=True)
    # точна назва LEAF-категорії з hotline_tree_uk.csv; порожньо => товари категорії у фід НЕ йдуть
    hotline_enabled_default  = models.BooleanField(default=False)   # ⚡ CRITICAL FIX
    # успадковується НОВИМ товаром при створенні; НЕ входить в update_fields

    products_count = models.PositiveIntegerField(default=0, editable=False)  # денорм, Celery

    class Meta:
        ordering = ("sort_order", "id")
        indexes = [
            models.Index(fields=["parent", "sort_order"]),
            models.Index(fields=["is_active", "depth"]),
            models.Index(fields=["path"], name="cat_path_btree"),  # для path__startswith
        ]
        constraints = [
            # ⚡ БУЛО: UniqueConstraint(["slug_uk"]) — глобальний unique. Викинуто:
            #    slug декоративний (роут по ID), а дублі назв підкатегорій ("Аксесуари",
            #    "Інше" під різними батьками) — норма. Автосуфікс у save(): "aksesuary-2".
            models.UniqueConstraint(fields=["parent", "slug_uk"], name="uniq_cat_slug_in_parent"),
        ]
```

**Логіка `save()`:** перерахунок `path` = `parent.path + "/" + external_id` (або просто `external_id` для кореня) і `depth`; каскадний перерахунок нащадків (`Category.objects.filter(path__startswith=old_path + "/")`). Дерево — 59 вузлів, змінюється раз на місяць.
**Кеш:** усе дерево в Redis (`catalog:tree:{lang}`, TTL 1 год + інвалідація сигналом `post_save`).
**Сигнал `post_save`:** якщо `external_id` створено вперше → `remap_unmapped_products.delay(external_id)` (ADR: товари з `__unmapped__` автоматично переїжджають).

### 2.2 Brand / Country

```python
class Brand(TimeStampedModel):
    name    = models.CharField(max_length=120, unique=True)
    slug    = models.SlugField(max_length=140, unique=True)   # → токен "brand:gorenje"
    logo    = models.ImageField(upload_to="brands/", blank=True)
    aliases = ArrayField(models.CharField(max_length=120), default=list, blank=True)
    # ["SHARP", "sharp", "Шарп"] — усе, що приходить з прайсів
    is_active    = models.BooleanField(default=True)
    needs_review = models.BooleanField(default=False, db_index=True)
    # True => створено автоматично синком, контент-менеджер має злити дублі

    class Meta:
        indexes = [GinIndex(fields=["aliases"], name="brand_aliases_gin")]


class Country(TimeStampedModel):
    code    = models.CharField(max_length=2, unique=True)     # ISO-3166-1 alpha-2
    name    = models.CharField(max_length=100)                # [tr]
    slug    = models.SlugField(max_length=120, unique=True)   # → токен "country:polshcha"
    aliases = ArrayField(models.CharField(max_length=100), default=list, blank=True)
    flag    = models.FileField(upload_to="countries/", blank=True)
    needs_review = models.BooleanField(default=False, db_index=True)

    class Meta:
        indexes = [GinIndex(fields=["aliases"], name="country_aliases_gin")]
```

> ⚡ **Видалення `Brand` / `Country` / `AttributeOption` заборонене в адмінці** (`has_delete_permission → False`).
> Замість нього — дія **«Злити з…»** → `services/merge.py::merge_brands(src, dst)`:
> `Product.objects.filter(brand=src).update(brand=dst, denorm_dirty=True)` → `dst.aliases = set(dst.aliases + [src.name] + src.aliases)` → `src.delete()` → `rebuild_product_denorm.delay(ids)` → інвалідація Redis-кешу фасетів.
> **Чому:** `Product.brand` має `on_delete=SET_NULL` — просте видалення дубля обнулило б бренд у сотень товарів і вибило б їх з фасета «Виробник».

### 2.3 Product — центральна модель

```python
class Availability(models.TextChoices):
    IN_STOCK     = "in_stock",     "В наявності"
    ON_ORDER     = "on_order",     "Під замовлення"
    OUT_OF_STOCK = "out_of_stock", "Немає в наявності"

class SourceCurrency(models.TextChoices):
    UAH = "UAH", "Гривня"
    USD = "USD", "Долар США"

class ProductSource(models.TextChoices):
    SHEET  = "sheet",  "Google Sheets"
    MANUAL = "manual", "Створено вручну"
    AI     = "ai",     "ШІ-імпорт"

class Condition(models.IntegerChoices):      # сумісно з Hotline <condition>
    NEW         = 0, "Новий"
    REFURBISHED = 1, "Відновлений"
    DISCOUNTED  = 2, "Уцінений"
    USED        = 3, "Б/в"


class Product(SEOMixin, TimeStampedModel):
    # id: BigAutoField. Legacy-товари імпортуються з явним pk = старий 10-значний Prom-ID.
    # ⚡ Після імпорту: SELECT setval('catalog_product_id_seq', MAX(id)+1, false)  — НЕ константа 3e9.

    # --- ідентифікація ---
    sku = models.CharField("Артикул", max_length=64, unique=True)
    #  ↑ ЄДИНИЙ ключ синхронізації. NOT NULL. Для ручних товарів — "MAN-000123".
    mpn = models.CharField("Артикул виробника", max_length=64, blank=True, db_index=True)
    #  ↑ Hotline <code>. ОПЦІЙНИЙ (⚡ більше НЕ причина скіпу у фіді)
    barcode = models.CharField("Штрихкод (EAN)", max_length=32, blank=True)

    name              = models.CharField(max_length=255)              # [tr]
    slug              = models.SlugField(max_length=280)              # [tr]  декоративний
    description       = models.TextField(blank=True)                  # [tr]  rich HTML, bleach
    # ⚡ ОПИС — це rich HTML з ВБУДОВАНИМИ зображеннями (INPUTS §2, скрін 171: робот-пилосос
    #   з фото між абзацами). Редактор — TipTap в unfold, завантаження картинок → cms.EditorImage.
    #   Опис НЕ фільтрується і НЕ парситься в характеристики. Він іде у search_vector з вагою D.
    #   bleach-allowlist: h2,h3,h4,p,ul,ol,li,strong,em,br,table,tr,td,th,img,figure,figcaption,a
    #   + img[src] ДОЗВОЛЕНО тільки з нашого R2-домену (інакше — mixed content і чужий трекінг).
    short_description = models.CharField(max_length=500, blank=True)  # [tr]

    name_normalized = models.CharField(max_length=255, blank=True, editable=False)   # ⚡ NEW
    # ⚡ РЕЗЕРВНИЙ КЛЮЧ зіставлення прайсу з таблицею характеристик (INPUTS §3.5).
    #   Основний ключ — sku (артикул є в ОБОХ таблицях). Але Артурів скрипт зшивав по назві,
    #   і в даних лишились рядки, де артикул порожній/битий → потрібен фолбек.
    #   Алгоритм — catalog/services/naming.py::normalize_model_name() (порт скрипта Артура 1:1):
    #     NBSP→" "; усі тире (‐‑‒–—―−) → "-"; апострофи (’‘`´) → "'"; "×" → "x";
    #     unicodedata.normalize("NFKC"); .lower();
    #     кирилична І/і/Ї/ї → латинські I/i (класична пастка: "Sіemens" з кириличною і);
    #     викинути все, крім [a-z0-9._\-/]; прибрати пробіли.
    #   Заповнюється в save()/парсері, НІКОЛИ не редагується руками.

    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    brand    = models.ForeignKey(Brand,   null=True, blank=True, on_delete=models.SET_NULL)
    country  = models.ForeignKey(Country, null=True, blank=True, on_delete=models.SET_NULL)

    source_category_ext_id = models.CharField(max_length=32, blank=True, db_index=True)  # ⚡ CRITICAL
    # ID категорії з прайсу — пишеться синком ЗАВЖДИ (це дані прайсу, не ручний контент).
    # Без нього товари з "__unmapped__" ніколи не переїжджають у створену пізніше категорію.

    # --- ЦІНА (двошарова) ---
    source_currency = models.CharField(max_length=3, choices=SourceCurrency.choices,
                                       default=SourceCurrency.UAH, db_index=True)
    base_price      = models.DecimalField(max_digits=12, decimal_places=2)   # як у прайсі
    markup_percent  = models.DecimalField(max_digits=5, decimal_places=2, default=0)  # ⚡ CRITICAL
    # денормалізована ефективна націнка (з PriceSource; для ручних = 0).
    # Без неї recalc не може дотягнутись до price_source__price_markup_percent в одному UPDATE.
    price           = models.DecimalField(max_digits=12, decimal_places=2)   # грн, вітрина
    # ⚡ db_index прибрано: композит (is_active, category, price) покриває; окремий btree мертвий
    base_old_price  = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)  # ⚡
    old_price       = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # ⚡ old_price тепер ПОХІДНА: рахується тією ж complex_price_uah() з base_old_price.
    #   Було: old_price у грн назавжди → після зміни курсу old_price міг стати НИЖЧИМ за price.
    usd_rate_used    = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    price_updated_at = models.DateTimeField(null=True, blank=True)

    price_locked        = models.BooleanField(default=False, db_index=True)
    # ⚡ блокує ТІЛЬКИ base_price/price/source_currency/usd_rate_used/markup_percent/*_old_price.
    #   НЕ блокує наявність і НЕ виключає товар з прогону (був critical: locked = зникав з продажу).
    availability_locked = models.BooleanField(default=False, db_index=True)   # ⚡ MAJOR FIX
    # менеджер вручну поставив "Під замовлення" → синк не перезапише

    # --- НАЯВНІСТЬ ---
    availability    = models.CharField(max_length=16, choices=Availability.choices,
                                       default=Availability.OUT_OF_STOCK, db_index=True)
    order_lead_days = models.PositiveSmallIntegerField(null=True, blank=True)
    stock_qty       = models.IntegerField(null=True, blank=True)

    # --- ГАБАРИТИ УПАКОВКИ (⚡ ПЕРЕЙМЕНОВАНО з weight_kg/width_cm/… — див. нижче) ---
    package_weight_kg = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)
    package_width_cm  = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    package_height_cm = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    package_depth_cm  = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    package_dims_source = models.CharField(max_length=10, choices=ProductSource.choices,   # ⚡ NEW
                                           blank=True, editable=False)
    # ⚡ ЗВІДКИ ВОНИ БЕРУТЬСЯ (INPUTS §3.4 — блокер калькулятора НП знято):
    #   НЕ вводяться руками. Це ДЕНОРМАЛІЗОВАНА ПРОЄКЦІЯ з таблиці характеристик:
    #     "Габарити упаковки (ВхШхГ) (см): 171,5 x 56 x 35"  → height=171.5, width=56, depth=35
    #     "Вага в упаковці (кг): 61.5"                        → package_weight_kg=61.5
    #   Парсер: роздільник "x"/"х"/"×" (кирилична "х" — реальний випадок!), десяткова КОМА → крапка.
    #   Порядок у джерелі — ВхШхГ (Висота × Ширина × Глибина). Не переставляти.
    #   Заповнює sync_specs через Attribute.role = PACKAGE_DIMS / PACKAGE_WEIGHT (§2.5) —
    #   тобто без хардкоду назв колонок: назву характеристики міняє контент-менеджер в адмінці.
    #
    # ⚠️ ГАБАРИТИ САМОГО ТОВАРУ ("Висота (мм): 1635", "Вага (кг): 58") денормалізації НЕ мають —
    #   вони лишаються звичайними характеристиками в EAV і рендеряться в блоці "Габаритні розміри".
    #   У Нову Пошту йде ТІЛЬКИ упаковка. Саме тому старі імена (weight_kg/height_cm) були
    #   небезпечно двозначними — їх легко було заповнити вагою товару і недоплатити за доставку.
    # Фолбек: Category.default_* → SiteSettings.default_* (єдина функція delivery/services/dims.py).
    # ⚡ GeneratedField(volume_m3) ВИКИНУТО. Причини:
    #   (1) NULL, якщо хоч один вимір порожній → не збігається з тим, що реально пішло в НП
    #       (калькулятор бере фолбек Category.default_* у Python);
    #   (2) Django не вміє ALTER виразу згенерованої колонки — зміна формули = DROP+ADD COLUMN.
    #   Об'єм рахує ЄДИНА функція delivery/services/dims.py::effective_dims(product) (ADR-021).

    # --- ГАРАНТІЯ (⚡ NEW, теж проєкція з характеристик) ---
    warranty_months = models.PositiveSmallIntegerField(null=True, blank=True)
    # З характеристики "Гарантійний термін (міс): 12" (Attribute.role = WARRANTY_MONTHS).
    # Тип гарантії — ЗАВЖДИ "від виробника" (написано в шапці прайсу, INPUTS §1), тому окремого
    # поля warranty_type немає: у фід Hotline йде <guarantee type="manufacturer" period="12"/>.

    # --- ТУМБЛЕРИ ---
    is_active             = models.BooleanField(default=True, db_index=True)
    installment_available = models.BooleanField("Оплата частинами", default=False)
    installment_max_payments = models.PositiveSmallIntegerField(null=True, blank=True)   # ⚡ NEW
    # override глобального SiteSettings.installment_max_period. NULL = брати глобальний.
    hotline_enabled       = models.BooleanField("Розміщувати на Hotline", default=False, db_index=True)
    is_featured           = models.BooleanField(default=False)

    condition      = models.PositiveSmallIntegerField(choices=Condition.choices,
                                                      default=Condition.NEW, db_index=True)   # ⚡ db_index
    condition_note = models.CharField(max_length=300, blank=True)   # ⚡ NEW  [tr]
    # "Вітринний зразок, подряпина на боковій панелі" — показуємо на картці біля бейджа "Уцінка".
    # ⚡ УЦІНКА = ОЗНАКА ТОВАРУ, не категорія (INPUTS §5 Q6):
    #   (1) Hotline <condition> вимагає саме ознаку товару;
    #   (2) уцінений холодильник має лишатись у "Холодильники" з усіма фільтрами (об'єм, No Frost),
    #       а не випадати в кошик "Уцінка", де фасети не працюють;
    #   (3) сторінка "Уцінка" будується як Category.virtual_tokens=["cond:2"] (§2.1) — нуль дублювання.
    #   Токен "cond:2" вже є у filter_tokens, тобто фасет "Стан" працює всередині будь-якої категорії.

    # --- ДЖЕРЕЛО ДАНИХ ---
    source        = models.CharField(max_length=16, choices=ProductSource.choices,
                                     default=ProductSource.SHEET)
    price_source  = models.ForeignKey("sync.PriceSource", null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name="products")
    # ⚡ SET_NULL (було PROTECT): видалення прайсу не має блокуватись товарами
    winning_offer = models.ForeignKey("sync.SupplierOffer", null=True, blank=True,   # ⚡ NEW
                                      on_delete=models.SET_NULL, related_name="+")
    # ⚡ locked_fields (ArrayField) ВИКИНУТО повністю — обіцяв гарантію, якої не існувало
    #   (bulk_create має СТАТИЧНИЙ update_fields і per-row винятків не підтримує в принципі).
    #   Замінено двома явними прапорцями: price_locked + availability_locked.

    # --- СЛУЖБОВІ ДЛЯ СИНКУ ---
    last_seen_run = models.UUIDField(null=True, blank=True, db_index=True)
    synced_at     = models.DateTimeField(null=True, blank=True)

    # --- ЗВ'ЯЗКИ ---
    # ⚡ variant_group / variant_label / variant_sort ПРИБРАНІ з Product → окрема модель VariantItem (§2.7).
    #   Причини: (1) label мусить бути [tr] ("Слонова кістка" / "Слоновая кость") — на Product це
    #   означало б ще дві колонки заради перемикача; (2) для кольорових кружечків (скрін 346/347)
    #   потрібні swatch_hex / swatch_image, які до Product не мають жодного стосунку.

    # --- ДЕНОРМАЛІЗАЦІЯ (перебудовує rebuild_product_denorm) ---
    specs_json = models.JSONField(default=list, blank=True)   # ⚡ [tr] → specs_json_uk / specs_json_ru
    # [{"g":"Основні","gs":1,"code":"obiem","n":"Загальний об'єм","u":"л","v":"331","vn":331.0,"s":3},
    #  {"g":"Габаритні розміри","gs":2,"code":"vysota","n":"Висота","u":"мм","v":"284","vn":284.0,"s":1}]
    #   g/gs = група та її порядок (AttributeGroup) — блок рендериться згрупованою таблицею.
    #   n = назва БЕЗ одиниці, u = одиниця, v = значення (рядок, як показувати).
    # ⚡ ПРАВИЛО РЕНДЕРУ (INPUTS §2, жорстка вимога замовника):
    #     ліва колонка = `n`               → "Висота"
    #     права колонка = f"{v} {u}".strip() → "284 мм"        читається "Висота: 284 мм"
    #   НЕ "Висота (мм): 284". Одиниця клеїться до ЗНАЧЕННЯ, не до назви. Фронт НІКОЛИ не конкатенує
    #   `n` + `u`. Той самий формат — у порівнянні, у фіді Hotline і в search_vector (вага C).
    # ⚡ ru-версія: n/u/v беруться з *_ru (Attribute.name_ru / Unit.name_ru / AttributeOption.value_ru),
    #   а code/g-порядок/vn — ті самі. Тобто specs_json_ru — це ПЕРЕКЛАД ПІДПИСІВ, а не інша структура.
    filter_tokens = ArrayField(models.CharField(max_length=80), default=list, blank=True)
    # ⚡ ЄДИНЕ джерело і фільтрації, і лічильників фасетів (ADR-008). Токени:
    #   "brand:gorenje" | "country:sloveniia" | "avail:in_stock" | "cond:2" | "installment:1"
    #   "kamer:2" (option) | "obiem:300-399" (⚡ БАКЕТ range, ADR-007) | "morozylka:1" (bool)
    # ⚡ ТОКЕНИ — БЕЗ МОВИ (тільки slug). Одна колонка на обидві мови, один GIN-індекс,
    #   один набір лічильників. Перемикання uk↔ru не змінює вибірку. Підписи фасетів — з EAV.
    # ⚡ ФІЛЬТРУЄМО ТІЛЬКИ ХАРАКТЕРИСТИКИ. Опис (description) у filter_tokens не потрапляє ніколи.
    specs_num = models.JSONField(default=dict, blank=True)
    # {"obiem": 331.0, "shyryna": 60.0} — ⚡ ТІЛЬКИ payload + вхід для expression-індексів
    #   під filter_widget=range_slider. ⚡ GIN(jsonb_path_ops) ЗНЯТО (не прискорює нерівності).
    main_image_url = models.URLField(max_length=500, blank=True)

    search_vector = SearchVectorField(null=True, editable=False)  # ⚡ [tr] → _uk / _ru

    denorm_dirty = models.BooleanField(default=True, db_index=True)   # ⚡ CRITICAL
    denorm_at    = models.DateTimeField(null=True, blank=True, db_index=True)  # ⚡ CRITICAL
    # сигнал ставить dirty=True СИНХРОННО (в тій же транзакції); Celery-задача знімає.
    # beat heal_denorm кожні 5 хв добирає dirty → втрата on_commit-задачі більше не фатальна.

    class Meta:
        indexes = [
            models.Index(fields=["is_active", "category", "availability"], name="prod_list_idx"),
            models.Index(fields=["is_active", "category", "price"],        name="prod_price_idx"),
            models.Index(fields=["hotline_enabled", "is_active"],          name="prod_hotline_idx"),
            models.Index(fields=["source_currency", "price_locked"],       name="prod_usd_recalc_idx"),
            models.Index(fields=["price_source", "availability"],          name="prod_deactivate_idx"),  # ⚡
            models.Index(fields=["denorm_dirty"], condition=Q(denorm_dirty=True),
                         name="prod_dirty_idx"),                                                # ⚡ partial
            models.Index(fields=["name_normalized"], condition=~Q(name_normalized=""),          # ⚡ NEW
                         name="prod_name_norm_idx"),   # partial: фолбек-матчинг specs по назві
            GinIndex(fields=["filter_tokens"], name="prod_filter_gin"),
        ]
        constraints = [
            models.CheckConstraint(check=Q(price__gte=0),      name="prod_price_nonneg"),
            models.CheckConstraint(check=Q(base_price__gte=0), name="prod_base_price_nonneg"),
            models.CheckConstraint(
                check=~Q(availability="on_order") | Q(order_lead_days__isnull=False),
                name="prod_on_order_needs_lead_days"),
            models.CheckConstraint(                                                     # ⚡
                check=Q(old_price__isnull=True) | Q(old_price__gt=F("price")),
                name="prod_old_price_gt_price"),
            models.CheckConstraint(                                                     # ⚡
                check=~Q(source_currency="USD") | Q(usd_rate_used__isnull=False),
                name="prod_usd_needs_rate"),
        ]
```

> ⚠️ **Constraint'и — ДРУГА лінія оборони, не перша.** Усі ці перевірки дублюються в парсері (`parse_rows`),
> бо `CheckConstraint` + `bulk_create(batch_size=500)` = один битий рядок валить **весь батч** з `IntegrityError`,
> а з ним — усю транзакцію прогону. Див. SYNC.md §4 (валідація) і §6 (savepoint на батч).

**Перекладні поля Product (`translation.py`):**
`name`, `slug`, `description`, `short_description`, `condition_note`, `seo_title`, `seo_description`, `og_image`,
**`specs_json`**, **`search_vector`**.
> ⚡ `name_normalized`, `filter_tokens`, `specs_num`, `sku`, `mpn` — **НЕ** перекладні (це ключі й токени, не текст).

**Індекси в окремій міграції (після modeltranslation):**
```python
GinIndex(fields=["search_vector_uk"], name="prod_fts_uk_gin")
GinIndex(fields=["search_vector_ru"], name="prod_fts_ru_gin")
GinIndex(fields=["name_uk"], opclasses=["gin_trgm_ops"], name="prod_name_uk_trgm")
GinIndex(fields=["name_ru"], opclasses=["gin_trgm_ops"], name="prod_name_ru_trgm")   # ⚡ було лише uk
```

### 2.4 PriceHistory ⚡ NEW (critical)

```python
class PriceHistory(models.Model):
    """Append-only. Наповнюється PostgreSQL-ТРИГЕРОМ, не Python (ADR-006)."""
    class Reason(models.TextChoices):
        SYNC       = "sync",       "Синхронізація прайсу"
        RECALC     = "recalc",     "Перерахунок (курс/округлення/націнка)"
        MANUAL     = "manual",     "Ручна правка"
        ROLLBACK   = "rollback",   "Відкат прогону"
        IMPORT     = "import",     "Імпорт"

    id      = models.BigAutoField(primary_key=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="price_history")
    price           = models.DecimalField(max_digits=12, decimal_places=2)
    base_price      = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    source_currency = models.CharField(max_length=3, blank=True)
    usd_rate_used   = models.DecimalField(max_digits=10, decimal_places=4, null=True)
    markup_percent  = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    availability    = models.CharField(max_length=16, blank=True)
    reason  = models.CharField(max_length=10, choices=Reason.choices, default=Reason.MANUAL)
    run     = models.ForeignKey("sync.SyncRun", null=True, blank=True,
                                on_delete=models.SET_NULL, db_constraint=False)
    # db_constraint=False: тригер пише run_id як сирий uuid, FK-перевірка на рівні тригера зайва
    changed_at = models.DateTimeField(db_index=True)

    class Meta:
        indexes = [models.Index(fields=["product", "-changed_at"], name="ph_product_idx")]
```
Ретеншн — **2 роки**, чистка батчами (`purge_price_history`). При обсягах > 5М рядків — партиціонування по місяцях.

### 2.5 Атрибути — EAV + денормалізовані проєкції

```python
class AttributeGroup(TimeStampedModel):
    """⚡ Групи рендеряться як підзаголовки в блоці "Характеристики": "Основні:", "Габаритні розміри:"
       (INPUTS §2, скріни 169/170). Порядок груп — sort_order, всередині групи — Attribute.sort_order."""
    code       = models.SlugField(max_length=60, unique=True)   # "main", "dimensions"
    name       = models.CharField(max_length=120)      # [tr] "Основні", "Габаритні розміри"
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active  = models.BooleanField(default=True)

    class Meta:
        ordering = ("sort_order", "id")


class Unit(TimeStampedModel):                                        # ⚡ NEW (TRANSLATION.md §1)
    """
    Одиниця виміру — НОРМАЛІЗОВАНА таблиця, ~25 рядків. Не CharField.
    ЧОМУ: (1) одиниця перекладна (`міс`→`мес`, `год`→`ч`, `об/хв`→`об/мин`) і мусить перекластись
          РІВНО ОДИН РАЗ на весь каталог — інакше в RU-версії поїде "12 міс";
      (2) у таблиці характеристик одиниця лежить в ОКРЕМІЙ колонці трійки (INPUTS §3.3),
          тобто вона вже є окремою сутністю в джерелі — гріх її розчиняти в рядку;
      (3) її треба клеїти до ЗНАЧЕННЯ при рендері (§2.3 specs_json), а не до назви.
    """
    code    = models.SlugField(max_length=32, unique=True)   # "mm", "kg", "l", "month"
    name    = models.CharField(max_length=32)                # [tr] "мм" / "мм"; "міс" / "мес"
    aliases = ArrayField(models.CharField(max_length=32), default=list, blank=True)
    # ["мм", "mm", "мілім", "миллиметр"] — усе, що прилітає з колонки "Одиниця_виміру"
    needs_review = models.BooleanField(default=False, db_index=True)

    class Meta:
        indexes = [GinIndex(fields=["aliases"], name="unit_aliases_gin")]


class Attribute(TimeStampedModel):
    class ValueType(models.TextChoices):
        OPTION = "option", "Варіант зі списку"
        NUMBER = "number", "Число"
        BOOL   = "bool",   "Так/Ні"
        STRING = "string", "Текст"

    class FilterWidget(models.TextChoices):
        NONE          = "none",          "Не фільтрувати"
        CHECKBOX      = "checkbox",      "Чекбокси"
        SELECT        = "select",        "Випадаючий список"
        RANGE_BUCKETS = "range_buckets", "Діапазон (бакети)"   # ⚡ ДЕФОЛТ для NUMBER
        RANGE_SLIDER  = "range_slider",  "Діапазон (слайдер)"  # ⚡ escape hatch, потребує index

    class Role(models.TextChoices):                                       # ⚡ NEW
        NONE            = "",                "—"
        PACKAGE_DIMS    = "package_dims",    "Габарити упаковки (ВхШхГ)"
        PACKAGE_WEIGHT  = "package_weight",  "Вага в упаковці"
        WARRANTY_MONTHS = "warranty_months", "Гарантійний термін"
        COLOR           = "color",           "Колір (для кружечків-варіантів)"

    code  = models.SlugField(max_length=80, unique=True)      # "obiem"
    name  = models.CharField(max_length=160)                  # [tr] "Загальний об'єм"
    unit  = models.ForeignKey(Unit, null=True, blank=True,    # ⚡ БУЛО CharField [tr]
                              on_delete=models.PROTECT, related_name="attributes")
    # ⚡ Одиниця — FK на словник (див. Unit вище). У назві атрибута одиниці бути НЕ МОЖЕ:
    #   name="Висота" + unit="мм", а не name="Висота (мм)". Якщо синк побачив "Висота (мм)" —
    #   парсер відриває дужку в unit (services/specs.py::split_unit_from_name) і логує WARN.
    group = models.ForeignKey(AttributeGroup, on_delete=models.PROTECT, related_name="attributes")
    value_type = models.CharField(max_length=10, choices=ValueType.choices)

    role = models.CharField(max_length=16, choices=Role.choices, blank=True, default="")   # ⚡ NEW
    # ⚡ Ким ця характеристика працює, крім себе самої. Саме через role sync_specs заповнює
    #   Product.package_* і Product.warranty_months — БЕЗ хардкоду рядків "Габарити упаковки"
    #   у коді. Замовник переназве характеристику в таблиці → міняємо alias в адмінці, не деплой.

    aliases = ArrayField(models.CharField(max_length=160), default=list, blank=True)
    # ["Общий объем", "Обсяг загальний", "Об'єм, л"] — нормалізація сирих назв з прайсу/AI

    is_filterable = models.BooleanField(default=False, db_index=True)   # ⚠ ДЕФОЛТ False
    filter_widget = models.CharField(max_length=14, choices=FilterWidget.choices,
                                     default=FilterWidget.NONE)
    range_buckets = models.JSONField(default=list, blank=True)          # ⚡ NEW (ADR-007)
    # [[0,199],[200,299],[300,399],[400,null]] → токени "obiem:0-199", …, "obiem:400-plus"
    # Автогенерація по перцентилях: manage.py suggest_buckets --attr=obiem
    is_comparable = models.BooleanField(default=True)
    show_in_card  = models.BooleanField(default=True)
    sort_order    = models.PositiveSmallIntegerField(default=100)

    categories = models.ManyToManyField(Category, blank=True, related_name="attributes")
    # у яких категоріях показувати/фільтрувати (порожньо = у всіх)

    hotline_param_name = models.CharField(max_length=160, blank=True)   # → <param name="...">
    needs_review = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ("group__sort_order", "sort_order", "id")
        indexes = [GinIndex(fields=["aliases"], name="attr_aliases_gin")]
        constraints = [
            models.CheckConstraint(                                              # ⚡
                check=~Q(filter_widget__in=["range_buckets"]) | ~Q(range_buckets=[]),
                name="attr_buckets_required_for_range"),
            models.UniqueConstraint(fields=["role"], condition=~Q(role=""),      # ⚡ NEW
                                    name="uniq_attr_role"),
            # рівно ОДНА характеристика може бути джерелом габаритів упаковки / ваги / гарантії.
            # Дві "Габарити упаковки" в таблиці = недетермінований розмір посилки в НП.
        ]
```
> ⚡ **Фільтруються ТІЛЬКИ характеристики** (INPUTS §2). Ані `description`, ані `short_description`
> у фасети не потрапляють ніколи — навіть якщо в описі написано «No Frost». Хоче замовник фільтр —
> має з'явитись `Attribute`. Це не обмеження, це те, що рятує фасети від сміття.

> **Ключове правило (незмінне з v1):** атрибути й опції, створені **автоматично** (синком або ШІ),
> отримують `is_filterable=False, filter_widget=NONE, needs_review=True`. Вони одразу видно в картці
> й порівнянні, але **не потрапляють у фільтри**, поки контент-менеджер їх не схвалить.
> Це прямий фікс брудних фасетів старого сайту (`Sharp` vs `SHARP`, сирі кВт·год у фільтрах).

```python
class AttributeOption(TimeStampedModel):
    attribute = models.ForeignKey(Attribute, on_delete=models.CASCADE, related_name="options")
    value     = models.CharField(max_length=160)   # [tr] "Сріблястий" / "Серебристый"
    slug      = models.SlugField(max_length=180)   # "sriblyastyi" → токен "color:sriblyastyi"
    # ⚡ slug — НЕ [tr]. Він будується з UK-значення і є вічним ключем токена/URL фільтра.
    #   Перекладається тільки `value`. Тому /catalog/?color=sriblyastyi однаковий в обох мовах.
    aliases   = ArrayField(models.CharField(max_length=160), default=list, blank=True)
    sort_order   = models.PositiveSmallIntegerField(default=0)
    needs_review = models.BooleanField(default=False, db_index=True)

    # --- ⚡ для перемикача-кружечка (скріни 346/347), використовує VariantItem ---
    swatch_hex   = models.CharField(max_length=7, blank=True)   # "#C0C0C0"
    swatch_image = models.ImageField(upload_to="swatches/", blank=True)   # текстура (нерж. сталь, дерево)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["attribute", "slug"], name="uniq_attr_option")]
        indexes = [GinIndex(fields=["aliases"], name="attropt_aliases_gin")]


class ProductAttributeValue(TimeStampedModel):     # ⚡ БУЛО models.Model — тепер TimeStamped
    """EAV — ДЖЕРЕЛО ІСТИНИ для характеристик."""
    product   = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="attr_values")
    attribute = models.ForeignKey(Attribute, on_delete=models.CASCADE, related_name="values")

    option = models.ForeignKey(AttributeOption, null=True, blank=True,
                               on_delete=models.PROTECT, related_name="values")
    # ⚡ БУЛО CASCADE — і це знищувало б УСІ PAV товарів при видаленні дубля опції.
    #   PROTECT + заборона delete в адмінці + дія merge_into() (див. §2.2).

    value_string = models.CharField(max_length=500, blank=True)   # [tr]
    value_number = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    value_bool   = models.BooleanField(null=True)
    raw_value    = models.CharField(max_length=500, blank=True)   # як прийшло з прайсу (аудит)
    sort_order   = models.PositiveSmallIntegerField(default=0)
    source       = models.CharField(max_length=10, choices=ProductSource.choices,
                                    default=ProductSource.SHEET)   # ⚡ хто заповнив (sheet/manual/ai)

    class Meta:
        constraints = [
            # ⚡ ДВА часткові unique замість nulls_distinct=False (PG14-сумісність, ADR-016)
            models.UniqueConstraint(fields=["product", "attribute", "option"],
                                    condition=Q(option__isnull=False), name="uniq_pav_option"),
            models.UniqueConstraint(fields=["product", "attribute"],
                                    condition=Q(option__isnull=True), name="uniq_pav_scalar"),
        ]
        indexes = [
            models.Index(fields=["product", "attribute"], name="pav_card_idx"),
            models.Index(fields=["attribute", "value_number", "product"], name="pav_range_idx"),
            # ⚡ (attribute, value_number, product) — index-only scan для адмінських звітів
            #    і для suggest_buckets. Фасети його НЕ використовують (ADR-008).
            # ⚡ pav_facet_idx (attribute, option, product) ВИКИНУТО — фасети рахуються
            #    з filter_tokens, а не з EAV. Мінус індекс на найтовщій таблиці.
        ]
```

**Multi-value:** товар з двома кольорами = два рядки з різними `option` — часткові unique це дозволяють.
**Скалярні значення** (`NUMBER`/`STRING`/`BOOL`): `option = NULL`, унікальність по `(product, attribute)`.

#### Чому EAV І денормалізація одночасно

| Сценарій | Що обслуговує | Чому |
|---|---|---|
| Картка (30–60 характеристик) | `specs_json_{lang}` | 1 рядок замість JOIN на 60; нуль N+1 |
| Порівняння (6 товарів × 60) | `specs_json_{lang}` | один `filter(pk__in=ids)`, логіка same/different на клієнті |
| Hotline XML | `specs_json_uk` | стрімінг курсором, без мільйонів JOIN-рядків |
| **Фільтр (усі типи)** | `filter_tokens` (GIN) | `&&` на групу; **одне** джерело з лічильниками |
| **Лічильники фасетів** | `filter_tokens` (той самий!) | `unnest + GROUP BY` — узгодженість за побудовою (ADR-008) |
| Метадані фасета (назва, unit, порядок) | EAV | змінюється раз на тиждень, кешується надовго |
| Редагування, схвалення, злиття дублів | EAV | JSONB не має FK-цілісності і не редагується як структура |

### 2.6 ProductImage

```python
class ProductImage(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")

    source     = models.CharField(max_length=10, choices=ProductSource.choices,   # ⚡ NEW
                                  default=ProductSource.SHEET, db_index=True)
    # ⚡ БЕЗ цього поля неможливо відрізнити фото з прайсу від ручного → будь-яке
    #   автоприбирання зносило б роботу контент-менеджера.
    last_seen_run = models.UUIDField(null=True, blank=True, db_index=True)         # ⚡ NEW

    source_url      = models.URLField(max_length=1000, blank=True)
    source_url_hash = models.CharField(max_length=64, blank=True, db_index=True)   # sha256(url)
    etag            = models.CharField(max_length=255, blank=True)   # conditional GET → 304
    content_hash    = models.CharField(max_length=64, blank=True)    # sha256(bytes)

    file       = models.ImageField(upload_to="products/orig/%Y/%m/")
    file_large = models.ImageField(upload_to="products/lg/%Y/%m/", blank=True)   # 1600px webp
    file_card  = models.ImageField(upload_to="products/md/%Y/%m/", blank=True)   #  700px webp
    file_thumb = models.ImageField(upload_to="products/sm/%Y/%m/", blank=True)   #  300px webp
    # Імена файлів — ЛАТИНИЦЯ: {url_hash[:16]}_md.webp. Вимога Hotline: кирилиця = товар без фото.

    alt      = models.CharField(max_length=255, blank=True)   # [tr]
    position = models.PositiveSmallIntegerField(default=0)
    is_main  = models.BooleanField(default=False)
    width    = models.PositiveIntegerField(null=True, blank=True)
    height   = models.PositiveIntegerField(null=True, blank=True)

    downloaded_at = models.DateTimeField(null=True, blank=True, db_index=True)
    failed_reason = models.CharField(max_length=255, blank=True, db_index=True)
    attempts      = models.PositiveSmallIntegerField(default=0)          # ⚡ NEW
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)   # ⚡ NEW

    class Meta:
        ordering = ("position", "id")
        constraints = [
            models.UniqueConstraint(                                          # ⚡ ЧАСТКОВИЙ
                fields=["product", "source_url_hash"],
                condition=~Q(source_url_hash=""),
                name="uniq_product_image_url"),
            # ⚡ БУЛО без condition → два РУЧНІ фото одного товару (source_url="") давали
            #   однаковий hash → IntegrityError на другому. Контент-менеджер не міг залити галерею.
            models.UniqueConstraint(fields=["product"], condition=Q(is_main=True),
                                    name="uniq_main_image"),
        ]
        indexes = [models.Index(fields=["product", "source", "last_seen_run"], name="img_sync_idx")]
```

> ⚠️ `uniq_main_image` — **partial unique index, який НЕ може бути DEFERRABLE**. Тому зміна головного фото
> завжди двокрокова. Реалізація — **тільки** через `services/images.py::set_main_image(product, image)`:
> `product.images.filter(is_main=True).exclude(pk=image.pk).update(is_main=False)` → `image.is_main=True`,
> усе в одній транзакції. В `ProductAdmin` перевизначити `save_formset` — інакше inline-формсет,
> що зберігає рядки по одному в довільному порядку, впаде з IntegrityError **приблизно завжди**.

**Прибирання застарілих фото (⚡):** після синку товару
`product.images.filter(source="sheet").exclude(last_seen_run=run.id).delete()` — **ручні фото не чіпаються ніколи**.
Файли з R2 підчищає окрема задача `purge_orphan_media` (не в транзакції).

### 2.7 VariantGroup / RelatedGroup

**Вісь варіанта — БУДЬ-ЯКА характеристика, не лише діагональ.** На референсах замовника є і перемикач
діагоналей (`50" (127 см)` / `55"` / `65"`, скрін 384), і перемикач **кольору** кружечками (скріни 346/347).
Тому вісь задається через `axis_attribute` (FK на `Attribute`), а спосіб відображення — через `widget`.

```python
class VariantGroup(TimeStampedModel):
    class Widget(models.TextChoices):
        BUTTONS = "buttons", "Кнопки (діагональ, об'єм)"
        SWATCHES = "swatches", "Кружечки (колір)"

    name = models.CharField(max_length=200)   # [tr] "Samsung QLED Q60D"
    # вісь перемикача: Attribute("Діагональ екрану") або Attribute("Колір виробу")
    axis_attribute = models.ForeignKey(Attribute, null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name="variant_groups")
    widget    = models.CharField(max_length=16, choices=Widget.choices, default=Widget.BUTTONS)
    is_active = models.BooleanField(default=True)


class VariantItem(models.Model):
    """Членство товару в групі варіантів. Винесено з Product (див. §2.3) — товар може
    належати ДВОМ осям одночасно (та сама модель ТВ: і діагональ, і колір)."""
    group      = models.ForeignKey(VariantGroup, on_delete=models.CASCADE, related_name="items")
    product    = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variant_memberships")
    label      = models.CharField(max_length=80)   # [tr] '50" (127 см)' / 'Чорний'
    swatch_hex = models.CharField(max_length=7, blank=True)   # тільки для widget=SWATCHES
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["group", "product"], name="uniq_variant_member"),
        ]
        indexes = [models.Index(fields=["group", "sort_order"])]
        ordering = ("sort_order", "id")

# Перемикач у картці = group.items.filter(product__is_active=True).order_by("sort_order")
# Активний елемент — той, чий product == поточний товар.


class RelatedGroup(TimeStampedModel):
    class Kind(models.TextChoices):
        SET        = "set",        "Комплект (взаємно)"
        ACCESSORY  = "accessory",  "Аксесуари"
        CROSS_SELL = "cross_sell", "Разом купують"
    name       = models.CharField(max_length=200)   # [tr]
    kind       = models.CharField(max_length=16, choices=Kind.choices, default=Kind.SET)
    is_active  = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)


class RelatedGroupItem(models.Model):
    group      = models.ForeignKey(RelatedGroup, on_delete=models.CASCADE, related_name="items")
    product    = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="related_memberships")
    role       = models.CharField(max_length=60, blank=True)   # [tr] "Духова шафа"
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["group", "product"], name="uniq_group_product")]
        indexes = [models.Index(fields=["product", "group"])]
```
**Взаємність — властивість структури.** Контент-менеджер збирає ОДНУ групу з 3 товарів — усі троє
автоматично бачать двох інших. Асиметрія неможлива в принципі.

---

## 3. `sync`

### 3.1 PriceSource / PriceSheet / SpecSheet

```python
class PriceSource(TimeStampedModel):
    class Kind(models.TextChoices):
        GOOGLE_SHEET = "gsheet", "Google Sheets"
        XML_FEED     = "xml",    "XML-фід постачальника"
        CSV_FILE     = "csv",    "CSV/XLSX"

    code = models.SlugField(max_length=60, unique=True)      # "complex-main", "supplier-alpha"
    name = models.CharField(max_length=160)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.GOOGLE_SHEET)

    is_primary = models.BooleanField(default=False)          # власний склад Complex
    is_active  = models.BooleanField(default=True)
    priority   = models.SmallIntegerField(default=100)
    # МЕНШИЙ priority виграє. ⚡ Тепер це РЕАЛЬНО працює — через SupplierOffer + project_offers()

    default_availability = models.CharField(max_length=16, choices=Availability.choices,
                                            default=Availability.IN_STOCK)
    default_lead_days    = models.PositiveSmallIntegerField(null=True, blank=True)
    price_markup_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    overwrite_names      = models.BooleanField(default=False)

    spreadsheet_id       = models.CharField(max_length=120, blank=True)
    feed_url             = models.URLField(blank=True)

    # --- ЗАПОБІЖНИКИ ---
    guard_min_rows_ratio      = models.DecimalField(max_digits=3, decimal_places=2,
                                                    default=Decimal("0.50"))
    guard_max_deactivate_pct  = models.DecimalField(max_digits=5, decimal_places=2,   # ⚡ NEW
                                                    default=Decimal("20.00"))
    guard_max_median_price_pct= models.DecimalField(max_digits=5, decimal_places=2,   # ⚡ NEW
                                                    default=Decimal("30.00"))

    class Meta:
        constraints = [
            models.CheckConstraint(                                                   # ⚡ NEW
                check=~Q(default_availability="on_order") | Q(default_lead_days__isnull=False),
                name="src_on_order_needs_lead_days"),
            models.UniqueConstraint(fields=["is_primary"], condition=Q(is_primary=True),
                                    name="uniq_primary_source"),
        ]

    def clean(self):
        # ⚡ fail-fast у формі адмінки: інакше bulk_create падає з IntegrityError на КОЖНОМУ батчі
        #    і прогін = FAILED цілком, з діагностикою рівня «порушено constraint»
        if self.default_availability == Availability.ON_ORDER and self.default_lead_days is None:
            raise ValidationError({"default_lead_days": "Обов'язкове для «Під замовлення»"})


class PriceSheet(TimeStampedModel):
    price_source = models.ForeignKey(PriceSource, on_delete=models.CASCADE, related_name="sheets")
    tab_name     = models.CharField(max_length=120)     # "UAH" / "USD"
    currency     = models.CharField(max_length=3, choices=SourceCurrency.choices)
    column_map   = models.JSONField(default=dict)
    # {"sku":"Артикул","name":"Найменування","price":"Ціна","qty":"К-сть","currency":"Валюта",
    #  "category":"Категорія","brand":"Виробник","country":"Країна_виробник","photo":"Фото",
    #  "mpn":"Артикул виробника",       ← ⚡ ОПЦІЙНИЙ ключ
    #  "old_price":"Стара ціна"}        ← ⚡ ОПЦІЙНИЙ ключ
    # ⚠️ "qty" — ОПЦІЙНИЙ ключ. Немає колонки → availability = source.default_availability
    is_active  = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)   # переможець при дублі sku між листами

    class Meta:
        constraints = [models.UniqueConstraint(fields=["price_source", "tab_name"],
                                               name="uniq_sheet_tab")]


class SpecSheet(TimeStampedModel):
    price_source   = models.ForeignKey(PriceSource, on_delete=models.CASCADE,
                                       related_name="spec_sheets")
    spreadsheet_id = models.CharField(max_length=120)
    tab_name       = models.CharField(max_length=120)
    column_map     = models.JSONField(default=dict)
    # {"sku":"Артикул","name":"Найменування","country":"Країна-виробник",
    #  "package_dims":"Розміри в упаковці","image":"Зображення","category":"Категорія",
    #  "mpn":"Артикул виробника",
    #  "spec_triplet_start":"Назва_Характеристики"}  ← далі трійки читаються ПОЗИЦІЙНО
    is_active = models.BooleanField(default=True)
```
> `column_map` в адмінці = **зміна заголовків у таблиці правиться без деплою**.

### 3.2 SupplierOffer ⚡ NEW — ключова таблиця v2 (critical)

```python
class SupplierOffer(models.Model):
    """
    Пропозиція КОНКРЕТНОГО постачальника по КОНКРЕТНОМУ sku.
    Синк пише ТІЛЬКИ сюди. У Product дані потрапляють через project_offers().

    ЩО ЦЕ ЛАМАЛО в v1 (три бомби в одній):
      (а) sku глобально унікальний на Product → два прайси мапляться в ОДИН рядок;
      (б) ON CONFLICT (sku) DO UPDATE не має перевірки priority → виграє той, хто синкнувся ОСТАННІМ
          (прайс постачальника з націнкою 15% і "під замовлення 14 днів" перезаписував
           складський товар Complex "в наявності" — прямі збитки, тихо);
      (в) price_source / last_seen_run — ОДИНИЧНІ колонки: після перезапису price_source
          прогін Complex робив filter(price_source=complex) і цього товару ВЖЕ НЕ БАЧИВ →
          він ніколи не деактивувався, а прогін постачальника деактивував товари Complex.
    """
    id           = models.BigAutoField(primary_key=True)
    price_source = models.ForeignKey(PriceSource, on_delete=models.CASCADE, related_name="offers")
    sku          = models.CharField(max_length=64, db_index=True)

    # --- сирі дані прайсу (як прийшло) ---
    name            = models.CharField(max_length=255, blank=True)
    base_price      = models.DecimalField(max_digits=12, decimal_places=2)
    base_old_price  = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    source_currency = models.CharField(max_length=3, choices=SourceCurrency.choices)
    stock_qty       = models.IntegerField(null=True, blank=True)
    availability    = models.CharField(max_length=16, choices=Availability.choices)
    order_lead_days = models.PositiveSmallIntegerField(null=True, blank=True)

    category_ext_id = models.CharField(max_length=32, blank=True)
    brand_raw       = models.CharField(max_length=160, blank=True)
    country_raw     = models.CharField(max_length=160, blank=True)
    mpn             = models.CharField(max_length=64, blank=True)
    photo_urls      = ArrayField(models.URLField(max_length=1000), default=list, blank=True)
    raw             = models.JSONField(default=dict, blank=True)   # повний рядок (форензика)

    # --- службові ---
    is_active     = models.BooleanField(default=True, db_index=True)
    # False = оффер зник з прайсу (НЕ видаляємо — потрібен для аудиту й проєкції)
    last_seen_run = models.UUIDField(null=True, blank=True, db_index=True)
    sheet         = models.CharField(max_length=60, blank=True)   # "UAH" / "USD"
    row_number    = models.IntegerField(null=True, blank=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["price_source", "sku"], name="uniq_offer_source_sku"),
            models.CheckConstraint(check=Q(base_price__gt=0), name="offer_price_positive"),
        ]
        indexes = [
            models.Index(fields=["price_source", "last_seen_run"], name="offer_run_idx"),
            models.Index(fields=["sku", "is_active"],              name="offer_sku_idx"),
        ]
```

**Проєкція `project_offers(sku_list)` — SQL (SYNC.md §7):**
переможець = `DISTINCT ON (sku) … ORDER BY sku, ps.priority, ps.is_primary DESC, o.id`
(тільки `is_active=True` оффери активних джерел).

### 3.3 ProductPriceSnapshot ⚡ NEW (critical — відкат)

```python
class ProductPriceSnapshot(models.Model):
    """
    Знімок ПЕРЕД проєкцією. COPY 10k рядків = мілісекунди.
    Дозволяє відкотити прогін, який пройшов guard, але зіпсував каталог
    (класика: «Артур зсунув колонку К-сть» — рядків стільки ж, guard мовчить,
     10k товарів стають OUT_OF_STOCK, а відкоту в v1 не було ВЗАГАЛІ).
    """
    id       = models.BigAutoField(primary_key=True)
    run      = models.ForeignKey("SyncRun", on_delete=models.CASCADE, related_name="snapshots")
    product  = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, db_constraint=False)

    old_base_price      = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    old_price           = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    old_old_price       = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    old_source_currency = models.CharField(max_length=3, blank=True)
    old_markup_percent  = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    old_usd_rate_used   = models.DecimalField(max_digits=10, decimal_places=4, null=True)
    old_availability    = models.CharField(max_length=16, blank=True)
    old_order_lead_days = models.PositiveSmallIntegerField(null=True)
    old_stock_qty       = models.IntegerField(null=True)
    old_synced_at       = models.DateTimeField(null=True)

    class Meta:
        indexes = [models.Index(fields=["run", "product"], name="snap_run_idx")]
```
**Відкат (`SyncRunAdmin` → дія «Відкотити прогін»):** `UPDATE` назад **лише для товарів, у яких
`Product.synced_at` досі дорівнює `run.finished_at`-мітці цього прогону** — тобто їх ніхто не змінював після.
Ретеншн — 30 днів (`purge_snapshots`).

### 3.4 SyncRun / SyncLogEntry

```python
class SyncRun(models.Model):
    class Kind(models.TextChoices):
        SHEETS_PRICES = "sheets_prices", "Прайс з Google Sheets"
        SHEETS_SPECS  = "sheets_specs",  "Характеристики з Google Sheets"
        NP_REFS       = "np_refs",       "Довідники Нової Пошти"
        HOTLINE_FEED  = "hotline_feed",  "Генерація фіда Hotline"
        PRICE_RECALC  = "price_recalc",  "Перерахунок цін"        # ⚡ було usd_recalc
        ROLLBACK      = "rollback",      "Відкат прогону"          # ⚡ NEW

    class Status(models.TextChoices):
        RUNNING = "running", "Виконується"
        SUCCESS = "success", "Успішно"
        PARTIAL = "partial", "Успішно з помилками"
        FAILED  = "failed",  "Помилка"
        ABORTED = "aborted", "Зупинено запобіжником"
        SKIPPED = "skipped", "Пропущено (вже виконується)"

    class Trigger(models.TextChoices):
        CRON = "cron", "За розкладом"
        MANUAL = "manual", "Вручну з адмінки"
        API = "api", "API"
        HEAL = "heal", "Самозагоювання"       # ⚡ NEW

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind         = models.CharField(max_length=20, choices=Kind.choices, db_index=True)
    price_source = models.ForeignKey(PriceSource, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="runs")
    trigger      = models.CharField(max_length=10, choices=Trigger.choices, default=Trigger.CRON)
    started_by   = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL)
    dry_run      = models.BooleanField(default=False)
    forced       = models.BooleanField(default=False)   # ⚡ «Застосувати попри попередження»
    rolled_back_run = models.ForeignKey("self", null=True, blank=True,                 # ⚡
                                        on_delete=models.SET_NULL, related_name="rollbacks")

    status      = models.CharField(max_length=10, choices=Status.choices,
                                   default=Status.RUNNING, db_index=True)
    started_at  = models.DateTimeField(auto_now_add=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    rows_read            = models.IntegerField(default=0)
    rows_expected        = models.IntegerField(default=0)   # ⚡ з spreadsheets.get (звірка повноти)
    products_created     = models.IntegerField(default=0)
    products_updated     = models.IntegerField(default=0)
    products_deactivated = models.IntegerField(default=0)
    rows_skipped         = models.IntegerField(default=0)
    rows_failed          = models.IntegerField(default=0)
    rows_duplicated      = models.IntegerField(default=0)   # ⚡ дублі sku
    images_queued        = models.IntegerField(default=0)
    specs_filled         = models.IntegerField(default=0)
    categories_missing   = ArrayField(models.CharField(max_length=32), default=list, blank=True)

    guard_report = models.JSONField(default=dict, blank=True)   # ⚡ чому ABORTED, з цифрами
    # {"rows": 4100, "alive": 9800, "ratio": 0.42, "min_ratio": 0.5,
    #  "deactivate_pct": 58.2, "median_price_delta_pct": 0.0}
    raw_snapshot_key = models.CharField(max_length=200, blank=True)   # ⚡ gzip CSV у R2

    stats     = models.JSONField(default=dict, blank=True)
    traceback = models.TextField(blank=True)

    class Meta:
        ordering = ("-started_at",)
        indexes = [models.Index(fields=["kind", "-started_at"]),
                   models.Index(fields=["status", "-started_at"])]


class SyncLogEntry(models.Model):
    class Level(models.TextChoices):
        INFO = "info", "Інфо"; WARN = "warn", "Попередження"; ERROR = "error", "Помилка"

    class Action(models.TextChoices):
        CREATED = "created", "Створено"
        UPDATED = "updated", "Оновлено"
        DEACTIVATED = "deactivated", "Знято з наявності"
        SKIPPED = "skipped", "Пропущено"
        DUPLICATE_SKU = "duplicate_sku", "Дубль артикула"          # ⚡
        LOW_PRIORITY  = "low_priority",  "SKU належить пріоритетнішому джерелу"  # ⚡
        CATEGORY_MISSING = "category_missing", "Немає категорії"
        CATEGORY_REMAPPED = "category_remapped", "Категорію підібрано"           # ⚡
        BRAND_CREATED = "brand_created", "Створено бренд"
        ATTR_CREATED = "attr_created", "Створено характеристику"
        SPEC_FILLED = "spec_filled", "Заповнено характеристики"
        IMAGE_FAILED = "image_failed", "Фото не завантажилось"
        PRICE_CHANGED = "price_changed", "Змінилась ціна"
        ROW_INVALID = "row_invalid", "Невалідний рядок"            # ⚡

    id      = models.BigAutoField(primary_key=True)
    run     = models.ForeignKey(SyncRun, on_delete=models.CASCADE, related_name="entries")
    level   = models.CharField(max_length=6, choices=Level.choices, default=Level.INFO)
    action  = models.CharField(max_length=20, choices=Action.choices)
    sheet   = models.CharField(max_length=60, blank=True)
    row_number = models.IntegerField(null=True, blank=True)
    sku     = models.CharField(max_length=64, blank=True, db_index=True)
    product = models.ForeignKey("catalog.Product", null=True, blank=True, on_delete=models.SET_NULL)
    message = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["run", "level"]),
                   models.Index(fields=["sku", "-created_at"])]
```
> **Ретеншн.** Пишемо `WARN`/`ERROR` завжди; `CREATED`/`DEACTIVATED`/`DUPLICATE_SKU` завжди;
> `PRICE_CHANGED` **не пишемо взагалі** (це тепер робота `PriceHistory`-тригера); решту `INFO` — не пишемо.
> `purge_sync_logs` — 90 днів, **батчами по 10k з комітами** (звичайний `DELETE` на мільйонах рядків
> з трьома індексами = довга транзакція, bloat і autovacuum-шторм).

### 3.5 UsdRateChange

```python
class UsdRateChange(TimeStampedModel):
    old_rate = models.DecimalField(max_digits=10, decimal_places=4, null=True)
    new_rate = models.DecimalField(max_digits=10, decimal_places=4, null=True)
    old_rounding = models.CharField(max_length=6, blank=True)   # ⚡ округлення теж тригерить recalc
    new_rounding = models.CharField(max_length=6, blank=True)   # ⚡
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    products_recalculated = models.IntegerField(default=0)
    run = models.ForeignKey(SyncRun, null=True, blank=True, on_delete=models.SET_NULL)
```
> ⚡ Пишеться **тільки якщо значення реально змінилось** (diff у `SiteSettings.save()`).
> У v1 сигнал `post_save` на синглтоні спрацьовував на **будь-якому** збереженні (зміна телефону, SEO-опису)
> і запускав важкий recalc + сміттєвий рядок `UsdRateChange(old == new)`.

---

## 4. `orders`

```python
class Order(TimeStampedModel):
    class Status(models.TextChoices):
        NEW = "new", "Новий";                 CONFIRMED = "confirmed", "Підтверджений"
        PACKED = "packed", "Скомплектований"; SHIPPED = "shipped", "Відправлений"
        DELIVERED = "delivered", "Доставлений"; DONE = "done", "Завершений"
        CANCELLED = "cancelled", "Скасований";  RETURNED = "returned", "Повернення"

    class DeliveryMethod(models.TextChoices):
        NP_WAREHOUSE = "np_warehouse", "Відділення Нової Пошти"
        NP_POSTOMAT  = "np_postomat",  "Поштомат Нової Пошти"
        NP_COURIER   = "np_courier",   "Кур'єр Нової Пошти"
        PICKUP       = "pickup",       "Самовивіз (Ужгород)"
        LOCAL_COURIER= "local_courier","Кур'єр по Ужгороду"      # ⚡ був на старому сайті

    class PaymentMethod(models.TextChoices):
        COD = "cod", "Накладений платіж";  PREPAY = "prepay", "Повна передоплата (реквізити)"
        ONLINE = "online", "Онлайн-оплата картою"
        INSTALLMENT = "installment", "Оплата частинами"

    class PaymentStatus(models.TextChoices):
        NOT_REQUIRED = "not_required", "Не потрібна"
        PENDING = "pending", "Очікує";  PAID = "paid", "Оплачено"
        FAILED = "failed", "Помилка";   REFUNDED = "refunded", "Повернуто"

    number = models.CharField(max_length=24, unique=True)          # "CMPX-260711-0042"
    # ⚡ ГЕНЕРУЄТЬСЯ З SEQUENCE: nextval('order_number_seq') → f"CMPX-{d:%y%m%d}-{n:04d}"
    #   Було count()+1 → гонка: два одночасні checkout'и беруть один номер, другий падає
    #   з IntegrityError ВЖЕ ПІСЛЯ створення платежу.
    public_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)   # ⚡ CRITICAL
    # ⚡ Публічний роут — /order/{public_token}, НЕ /order/{number}.
    #   Було: IDOR — перебір CMPX-260711-0001..0100 віддавав ПІБ/телефон/адресу будь-кого.
    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.NEW, db_index=True)
    idempotency_key = models.UUIDField(unique=True)

    # --- покупець ---
    last_name  = models.CharField(max_length=80)
    first_name = models.CharField(max_length=80)
    phone      = models.CharField(max_length=20, db_index=True)   # нормалізований +380XXXXXXXXX
    email      = models.EmailField(blank=True)
    # ⚠️ blank=True — свідомий ДЕФОЛТ (див. OPEN_QUESTIONS Q-ORD-1). Якщо payment_method=online —
    #   email ОБОВ'ЯЗКОВИЙ (перевірка в checkout-сервісі + CheckConstraint нижче).
    comment    = models.TextField(blank=True)

    # --- доставка ---
    delivery_method   = models.CharField(max_length=14, choices=DeliveryMethod.choices)
    np_area_ref       = models.CharField(max_length=36, blank=True)
    np_area_name      = models.CharField(max_length=120, blank=True)
    np_city_ref       = models.CharField(max_length=36, blank=True)   # канонічний DeliveryCity ref
    np_settlement_ref = models.CharField(max_length=36, blank=True)   # ≠ CityRef!
    np_city_name      = models.CharField(max_length=200, blank=True)  # готовий Present
    np_warehouse_ref  = models.CharField(max_length=36, blank=True)
    np_warehouse_name = models.CharField(max_length=255, blank=True)
    np_service_type   = models.CharField(max_length=24, blank=True)
    # WarehouseWarehouse | WarehousePostomat | WarehouseDoors
    delivery_address  = models.CharField(max_length=255, blank=True)
    pickup_point      = models.ForeignKey("cms.PickupPoint", null=True, blank=True,
                                          on_delete=models.SET_NULL)
    delivery_cost_estimate = models.DecimalField(max_digits=10, decimal_places=2,
                                                 null=True, blank=True)
    # NULL — легальний стан: circuit breaker НП відкритий → «вартість повідомить менеджер»
    ttn = models.CharField(max_length=20, blank=True, db_index=True)

    # --- оплата ---
    payment_method = models.CharField(max_length=12, choices=PaymentMethod.choices)
    payment_status = models.CharField(max_length=12, choices=PaymentStatus.choices,
                                      default=PaymentStatus.NOT_REQUIRED, db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    # --- суми (ЗАВЖДИ перераховуються на сервері) ---
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total    = models.DecimalField(max_digits=12, decimal_places=2)

    manager_note = models.TextField(blank=True)
    utm = models.JSONField(default=dict, blank=True)
    ip  = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=400, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["status", "-created_at"]),
                   models.Index(fields=["payment_status", "-created_at"])]
        constraints = [
            models.CheckConstraint(
                check=~Q(delivery_method__in=["np_warehouse", "np_postomat"])
                      | (Q(np_city_ref__gt="") & Q(np_warehouse_ref__gt="")),
                name="order_np_refs_required"),
            models.CheckConstraint(                                                    # ⚡
                check=~Q(payment_method__in=["online", "installment"]) | ~Q(email=""),
                name="order_online_needs_email"),
            models.CheckConstraint(check=Q(total__gte=0), name="order_total_nonneg"),
        ]


class OrderItem(models.Model):
    order   = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", null=True, on_delete=models.SET_NULL)

    # СНАПШОТ на момент замовлення — чек не має «пливти»
    sku        = models.CharField(max_length=64)
    name       = models.CharField(max_length=255)
    price      = models.DecimalField(max_digits=12, decimal_places=2)
    quantity   = models.PositiveSmallIntegerField(default=1)
    line_total = models.GeneratedField(
        expression=F("price") * F("quantity"),
        output_field=models.DecimalField(max_digits=12, decimal_places=2), db_persist=True)
    image_url  = models.URLField(max_length=500, blank=True)
    installment_available = models.BooleanField(default=False)

    # ⚡ ЕФЕКТИВНІ габарити (те, що РЕАЛЬНО пішло в НП), а не сирі з Product:
    #    effective_dims() = товар → фолбек Category.default_* → глобальний дефолт (ADR-021)
    weight_kg = models.DecimalField(max_digits=7, decimal_places=3, null=True)
    volume_m3 = models.DecimalField(max_digits=10, decimal_places=5, null=True)
    dims_source = models.CharField(max_length=10, blank=True)   # product | category | default

    class Meta:
        constraints = [models.CheckConstraint(check=Q(quantity__gte=1), name="oi_qty_positive")]


class OrderStatusHistory(models.Model):
    order       = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="history")
    from_status = models.CharField(max_length=12, blank=True)
    to_status   = models.CharField(max_length=12)
    changed_by  = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    comment     = models.TextField(blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
```

**Інваріант «оплата частинами»** (`orders/services/checkout.py::validate_installment`) викликається
**з трьох місць**: API-checkout, `Order.clean()` (адмінка), і **перед створенням Payment**:
```python
if order.payment_method == PaymentMethod.INSTALLMENT and \
   order.items.filter(installment_available=False).exists():
    raise ValidationError("Оплата частинами доступна лише якщо ВСІ товари її підтримують")
```

---

## 5. `payments`

```python
class Payment(TimeStampedModel):
    class Provider(models.TextChoices):
        MONO = "mono", "monobank (plata by mono)"
        LIQPAY = "liqpay", "LiqPay"
        MANUAL = "manual", "Ручне підтвердження"

    class Status(models.TextChoices):
        CREATED = "created", "Створено";  PENDING = "pending", "Очікує"
        PAID = "paid", "Оплачено";        FAILED = "failed", "Помилка"
        REVERSED = "reversed", "Скасовано"; REFUNDED = "refunded", "Повернуто"
        EXPIRED = "expired", "Протерміновано"     # ⚡ >48 год у PENDING

    order    = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="payments")
    provider = models.CharField(max_length=10, choices=Provider.choices)
    provider_invoice_id = models.CharField(max_length=120, blank=True, db_index=True)
    amount   = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="UAH")
    status   = models.CharField(max_length=10, choices=Status.choices,
                                default=Status.CREATED, db_index=True)
    payment_url  = models.URLField(max_length=500, blank=True)
    raw_request  = models.JSONField(default=dict, blank=True)   # ⚡ РЕДАКОВАНО по allowlist
    raw_response = models.JSONField(default=dict, blank=True)   # ⚡ РЕДАКОВАНО по allowlist
    # ⚡ Ніяких токенів/підписів/PAN у БД: providers/base.py::redact(payload, allowlist)
    error_message = models.CharField(max_length=500, blank=True)
    last_polled_at = models.DateTimeField(null=True, blank=True, db_index=True)   # ⚡ reconcile

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "provider_invoice_id"],
                                    condition=~Q(provider_invoice_id=""),
                                    name="uniq_provider_invoice"),
        ]
        indexes = [models.Index(fields=["status", "created_at"], name="pay_reconcile_idx")]  # ⚡


class PaymentWebhookEvent(models.Model):
    provider   = models.CharField(max_length=10)
    invoice_id = models.CharField(max_length=120, blank=True, db_index=True)
    status     = models.CharField(max_length=20, blank=True)     # ⚡ NEW
    body_hash  = models.CharField(max_length=64)                 # sha256(raw body)
    raw_body   = models.TextField()      # потрібен для перевірки підпису; ретеншн 90 днів
    headers    = models.JSONField(default=dict)
    signature_valid = models.BooleanField(default=False)
    processed  = models.BooleanField(default=False, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    error      = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "body_hash"], name="uniq_webhook_body"),
            models.UniqueConstraint(fields=["provider", "invoice_id", "status"],       # ⚡ CRITICAL
                                    condition=~Q(invoice_id=""),
                                    name="uniq_webhook_invoice_status"),
            # ⚡ Другий ключ НЕ залежить від timestamp у тілі. Перший (body_hash) ловить точний дубль,
            #   але якщо провайдер кладе в тіло час — hash завжди різний і дедуп не спрацює взагалі.
        ]
```

**Стан-машина (незворотні переходи):** `PAID` і `REFUNDED` — фінальні.
```python
updated = Payment.objects.filter(pk=p.pk, status__in=["created", "pending"]).update(status="paid")
if not updated:      # 0 рядків → подія прийшла не по порядку (pending ПІСЛЯ paid) → ігноруємо
    log.info("stale webhook ignored", ...)
```
**Звірка суми:** `payment.amount == order.total and payment.currency == "UAH"` — інакше
`signature_valid=True, processed=False, error="amount mismatch"` + **алерт**, гроші **не проводимо**.

---

## 6. `delivery`

```python
class NPArea(models.Model):
    ref  = models.CharField(max_length=36, primary_key=True)
    name = models.CharField(max_length=120)
    center_city_ref = models.CharField(max_length=36, blank=True)


class NPCity(models.Model):
    ref  = models.CharField(max_length=36, primary_key=True)   # = DeliveryCity ref (КАНОН)
    area = models.ForeignKey(NPArea, on_delete=models.CASCADE, related_name="cities")
    name = models.CharField(max_length=200)
    settlement_type = models.CharField(max_length=60, blank=True)
    is_active     = models.BooleanField(default=True, db_index=True)   # ⚡
    last_seen_run = models.UUIDField(null=True, blank=True, db_index=True)  # ⚡


class NPSettlement(models.Model):
    ref  = models.CharField(max_length=36, primary_key=True)   # SettlementRef ≠ CityRef!
    delivery_city_ref = models.CharField(max_length=36, db_index=True)  # ← КАНОН для API
    area = models.ForeignKey(NPArea, on_delete=models.CASCADE, related_name="settlements")
    name    = models.CharField(max_length=200)
    present = models.CharField(max_length=255)   # "м. Ужгород, Закарпатська обл." — для дропдауна
    settlement_type_code = models.CharField(max_length=16, blank=True)
    is_active     = models.BooleanField(default=True, db_index=True)
    last_seen_run = models.UUIDField(null=True, blank=True, db_index=True)   # ⚡ CRITICAL

    class Meta:
        indexes = [
            GinIndex(fields=["present"], opclasses=["gin_trgm_ops"], name="np_settl_trgm"),
            models.Index(fields=["area", "name"]),
        ]


class NPWarehouse(models.Model):
    class Category(models.TextChoices):
        BRANCH = "Branch", "Відділення"
        POSTOMAT = "Postomat", "Поштомат"
        DROPOFF = "DropOff", "Пункт видачі"

    ref  = models.CharField(max_length=36, primary_key=True)
    city = models.ForeignKey(NPCity, on_delete=models.SET_NULL, related_name="warehouses",
                             null=True, db_constraint=False)
    city_ref       = models.CharField(max_length=36, db_index=True)
    settlement_ref = models.CharField(max_length=36, db_index=True, blank=True)
    number        = models.CharField(max_length=12)
    description   = models.CharField(max_length=300)   # "Відділення №1: вул. Волонтерів, 16"
    short_address = models.CharField(max_length=200, blank=True)
    category  = models.CharField(max_length=10, choices=Category.choices, db_index=True)
    type_ref  = models.CharField(max_length=36, blank=True)
    place_max_weight_kg = models.DecimalField(max_digits=8, decimal_places=2, null=True)
    max_width_cm  = models.DecimalField(max_digits=6, decimal_places=1, null=True)  # поштомат 40
    max_height_cm = models.DecimalField(max_digits=6, decimal_places=1, null=True)  # 30
    max_length_cm = models.DecimalField(max_digits=6, decimal_places=1, null=True)  # 60
    latitude  = models.DecimalField(max_digits=9, decimal_places=6, null=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True)
    status    = models.CharField(max_length=20, blank=True)   # фільтруємо "Working"
    is_active = models.BooleanField(default=True, db_index=True)
    last_seen_run = models.UUIDField(null=True, blank=True, db_index=True)   # ⚡ CRITICAL

    class Meta:
        indexes = [
            models.Index(fields=["city_ref", "category", "is_active"]),
            GinIndex(fields=["description"], opclasses=["gin_trgm_ops"], name="np_wh_trgm"),
        ]
```
> ⚡ **`last_seen_run` + guard на довідниках НП** (був major): відділення НП закриваються і переносяться
> регулярно. Ref зниклого відділення просто не приходить, а рядок лишався `is_active=True` **назавжди**
> → покупець обирає неіснуюче відділення → `CheckConstraint` проходить, замовлення створюється,
> **а ТТН не вибивається**. Після повного успішного завантаження:
> `qs.exclude(last_seen_run=run.id).update(is_active=False)` — **ніколи не DELETE** (на них посилаються старі замовлення).

```python
class DeliveryQuote(models.Model):
    """⚡ Тепер ТІЛЬКИ аналітика. Гарячий кеш — Redis (TTL 24 год)."""
    cache_key    = models.CharField(max_length=140, db_index=True)   # ⚡ unique ЗНЯТО
    # ⚡ ключ ВКЛЮЧАЄ cost_declared і тиждень тарифів: f"{city}:{svc}:{w}:{v}:{cost}:{YYYYWW}"
    city_ref     = models.CharField(max_length=36)
    service_type = models.CharField(max_length=24)
    weight_kg    = models.DecimalField(max_digits=7, decimal_places=3)
    volume_m3    = models.DecimalField(max_digits=10, decimal_places=5)
    cost_declared = models.DecimalField(max_digits=12, decimal_places=2)
    cost_delivery = models.DecimalField(max_digits=10, decimal_places=2)
    cost_redelivery = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    raw = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
```
> ⚡ Пишемо **тільки на cache-miss і з семплюванням 10%** (`/delivery/quote` — публічний ендпоінт,
> «публічний запит → INSERT у БД» = вектор забивання диска). `purge_delivery_quotes` — 30 днів.

---

## 7. `cms` + `siteconfig`

```python
class Banner(TimeStampedModel):
    class Placement(models.TextChoices):
        HOME_SLIDER = "home_slider", "Слайдер головної"
        HOME_PROMO  = "home_promo",  "Промо-блок головної"
        CATEGORY_TOP = "category_top", "Банер над категорією"
    placement    = models.CharField(max_length=16, choices=Placement.choices, db_index=True)
    title        = models.CharField(max_length=200, blank=True)   # [tr]
    subtitle     = models.CharField(max_length=300, blank=True)   # [tr]
    image        = models.ImageField(upload_to="banners/")        # [tr]
    image_mobile = models.ImageField(upload_to="banners/", blank=True)   # [tr]
    link_url     = models.CharField(max_length=500, blank=True)
    category     = models.ForeignKey("catalog.Category", null=True, blank=True,
                                     on_delete=models.CASCADE)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active  = models.BooleanField(default=True, db_index=True)
    starts_at  = models.DateTimeField(null=True, blank=True)
    ends_at    = models.DateTimeField(null=True, blank=True)


class NewsPost(SEOMixin, TimeStampedModel):
    title   = models.CharField(max_length=250)   # [tr]
    slug    = models.SlugField(max_length=280)   # [tr]
    excerpt = models.TextField(blank=True)       # [tr]
    body    = models.TextField()                 # [tr]  HTML (TipTap в unfold), bleach
    cover   = models.ImageField(upload_to="news/", blank=True)
    is_published = models.BooleanField(default=False, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["slug_uk"], name="uniq_news_slug_uk")]


class StaticPage(SEOMixin, TimeStampedModel):
    key   = models.SlugField(max_length=60, unique=True)
    # payment-delivery | warranty | return | about | contacts | buyers | credit
    title = models.CharField(max_length=250)     # [tr]
    body  = models.TextField()                   # [tr]
    is_published   = models.BooleanField(default=True)
    show_in_footer = models.BooleanField(default=True)
    show_in_menu   = models.BooleanField(default=False)
    sort_order     = models.PositiveSmallIntegerField(default=0)


class MenuItem(TimeStampedModel):
    class Zone(models.TextChoices):
        HEADER = "header", "Верхнє меню"; FOOTER = "footer", "Підвал"; MOBILE = "mobile", "Бургер"
    zone   = models.CharField(max_length=8, choices=Zone.choices, db_index=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE,
                               related_name="children")
    title  = models.CharField(max_length=120)   # [tr]
    url    = models.CharField(max_length=300)
    static_page = models.ForeignKey(StaticPage, null=True, blank=True, on_delete=models.CASCADE)
    icon = models.CharField(max_length=40, blank=True)   # lucide-ім'я
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active  = models.BooleanField(default=True)


class PickupPoint(TimeStampedModel):
    name    = models.CharField(max_length=160)   # [tr]
    address = models.CharField(max_length=250)   # [tr]
    city    = models.CharField(max_length=100, default="Ужгород")
    phone   = models.CharField(max_length=20, blank=True)
    working_hours = models.CharField(max_length=200, blank=True)   # [tr]
    latitude  = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    is_active = models.BooleanField(default=True)
```

```python
class SiteSettings(SingletonModel):
    # --- ціноутворення ---
    usd_rate = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("41.5000"))
    usd_rate_updated_at = models.DateTimeField(null=True, blank=True)
    usd_rate_auto_nbu   = models.BooleanField(default=False)   # ⚡ тягнути з НБУ (див. OPEN_QUESTIONS)
    class Rounding(models.TextChoices):
        NONE = "none", "Без округлення (до копійки)"
        UNIT = "unit", "До гривні"
        TEN  = "ten",  "До 10 грн"
        NINE = "nine", "До 9 (…99, …999)"
    price_rounding = models.CharField(max_length=6, choices=Rounding.choices, default=Rounding.UNIT)

    # --- габарити: глобальний фолбек (останній рівень effective_dims) ---
    default_weight_kg = models.DecimalField(max_digits=7, decimal_places=3, default=Decimal("5"))  # ⚡
    default_width_cm  = models.DecimalField(max_digits=6, decimal_places=1, default=Decimal("40"))
    default_height_cm = models.DecimalField(max_digits=6, decimal_places=1, default=Decimal("40"))
    default_depth_cm  = models.DecimalField(max_digits=6, decimal_places=1, default=Decimal("40"))

    # --- синхронізація ---
    sync_enabled = models.BooleanField(default=True)   # kill-switch
    sync_alert_emails = ArrayField(models.EmailField(), default=list, blank=True)
    telegram_chat_id  = models.CharField(max_length=40, blank=True)   # ⚡ основний канал алертів

    # --- контакти ---
    phones = ArrayField(models.CharField(max_length=20), default=list, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=250, blank=True)   # [tr]
    map_embed_url = models.URLField(blank=True)
    social_links = models.JSONField(default=dict, blank=True)

    # --- доставка ---
    np_sender_city_ref = models.CharField(max_length=36,
                                          default="e221d627-391c-11dd-90d9-001a92567626")  # Ужгород
    free_shipping_from = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    courier_uzhhorod_price_note = models.CharField(max_length=120, blank=True)   # [tr]

    # --- оплата ---
    payment_provider = models.CharField(max_length=10, default="mono")   # mono | liqpay
    installment_badge_text = models.CharField(max_length=120, blank=True)   # [tr]
    installment_provider   = models.CharField(max_length=40, blank=True)
    # "Monobank, Privatbank" → у фід Hotline: <payment type="installment" from="...">
    installment_max_period = models.PositiveSmallIntegerField(default=24)   # ⚡ для фіда
    installment_min_payment = models.DecimalField(max_digits=10, decimal_places=2, default=300)  # ⚡

    # --- Hotline ---
    hotline_enabled   = models.BooleanField(default=False)
    hotline_firm_id   = models.CharField(max_length=20, blank=True)
    hotline_firm_name = models.CharField(max_length=120, blank=True)
    hotline_feed_generated_at = models.DateTimeField(null=True, blank=True)

    # --- ШІ ---
    ai_monthly_budget_usd = models.DecimalField(max_digits=8, decimal_places=2, default=50)
    ai_spent_this_month_usd = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    # --- SEO ---
    default_seo_title = models.CharField(max_length=200, blank=True)        # [tr]
    default_seo_description = models.CharField(max_length=400, blank=True)  # [tr]
    default_og_image = models.ImageField(upload_to="seo/", blank=True)

    def save(self, *args, **kwargs):
        # ⚡ DIFF-механізм: recalc запускається ТІЛЬКИ якщо реально змінилось
        old = type(self).objects.filter(pk=self.pk).first()
        super().save(*args, **kwargs)
        if old and (old.usd_rate != self.usd_rate or old.price_rounding != self.price_rounding):
            scope = "all" if old.price_rounding != self.price_rounding else "usd"
            transaction.on_commit(lambda: recalc_prices.delay(
                scope=scope, old_rate=old.usd_rate, new_rate=self.usd_rate, user_id=_current_user_id()))


class WorkingHours(models.Model):
    weekday    = models.PositiveSmallIntegerField(unique=True)   # 0=Пн … 6=Нд
    is_day_off = models.BooleanField(default=False)
    open_time  = models.TimeField(null=True, blank=True)
    close_time = models.TimeField(null=True, blank=True)
```
> ⚡ **Зміна `price_rounding` перераховує ВСІ товари** (`scope="all"`), не тільки USD.
> У v1 `recalc` фільтрував `source_currency='USD'` → після зміни правила округлення каталог
> був наполовину в старому, наполовину в новому округленні до наступного синку.

---

## 8. `feeds` + `aispecs`

```python
class FeedArtifact(TimeStampedModel):
    class Kind(models.TextChoices):
        HOTLINE_XML = "hotline_xml", "Hotline XML"
    kind = models.CharField(max_length=16, choices=Kind.choices, db_index=True)
    file = models.FileField(upload_to="feeds/")     # named volume + копія в R2
    items_count     = models.IntegerField(default=0)
    skipped_count   = models.IntegerField(default=0)
    skipped_reasons = models.JSONField(default=dict, blank=True)
    # {"no_hotline_category": 12, "no_image": 3, "out_of_stock": 41}   ← ⚡ БЕЗ "no_mpn"
    no_mpn_count = models.IntegerField(default=0)   # ⚡ лічильник, а НЕ причина скіпу
    size_bytes   = models.BigIntegerField(default=0)
    generated_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_current   = models.BooleanField(default=False, db_index=True)


class SpecHarvestJob(TimeStampedModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "У черзі";   RUNNING = "running", "Виконується"
        NEEDS_REVIEW = "needs_review", "Потребує перевірки"
        APPLIED = "applied", "Застосовано";  REJECTED = "rejected", "Відхилено"
        FAILED = "failed", "Помилка"

    product = models.ForeignKey("catalog.Product", null=True, on_delete=models.CASCADE,
                                related_name="harvest_jobs")
    sku        = models.CharField(max_length=64, db_index=True)
    query_name = models.CharField(max_length=255)
    status     = models.CharField(max_length=14, choices=Status.choices,
                                  default=Status.QUEUED, db_index=True)
    model_id       = models.CharField(max_length=60, blank=True)
    prompt_version = models.CharField(max_length=20, blank=True)
    proposed_specs = models.JSONField(default=list, blank=True)
    # [{"name":"Діагональ","unit":"дюйм","value":"55","confidence":0.95,"source":"https://..."}]
    proposed_mpn   = models.CharField(max_length=64, blank=True)   # ⚡ ШІ цілком здатен витягти MPN
    proposed_dims  = models.JSONField(default=dict, blank=True)    # ⚡ габарити для НП
    sources      = models.JSONField(default=list, blank=True)
    raw_response = models.JSONField(default=dict, blank=True)
    tokens_in  = models.IntegerField(default=0)
    tokens_out = models.IntegerField(default=0)
    cost_usd   = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    error      = models.TextField(blank=True)
    created_by  = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
```
**ШІ НІКОЛИ не пише в каталог автоматично.** `NEEDS_REVIEW` → адмін бачить diff «пропоновано / поточне»
→ кнопка «Застосувати» → створюються `ProductAttributeValue(source=ai)` + `denorm_dirty=True`.

---

## 9. Зведення критичних індексів

| Таблиця | Індекс | Навіщо |
|---|---|---|
| `catalog_product` | `(is_active, category_id, price)` | лістинг + сорт по ціні |
| `catalog_product` | `(is_active, category_id, availability)` | лістинг + фільтр наявності |
| `catalog_product` | **`GIN(filter_tokens)`** | **усі** exact- і bucket-фільтри + лічильники фасетів |
| `catalog_product` | `GIN(search_vector_uk)`, `GIN(search_vector_ru)` | FTS по кожній мові |
| `catalog_product` | `GIN(name_uk gin_trgm_ops)`, `GIN(name_ru gin_trgm_ops)` | опечатки, «схожі назви» |
| `catalog_product` | `(hotline_enabled, is_active)` | генерація фіда |
| `catalog_product` | `(source_currency, price_locked)` | recalc одним UPDATE |
| `catalog_product` | `(price_source, availability)` ⚡ | деактивація (був seq scan) |
| `catalog_product` | `partial (denorm_dirty) WHERE denorm_dirty` ⚡ | heal_denorm |
| `catalog_product` | expression `((specs_num->>'X')::numeric) WHERE specs_num ? 'X'` ⚡ | лише для `range_slider`, створює `sync_range_indexes` |
| `sync_supplieroffer` | `unique (price_source, sku)`, `(price_source, last_seen_run)`, `(sku, is_active)` ⚡ | upsert, деактивація в межах джерела, проєкція |
| `catalog_pricehistory` | `(product, -changed_at)` ⚡ | «історія ціни товару» |
| `catalog_productattributevalue` | `(product, attribute)`, `(attribute, value_number, product)` | картка; звіти/бакети |
| `delivery_npsettlement` | `GIN(present gin_trgm_ops)` | автокомпліт міста <5 мс з власної БД |
| `payments_payment` | `(status, created_at)` ⚡ | `reconcile_pending_payments` |
| `sync_synclogentry` | `(run_id, level)` | «показати тільки помилки прогону» |

**Свідомо ВИКИНУТІ індекси v1:**
* `GIN(specs_num jsonb_path_ops)` — **не прискорює нерівності** (ADR-007). Мертвий вантаж на найтовщій таблиці.
* `pav_facet_idx (attribute, option, product)` — фасети більше не рахуються з EAV (ADR-008).
* `db_index=True` на `Product.price` — покривається композитом; окремий btree ніколи не використається.
* `db_index=True` поруч з `unique=True` (`sku`, `external_id`, `code`) — Django його ігнорує, але читача плутає.
* `GinIndex(path, gin_trgm_ops)` на Category — `path__startswith` обслуговує звичайний btree.
</content>
</invoke>
