# Complex — Синхронізація Google Sheets v2

> Це найризикованіший модуль проєкту: одна помилка = весь каталог «Немає в наявності» і нуль продажів до ранку.
> Тому тут — параноя за замовчуванням. ⚡ = змінено у v2.

---

## 0. Власність полів — головне правило

| Поле | Хто власник | Поведінка синку |
|---|---|---|
| `base_price`, `base_old_price`, `source_currency`, `markup_percent` | **прайс** | оновлюється завжди, **крім `price_locked=True`** |
| `price`, `old_price`, `usd_rate_used`, `price_updated_at` | **обчислені** | рахує SQL-функція `complex_price_uah()` у проєкції |
| `availability`, `order_lead_days`, `stock_qty` | **прайс** | оновлюється завжди, **крім `availability_locked=True`** ⚡ |
| `source_category_ext_id`, `last_seen_run`, `synced_at`, `price_source`, `winning_offer` | **прайс** | службові, оновлюються **завжди** ⚡ |
| `name`, `category`, `brand`, `country`, `sku`, `hotline_enabled` | **адмінка** | пишуться **ТІЛЬКИ при створенні** товару |
| ⚡ `package_weight_kg`, `package_width_cm`, `package_height_cm`, `package_depth_cm` | **таблиця характеристик** | оновлюються завжди, **крім `package_dims_source == "manual"`** (§6.4) |
| ⚡ `warranty_months` | **таблиця характеристик** | з характеристики `Гарантійний термін` (`Attribute.role=WARRANTY_MONTHS`) |
| ⚡ `name_normalized` | **обчислене** | `normalize_model_name(name)` у `save()`/парсері (§6.2). Руками не редагується **ніколи** |
| ⚡ `name_ru`, `description_ru`, `seo_*_ru`, будь-яке `*_ru` | **черга перекладу** | синк **ніколи** не пише в `*_ru` напряму — тільки ставить `TranslationEntry` у чергу (§6.5) |
| `description`, `slug`, `seo_*`, порядок фото, `installment_available`, `mpn`, `condition` | **адмінка** | синк не чіпає **ніколи** |

Тумблер-виняток: `PriceSource.overwrite_names=True` дозволяє проєкції оновлювати `name`
(для постачальників, де назви авторитетні).

> ⚡ **Габарити переїхали з «адмінки» у власність синку** (у v2 було «синк не чіпає ніколи»).
> Причина фактична: у таблиці характеристик є `Габарити упаковки (ВхШхГ) (см)` і `Вага в упаковці (кг)`
> (INPUTS §3.4) — руками їх ніхто вводити не буде і не мусить.
>
> ⚡ **Третього прапорця `dims_locked` НЕМАЄ — і не треба.** Замок уже вбудований у поле
> `Product.package_dims_source` (DATA_MODEL §2.1): менеджер ввів габарити руками → `"manual"`,
> і `sync_specs` пише `WHERE package_dims_source <> 'manual'`. Це той самий `WHERE NOT locked`,
> тільки без четвертої булевої колонки і без питання «а що робити, якщо `dims_locked=False`,
> але значення все одно ввели руками». Джерело значення видно в адмінці — прапорець його не показує.
>
> ⚡ **Імена полів — `package_*`, не `weight_kg`/`height_cm`.** У прайсі є ДВА набори розмірів:
> габарити **товару** (`Висота (мм): 1635`, `Вага (кг): 58`) і габарити **упаковки**
> (`Габарити упаковки (ВхШхГ) (см): 171,5 x 56 x 35`, `Вага в упаковці (кг): 61.5`).
> У Нову Пошту йде **тільки упаковка**. Старе ім'я `weight_kg` було двозначним рівно там, де
> помилка коштує грошей: залив вагу товару (58) замість ваги в упаковці (61.5) — недоплатив за доставку
> на кожній посилці. Габарити самого товару денормалізації **не мають** — вони лишаються
> звичайними характеристиками в EAV.

> ⚡ **`Product.locked_fields` (ArrayField) видалено.** Воно обіцяло гарантію, якої не існувало:
> `bulk_create(update_conflicts=True, update_fields=[...])` має **статичний** список колонок
> і per-row винятків не підтримує в принципі. Замість нього — два явні прапорці
> (`price_locked`, `availability_locked`), які в новій архітектурі реалізуються тривіально:
> це просто `WHERE NOT locked` у трьох окремих UPDATE-ах проєкції.

---

## 1. Архітектура прогону: `SupplierOffer` → `project_offers()` → `Product`

```
                                ┌──────────────────────────────────┐
  Прайс Complex (priority=100) ───▶│ SupplierOffer(price_source=complex) │──┐
                                └──────────────────────────────────┘  │
                                ┌──────────────────────────────────┐  ├──▶ project_offers()
  Прайс Alpha (priority=200) ──▶│ SupplierOffer(price_source=alpha)│──┘         │
                                └──────────────────────────────────┘            │
                                                                                ▼
   Кожен прайс: upsert і деактивація ТІЛЬКИ у своїй               DISTINCT ON (sku)
   пісочниці. Перетинів немає за побудовою.                       ORDER BY priority,
                                                                  is_primary DESC, id
                                                                        │
                                                                        ▼
                                                                  UPDATE Product
```

**Чому саме так (це був critical #1).** У v1 синк робив `ON CONFLICT (sku) DO UPDATE` прямо в `Product`.
Наслідки, усі три тихі:
1. Прайс стороннього постачальника (націнка 15%, «під замовлення 14 днів») **перезаписував** ціну і наявність
   складського товару Complex. `PriceSource.priority` не перевірявся ніде — вигравав той, хто синкнувся останнім.
2. `Product.price_source` — **одинична колонка**. Після перезапису прогін Complex робив
   `filter(price_source=complex)` і цього товару **вже не бачив** → товар ніколи не деактивувався.
3. Дзеркально: прогін постачальника **деактивував** товари Complex (вони не мали його `last_seen_run`).

---

## 1.5. ⚡⚡ РЕАЛЬНА структура таблиць (звірено 14.07.2026, OAuth-доступ)

> **🔴 УВАГА, ЦЕЙ РОЗДІЛ ПЕРЕПИСАНО.** Усе, що було тут раніше, — реконструкція **зі скріншотів**.
> 14.07.2026 запрацював OAuth-доступ до РЕАЛЬНИХ таблиць замовника, і виявилось, що
> реконструкція не збігається з дійсністю **майже нічим**. Нижче — те, що прочитано з
> живих таблиць. Історична (хибна) версія — в кінці розділу, щоб її не «полагодили» назад.

**Два окремі файли:**

| Що | ID | Листи |
|---|---|---|
| Прайс «Прайс (Сайт)» | `12xmgixwelF621CPRhd9gqxNRrIW1RF-vLfHsAA4Dohc` | **UAH** (287), **USD** (555), ~~Лист4~~ (порожній), ~~USD (копия)~~, ~~USD (копия) 1~~ |
| Характеристики «База даних (Актуальна)» | `1qgMFRcTcH2Jt1q1kICmOYVJQiXSPzZpCVdP8UogqjDY` | **Основна** (1580×150), Гривнева (300×223), **Категорії** (70), Приходи (1796) |

> ⚠️ **Листи-копії (`USD (копия)`, `USD (копия) 1`) ІГНОРУЮТЬСЯ.** Робочі — лише `UAH` і `USD`.
> Механізм ігнорування — не код, а конфіг: синк читає ТІЛЬКИ ті листи, для яких є `PriceSheet`.

### 1.5.1. Прайс: 9 колонок, але заголовки на листах РІЗНІ ⚡

```
UAH: Найменування | Ціна | К-сть | Валюта | Категорія | Бренд    | Країна виробництва | Фото | Артикул
USD: Найменування | Ціна | К-сть | Валюта | Категорія | Виробник | Країна_виробник    | Фото | Артикул
                                                        ^^^^^^^^   ^^^^^^^^^^^^^^^^
```

**Наслідок: мапінг колонок — ТІЛЬКИ ПО ЗАГОЛОВКУ, з набором синонімів** (`sheets.COLUMN_SYNONYMS`).
Позиційний мапінг тут поклав би бренд у країну. Формати оголошення колонки в `column_map`:

| Значення | Що означає |
|---|---|
| `"Артикул"` | заголовок |
| `["Бренд", "Виробник"]` | синоніми, перший знайдений виграє |
| `"#8"` | **позиційно**, 8-ма колонка (1-based) — для колонок із заголовком-сміттям |

> ⚠️ Для **обов'язкових** колонок (`sku`, `name`, `price`) список синонімів навмисно куций.
> «Код», «Код товару» тощо там **заборонені**: лист із колонкою «Код» (штрихкод, код постачальника)
> мовчки під'їхав би в `sku`, і синк зіставляв би каталог не за тим ключем. Краще гучний
> `HeaderMismatch`, ніж тихо не той артикул.

### 1.5.2. Геометрія листа — 1/2/4, а НЕ 3/4/5 ⚡

```
рядок 1    ЗАГОЛОВКИ                                     → header_row     = 1
рядок 2    A2 = дата зрізу, ЧИСЛОМ (Excel serial 46211)  → date_cell      = "A2"
рядок 3    перша секція («Аксесуари до техніки»)         → рядок-секція
рядок 4    перший рядок даних                            → data_start_row = 4
```

Рядків-контактів («0950915222 Артур…», «Гарантія від виробника») у реальній таблиці **немає взагалі**.

> ⚠️ **Дата зрізу — ЧИСЛО, а не текст.** `valueRenderOption=UNFORMATTED_VALUE` віддає `46211`,
> а не «13.07.2026». Без гілки Excel-serial у `parse_sheet_date()` дата не парситься ніколи —
> і WARN `STALE_PRICE_SHEET` («синкаємо ціни місячної давності») мовчить теж. Тихо.

> 🔴 **КУРСУ USD У ПРАЙСІ НЕМАЄ ЗОВСІМ.** Він жив в **іншому** файлі («для клієнтів»).
> Комірка `E4`, з якої курс читали раніше, у реальній таблиці містить **категорію товару**.
> Курс береться **виключно з адмінки** (`SiteSettings.usd_rate`), як і вимагає ТЗ.
> `rate_cell` лишається порожнім; поле не видалене — якщо колонка з курсом колись з'явиться,
> її вмикають в адмінці, без деплою.

### 1.5.3. Таблиця характеристик — лист «Основна» (не «Гривнева») ⚡

| A | B | C | D | E | F | G | **H** | I | **J, K, L** | … |
|---|---|---|---|---|---|---|---|---|---|---|
| Найменування | Країна виробництва | Розміри в упакуванні (см) | Зображення | Тест | Характеристики | **Артикул** | **`2401579`** | Категорія | Назва / Одиниця / Значення | … |

**Беремо «Основну» (1580 рядків), а не «Гривневу» (300)** — вона повніша; «Гривнева» лишилась
від старого скрипта замовника. Структура в них однакова.

> 🔴 **Колонка H — це БРЕНД, але її заголовок — СМІТТЯ** (`2401579` — чийсь артикул, що заїхав
> у шапку). Шукати її за іменем неможливо, тому вона оголошена **позиційно**: `"brand": "#8"`.
> Без цього 1578 товарів лишилися б без бренда.

> ⚠️ Заголовок C — «Розміри в упако**ВАННІ**  (см)», а не «в упако**ВЦІ**». Стара карта колонок
> не збігалась ні на символ → габарити не читались зовсім.

**Трійки починаються з колонки J (10-та)** — це підтвердилось. `spec_triplet_start_col = 10`,
позиційно; `"spec_triplet_start": "Назва_Характеристики"` — лише **звірка** заголовка (не збігся → `FAILED`).

### 1.5.4. 🔴 РОЗМІРИ: роздільник «*», одиниці — САНТИМЕТРИ

Усі 291 заповнені габарити в «Основній» використовують **зірочку**: `600*650*1850`.
Підтверджено письмово:

> — «В базі бачиш розміри через `*` поділені. Це нормально?»
> — «Нормально. Лиш скажи в см чи в мм» → **«См»**

`parse_package_dims()` приймає `*`, `x`, кириличну `х`, `×`. Одиниці **не конвертуються**
(колонка оголошує см; вгадувати «це, мабуть, міліметри» = ділити чужу посилку на 10).
Окремої колонки з **вагою** поки **немає** — замовник обіцяв додати.

### 1.5.5. Лист «Категорії» — готовий довідник дерева ⚡

```
id         | level_1                    | level_2                     | level_3
c87728634  | Аудіо, відео техніка       |                             |
c10891916  | Аудіо, відео техніка       | Телевізори                  |
c12462857  | Вбудована побутова техніка | Вбудовані варильні поверхні | Газові варильні поверхні
```

70 рядків, 3 рівні. **Саме ці `id` стоять у колонці «Категорія» прайсу** (`c50549829`).
Імпорт — `manage.py sync_categories` (ідемпотентний, §6.6). `external_id` = id **без префікса `c`**.

**Це знімає проблему «__unmapped__»:** усі 37 категорій, що реально вживаються в прайсі,
знаходяться в довіднику (звірено). Без імпорту кожен такий товар осідав у службовій категорії
`__unmapped__` і **не потрапляв на сайт узагалі**.

---

<details>
<summary>❌ ЗАСТАРІЛО: реконструкція зі скріншотів (до 14.07.2026) — НЕ ВІДНОВЛЮВАТИ</summary>

Було описано так (і це виявилось **невірним**):

* «Два різні формати листів: робочий 9-колонковий і клієнтський 8-колонковий» —
  насправді **обидва листи 9-колонкові**, різняться лише заголовки `Бренд`/`Виробник`.
* «Рядки 1–2 — контакти, рядок 3 — заголовки, рядок 4 — метадані, дані з рядка 5» —
  насправді **заголовки в рядку 1, дані з рядка 4**.
* «`E4` = курс USD (41,65)» — **курсу в прайсі немає**; у `E4` лежить категорія.
* «`A4` = дата зрізу («14 08 25»)» — дата в **`A2`** і **числом** (Excel serial).
* «Характеристики — лист “Гривнева”, бренд у колонці H під заголовком “Виробник”» —
  лист **«Основна»**, заголовок колонки H — **сміття** (`2401579`).

</details>

---

## 2. Покроковий алгоритм `run_price_sync(price_source, trigger, dry_run=False, force=False)`

### Крок 0 — Advisory-lock ⚡

```python
with connection.cursor() as cur:
    cur.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))", [f"sync:prices:{source.code}"])
    if not cur.fetchone()[0]:
        SyncRun.objects.create(kind=SHEETS_PRICES, price_source=source, status=SKIPPED)
        return
```
**Чому не Redis-lock.** Advisory-lock звільняється **автоматично при обриві з'єднання** (OOM-kill воркера
не лишає завислого локу), не має проблеми fencing-токена (наївний `DEL` міг зняти **чужий** лок
і дозволити два одночасні синки), і не залежить від Redis (`FLUSHALL` кешу зносив і локи).
Той самий лок бере `recalc_prices` і крок `migrate` у деплої.

### Крок 1 — `SyncRun(status=RUNNING)` + контекст для тригера ⚡

```python
run = SyncRun.objects.create(kind=SHEETS_PRICES, price_source=source, trigger=trigger,
                             dry_run=dry_run, forced=force, started_by=user)
with connection.cursor() as cur:
    cur.execute("SET LOCAL complex.price_reason = 'sync'")
    cur.execute("SET LOCAL complex.run_id = %s", [str(run.id)])
# ↑ це читає PostgreSQL-тригер price_history_trg — без цього PriceHistory отримає reason='manual'
```
`run.id` (UUID) — ключ ідемпотентності всього прогону.

### Крок 2 — Читання + ЖОРСТКА звірка повноти ⚡

```python
expected = client.row_count(tab)               # spreadsheets.get(fields="…gridProperties.rowCount")
headers  = client.read_row(tab, sheet.header_row)          # ⚡ рядок 3, а не 1
meta     = client.read_cells(tab, [sheet.rate_cell, sheet.date_cell])   # ⚡ E4, A4
rows     = client.read_range(tab, first_row=sheet.data_start_row)       # ⚡ з рядка 5
                                               # values.batchGet чанками по 5000, UNFORMATTED_VALUE

offset = sheet.data_start_row - 1              # ⚡ 4 «технічні» рядки над даними, а не 1
if len(rows) + offset != expected:
    run.status = FAILED
    run.traceback = f"Неповне читання: {len(rows)+offset} з {expected} рядків"
    raise SyncAborted                          # ⚠️ КАТАЛОГ НЕ ЧІПАЄМО. Не PARTIAL, не guard — FAILED.
```
⚡ **`offset` — не косметика.** З жорстко зашитою `+1` звірка повноти на реальному листі
**не сходиться ніколи** (`len(rows)+1 != rowCount` на 3 рядки) → **кожен** прогін падає у `FAILED`.
Або, що гірше, хтось «полагодить» це, послабивши звірку — і ми втратимо захист із §9.

⚡ **Порожній хвіст.** `rowCount` — це **місткість сітки**, і Sheets радо тримає тисячі порожніх
рядків після даних. Тому `read_range` віддає рядки **до кінця сітки**, а не до останнього
заповненого: порожні рядки хвоста **входять** у `len(rows)` (інакше звірка не сходиться) і
відсіюються **вже в парсері** як «порожній рядок цілком» (крок 4), без помилки й без WARN.
**Чому (був major).** У v1 чанки читалися по 5000 з ретраями. Якщо чанк 3 упав після вичерпання ретраїв,
код міг вважати прочитане «повним прайсом» з 10 000 рядків замість 15 000. **Guard 0.5 це пропускає**
(10000 > 15000×0.5), і 5000 товарів мовчки стають «Немає в наявності».
Тепер: **будь-яка помилка будь-якого чанка після ретраїв = падіння всього кроку 2.** Ніякого «читаємо, що встигли».
Той самий інваріант — у `SpecSheet`.

### Крок 3 — Валідація заголовків

Заголовки беруться **з рядка `sheet.header_row` (=3)** і звіряються з `PriceSheet.column_map`.
Відсутня обов'язкова колонка → **`FAILED`, голосно**. Ніколи не «тихо занулити ціни, бо колонку перейменували».
Обов'язкові ключі: `sku`, `name`, `price`.
Опційні: `qty`, `mpn`, `old_price`, `currency`, `category`, `brand`, `country`, `photo` — ⚡ їх відсутність
не є помилкою (клієнтський лист не має `qty`/`category`/`brand`, §1.5.1).

⚡ **Заголовки нормалізуються перед звіркою** (`_norm_header`): NBSP і вузькі пробіли → пробіл,
`strip()`, схлопування подвійних пробілів, casefold. Інакше `«Країна_виробник»` vs `«Країна-виробник»`
(різні листи пишуть по-різному — видно на скрінах) валить прогін на рівному місці.
Дефіс/підкреслення/пробіл у заголовку вважаються **одним і тим самим символом**.

### Крок 4 — Парсинг + ВАЛІДАЦІЯ РЯДКА ⚡

```python
@dataclass(frozen=True, slots=True)
class PriceRow:
    sku: str; name: str
    name_normalized: str            # ⚡ normalize_model_name(name) — резервний ключ до SpecSheet (§6.2)
    price: Decimal
    old_price: Decimal | None
    qty: int | None                 # ⚡ None ≠ 0 (див. нижче)
    currency: str                   # колонка → fallback PriceSheet.currency
    category_ext_id: str
    brand_raw: str; country_raw: str; mpn: str
    photo_urls: tuple[str, ...]
    sheet: str; row_number: int
    raw: dict
```

**Нормалізація:**
* `_norm_category_id()` — зрізає префікс `c` **і кириличну `с`** (візуальний двійник!), пробіли, `.0` від Sheets.
* ⚡ `_norm_sku()` — `strip()`, викид усіх видів пробілів, **зріз `.0`** (Sheets віддає `2400042`
  як **число** при `UNFORMATTED_VALUE` → `float` → `"2400042.0"`), upper. Без цього артикули з прайсу
  й зі SpecSheet не зіставляться **жодного разу**, і вся §6 працюватиме на фолбеку по назві.
* ⚡ `_to_decimal_uk()` — українська локаль (див. нижче).
* ⚡ `_is_placeholder()` — «Уточнюється» та рідня (див. пастку 2).
* `_split_photos()` — по `,` / `;` / `\n`, тримає лише `https://`; ⚡ проганяє кожен елемент через
  `_is_placeholder()` (інакше в чергу завантаження летить URL `«Уточнюється»`).

#### ⚡ 4.1. Числа: `_to_decimal_uk()` — українська локаль

У прайсі реально є `145,00`, `41,65`, **`6 600,00`** (пробіл-роздільник тисяч) і **`61.5`**
(крапка — у таблиці характеристик). Один парсер має з'їсти все.

```python
SPACES = "    ​ "        # NBSP, narrow NBSP, thin, figure, ZWSP, звичайний
JUNK   = ("₴", "$", "грн", "uah", "usd")

def _to_decimal_uk(raw) -> Decimal | None:
    if isinstance(raw, (int, float, Decimal)):    # Sheets уже віддав число — НЕ чіпаємо
        return Decimal(str(raw))
    s = str(raw or "").strip().lower()
    for j in JUNK:  s = s.replace(j, "")
    for sp in SPACES: s = s.replace(sp, "")       # ⚡ пробіл-тисячі зникає ТУТ
    if not s: return None

    has_c, has_d = "," in s, "." in s
    if has_c and has_d:            # "1.234,56" або "1,234.56" → десятковий той, що ПРАВІШЕ
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("," if dec == "." else ".", "").replace(dec, ".")
    elif has_c:                    # ⚡ локаль зафіксована: кома = ДЕСЯТКОВИЙ роздільник
        s = s.replace(",", ".")
    # тільки крапка ("61.5") → вона й є десятковим: тисячі в цій локалі — пробіл, не крапка
    return Decimal(s)              # ValueError/InvalidOperation ловить викликач → ROW_INVALID
```

⚠️ **Свідома пастка, яку лишаємо відкритою:** `"6,600"` (кома як тисячі, US-стиль) за цим правилом
стане `6.60`, а не `6600`. Автовизначати тут неможливо — `"41,65"` виглядає **точно так само**.
Тому: локаль **зафіксована** (кома = десяткова), а неоднозначність ловиться трьома сітками —
`SyncLogEntry(WARN, AMBIGUOUS_NUMBER)`, якщо після коми рівно **3 цифри** (`6,600`, але не `145,00`);
guard #2 по медіанній зміні ціни (крок 6); і snapshot для відкату (§5).

#### ⚡ 4.2. Пастки парсера — матриця рішень (INPUTS §3.2)

Це **скасовує** правило v2 «порожній `sku` або `name` → ERROR». Виявилось, що порожній `sku` —
це **три різні ситуації** з трьома різними реакціями, і звалювати їх в одну купу означає
або залити лог сотнями фальшивих ERROR (і зробити `PARTIAL` нормою, на яку перестануть дивитись),
або мовчки проковтнути реальну втрату товару.

| # | `name` | `sku` | `price` | Що це насправді | Реакція |
|---|---|---|---|---|---|
| 0 | – | – | – | порожній рядок / хвіст сітки | пропуск **мовчки**, `rows_skipped++` |
| 1 | + | – | – | ⚡ **рядок-секція**: `Аксесуари до техніки` | пропуск **мовчки**, `rows_section++`. **НЕ помилка, НЕ WARN.** |
| 2 | + | – | + | ⚡ **товар без артикула**: `Запчастина для духовки TEKA 83340602` | пропуск + `SyncLogEntry(WARN, ROW_NO_SKU)`, `rows_no_sku++`. Прогін **не PARTIAL** |
| 3 | – | + | ? | артикул без назви — зсув колонок | `ERROR, ROW_INVALID`, `rows_failed++` |
| 4 | + | + | – / 0 / `<0` / не число | битий рядок | `ERROR, ROW_INVALID`, `rows_failed++` |
| 5 | + | + | `> 10_000_000` | з'їхала кома (`27445` → `2744500`) | `ERROR, ROW_INVALID`, `rows_failed++` |
| 6 | + | + | + | нормальний товар | у `PriceRow` |

**Пастка 1 — рядки-секції.** У прайсі це заголовок групи (`Аксесуари до техніки`), а не товар.
Формальна ознака: **є назва, немає ні артикула, ні ціни**. Ловити за списком імен («Аксесуари…»)
**не можна** — Артур допише нову секцію, і вона поїде в каталог як товар з ціною 0.
Прапорець `rows_section` виводиться в звіті прогону, щоб раптовий стрибок (з 12 до 4000)
було видно оком.

**Пастка 2 — `Уточнюється`.** Це **не значення**, це «ще не знаю». Зустрічається в
`Артикул` / `Категорія` / `Виробник` / `Країна` / `Фото`.

```python
PLACEHOLDERS = {"уточнюється", "уточняется", "уточнюеться", "уточнити", "-", "—", "–",
                "n/a", "н/д", "нема", "немає", "?", "..."}

def _is_placeholder(v) -> bool:
    s = str(v or "").strip().casefold().rstrip(".:")
    return not s or s in PLACEHOLDERS
```
Спрацював → поле стає **порожнім рядком**, і далі працюють уже написані механізми:
порожня категорія → `__unmapped__` (крок 10), порожній бренд → просто не виставляється.

> ⚡ **ЗМІНА 14.07.2026: `Уточнюється` в колонці «АРТИКУЛ» — це теж плейсхолдер**, тобто
> «артикула ще немає» (див. §4.3). Раніше він вважався **значенням** — і всі 237 таких рядків
> реального прайсу злиплись би в один товар з артикулом `УТОЧНЮЄТЬСЯ`.
> **Ціна** через `_is_placeholder` як і раніше НЕ проходить: там `Уточнюється` = битий рядок (ERROR).

> ⚠️ **Чому це критично, а не косметика.** Без цієї перевірки крок 11 створює **бренд
> `«Уточнюється»`** і чіпляє на нього 200 товарів; крок 10 шукає категорію з `external_id="Уточнюється"`;
> черга `images` отримує «URL» `Уточнюється` і 200 разів довбиться в DNS. Причому все це —
> **тихо і «успішно»**, з зеленим `SUCCESS`.

**Пастка 3 — рядок без артикула (`ROW_NO_SKU`).** ⚡ **ПОВЕДІНКУ ЗМІНЕНО** — див. §4.3:
товар **створюється** (з сурогатним ключем) і **ховається з сайту**, а не пропускається.
Якщо `rows_no_sku` різко стрибнув → це вже не забуті клітинки, а зсунута колонка (алерт, §9).

> **Чому це критично.** На `Product` висять `CheckConstraint(price >= 0)` і
> `CheckConstraint(~on_order | lead_days IS NOT NULL)`, а запис іде `bulk_create(batch_size=500)`.
> Один рядок з ціною `-1` валить **весь INSERT на 500 товарів** з `IntegrityError`, а `transaction.atomic()`
> відкочує **весь прогін**. Обіцянка «битий рядок не валить прогін» у v1 була неправдою.
> Constraint'и лишаються, але вони — **друга** лінія оборони.

#### ⚡⚡ 4.3. ЧЕРНЕТКИ: товар без артикула / без фото (нове, 14.07.2026)

**Факт:** у реальному прайсі **237 з 836 рядків** мають у колонці «Артикул» слово `Уточнюється`.
Це не збій даних. Пояснення замовника:

> «Це бо товари **не додані в базу**, у них нема ні фото ні артикула, нічого крім ціни.
> **Роби одразу, щоб товар не відображався.** Візьму з собою на нічну сьогодні комп,
> спробую за ніч додати»

Тобто це **чернетки**, які замовник дозаповнює **прямо зараз**. Звідси три правила:

**1. Товар СТВОРЮЄТЬСЯ, але НЕ ПОКАЗУЄТЬСЯ.**
Критерій повноти — **АРТИКУЛ + ФОТО + ЦІНА**:

```
немає артикула  →  is_incomplete = True  →  is_active = False   (немає на сайті, у фіді, в пошуку)
немає фото      →  is_incomplete = True  →  is_active = False
```

> ⚠️ **Характеристики в критерій НЕ входять** — це окрема, чинна вимога замовника:
> «навіть якщо пусті характеристики, хай карта товару буде пуста з ціною».
> Товар без характеристик, але з артикулом і фото, **показується**.

Мовчки пропускати такі рядки не можна (це чверть прайсу), показувати — теж не можна.
Дані (ціна, назва, категорія) зберігаються; товар просто невидимий.

**2. Сурогатний ключ.** Товар без артикула нічим оновлювати на наступному прогоні, тому ключ
будується з **нормалізованої назви** — тієї самої, якою зшиваються прайс і характеристики:

```
sku = "AUTO-" + blake2s(name_normalized)[:12]     # AUTO-788FB01C6A21
Product.sku_is_surrogate = True
```
Детермінований: той самий рядок прайсу на кожному прогоні дає той самий `sku` (інакше кожен
синк плодив би нові товари). На 246 реальних рядках — **0 колізій**.

**3. 🔴 АВТОМАТИЧНА АКТИВАЦІЯ (найважливіше).**
Щойно в прайсі з'явились артикул **і** фото — наступний синк:

```
plan_surrogate_adoptions()   зіставляє рядок з товаром за name_normalized
adopt_surrogate_skus()       ОНОВЛЮЄ sku ТОМУ САМОМУ товару (AUTO-788F… → 2400123)
_COMPLETE_SQL                знімає is_incomplete і вмикає is_active = True
```

Замовник дозаповнює таблицю → товари **самі** з'являються на сайті. Без жодної ручної дії.

> ⚠️ **Без усиновлення сурогатний ключ — не рішення, а бомба.** Інакше:
> прогін 1 створює `AUTO-788F…`; замовник вписує артикул; прогін 2 створює **ДРУГИЙ** товар
> `2400123`, а перший осиротів і деактивувався. У каталозі два товари замість одного, історія
> цін — у мертвого. Тест на це — обов'язковий (`test_sku_appears_later_updates_the_same_product`).

> ⚠️ **Запобіжник #2a мусить знати про усиновлення.** Для нього перейменування sku виглядає як
> «старий артикул зник, новий з'явився» → масова деактивація → `ABORTED`. Замовник заповнює
> артикули **пачками, за ніч** — тобто ранковий прогін зупинявся б рівно тоді, коли все йде
> правильно. Тому план усиновлення рахується **ДО** запобіжників і передається в них
> (`build_guard_report(rows, src, adoptions)`).

> ⚠️ **Товар, вимкнений РУКАМИ в адмінці, синк назад НЕ вмикає.** Автоактивація спрацьовує
> лише на переході «неповний → повний» (`WHERE p.is_incomplete`). Інакше «сховав товар»
> означало б «сховав до наступного синку».

**⚡ Крок 3.5 — fail-fast конфігу джерела** (до читання Sheets):
```python
if source.default_availability == ON_ORDER and source.default_lead_days is None:
    run.status = FAILED
    run.traceback = "Джерело налаштоване як «Під замовлення», але не вказано default_lead_days"
    return   # людське повідомлення, а не «порушено constraint» на 20-й хвилині
```

### ~~⚡ Крок 3.6 — Курс USD з комірки E4~~ → 🔴 КУРСУ В ПРАЙСІ НЕМАЄ (14.07.2026)

> **ЦЕЙ КРОК БІЛЬШЕ НЕ ПРАЦЮЄ І ЦЕ НОРМАЛЬНО.** Звірка з реальною таблицею показала:
> **курсу USD у прайсі немає взагалі** (він жив в іншому файлі, «для клієнтів»), а в комірці
> `E4` лежить **категорія товару**. `rate_cell` тепер порожній → `sheet_rate = None` →
> перевірка `RATE_MISMATCH` просто не спрацьовує.
>
> **Курс береться ВИКЛЮЧНО з адмінки** (`SiteSettings.usd_rate`) — як і вимагало ТЗ, і як
> описано нижче. Код читання `rate_cell` лишається: якщо колонка з курсом колись з'явиться,
> її вмикають в адмінці, без деплою. Дата зрізу переїхала з `A4` (текст) у **`A2` (Excel serial)**.

Історичне обґрунтування (лишається чинним як ПРИНЦИП):

У таблиці міг бути **свій** курс, а в адмінці є `SiteSettings.usd_rate`. Це два різні числа,
і вони **розійдуться**.

```python
sheet_rate = _to_decimal_uk(meta[sheet.rate_cell])       # може бути None (курс не заповнили)
run.sheet_usd_rate      = sheet_rate                     # ⚡ нове поле SyncRun
run.sheet_snapshot_date = _parse_sheet_date(meta[sheet.date_cell])   # "14 08 25" → date(2025,8,14)

rate = SiteSettings.get_solo().usd_rate                  # ⚡ АДМІНКА — ЄДИНЕ ДЖЕРЕЛО ПРАВДИ

if sheet_rate and rate and abs(sheet_rate - rate) / rate > Decimal("0.03"):
    log(WARN, RATE_MISMATCH, message=f"Курс у таблиці {sheet_rate}, в адмінці {rate}")
    run.needs_attention = True     # → жовтий банер в адмінці + кнопка «Підтягнути курс з таблиці»
```

**Ціни рахуються за курсом з АДМІНКИ, ніколи — за курсом з таблиці.** Це свідомо:
* курс у комірці міняє та сама людина, що й ціни, — і рівно так само може помилитись на порядок
  (`4,165` замість `41,65` → **весь каталог дешевшає вдесятеро**, і жоден guard по медіані
  цього не спіймає, бо зміна рівномірна);
* зміна курсу зобов'язана проходити через `recalc_prices()` + `UsdRateChange` (аудит, хто і коли),
  а мовчазне «синк сам підтягнув курс» цей аудит обходить.

Тому таблична комірка — це **підказка**: банер «У прайсі курс 44,75, у нас 41,65 — оновити?»
з кнопкою, яка робить нормальний `recalc_prices()` від імені користувача. Це закриває
відкрите питання №3 з INPUTS §5.
⚡ **`_parse_sheet_date("14 08 25")`** — формат `дд мм рр` (2-значний рік). Не розпарсилось → `None`,
не помилка. Дата зрізу потрібна лише для звіту прогону («прайс від 14.08.25, а сьогодні 20.08»
→ WARN `STALE_PRICE_SHEET`, якщо різниця > 14 днів: значить, Артур давно не оновлював таблицю,
а ми старанно синкаємо старі ціни).

### Крок 5 — ДЕДУПЛІКАЦІЯ SKU ⚡ (був critical)

```python
by_sku: dict[str, PriceRow] = {}
for row in sorted(all_rows, key=lambda r: (sheet_sort_order[r.sheet], r.row_number)):
    if row.sku in by_sku:
        prev = by_sku[row.sku]
        log(WARN, DUPLICATE_SKU, sku=row.sku,
            message=f"Дубль артикула: рядок {prev.sheet}:{prev.row_number} vs {row.sheet}:{row.row_number}. "
                    f"Взято {prev.sheet}:{prev.row_number}")
        run.rows_duplicated += 1
        continue          # ⚡ ПЕРЕМАГАЄ ПЕРШИЙ (лист з меншим sort_order, потім менший row_number)
    by_sku[row.sku] = row
rows = list(by_sku.values())
```
**Чому.** Якщо два об'єкти з однаковим `sku` потраплять в **один** `bulk_create(update_conflicts=True)`,
PostgreSQL кине `ON CONFLICT DO UPDATE command cannot affect row a second time` і **завалить весь батч**
(а з ним і транзакцію). Це не гіпотетика: дубль артикула в таблиці, яку редагують люди, —
питання тижнів. Дедуп робиться **на рівні всього прогону**, а не батча.
> v1 казав «виграє останній» — у v2 **виграє перший**: детермінізм важливіший, і лист з меншим
> `sort_order` (UAH) свідомо пріоритетніший за USD.

### Крок 6 — ЗАПОБІЖНИКИ (два) ⚡

**Guard #1 — «мало рядків» (з v1):**
```python
alive_before = SupplierOffer.objects.filter(price_source=src, is_active=True).count()
if alive_before and len(rows) < alive_before * src.guard_min_rows_ratio:      # дефолт 0.5
    abort(run, "guard_min_rows", {"rows": len(rows), "alive": alive_before})
```

**⚡ Guard #2 — «пороги масової зміни» (був critical):**
Рахується **на прочитаних рядках проти поточного стану, ДО будь-якого запису**:
```python
report = {
  "will_deactivate_pct": ...,   # % живих офферів джерела, які зникли з прайсу АБО мають qty<=0
  "median_price_delta_pct": ..., # медіанна |зміна| ціни по SKU, що є в обох
  "qty_zeroed_pct": ...,        # % рядків з qty<=0 при попередньому qty>0
}
if (report["will_deactivate_pct"]   > src.guard_max_deactivate_pct        # дефолт 20%
 or report["median_price_delta_pct"]> src.guard_max_median_price_pct      # дефолт 30%
 or report["qty_zeroed_pct"]        > 20) and not force:
    run.status = ABORTED
    run.guard_report = report
    alert_telegram(...)   # + кнопка в адмінці «Застосувати попри попередження» (force=True)
    return                # КАТАЛОГ НЕ ЗМІНЕНО
```

**Чому Guard #1 недостатньо.** Найімовірніший катастрофічний сценарій — не баг коду, а людина:
Артур **обнуляє або зсуває колонку «К-сть»** (рядків стільки ж → guard #1 мовчить → 10k товарів
стають `OUT_OF_STOCK`), або вставляє колонку і зсуває значення, або міняє десяткову кому.
Guard #2 ловить рівно це. `force=True` доступний **тільки з адмінки і тільки з `started_by`**.

### Крок 7 — Сирий знімок у R2 ⚡

Перед записом: `gzip(CSV(rows))` → `R2://sync-snapshots/{run.id}.csv.gz` (~1 МБ на 10k рядків),
`run.raw_snapshot_key`. Форензика («що було в таблиці о 12:00») + можливість переграти прогін офлайн.

### Крок 8 — Запис (у `transaction.atomic()`)

```python
with transaction.atomic():
    # 8a. ⚡ SNAPSHOT для відкату
    ProductPriceSnapshot.objects.bulk_create([...])   # COPY поточних цін/наявності, ~10k рядків

    # 8b. UPSERT ОФФЕРІВ — ТІЛЬКИ у свою пісочницю
    for batch in chunked(offers, 500):
        try:
            with transaction.atomic(savepoint=True):        # ⚡ savepoint на кожен батч
                SupplierOffer.objects.bulk_create(
                    batch,
                    update_conflicts=True,
                    unique_fields=["price_source", "sku"],
                    update_fields=OFFER_FIELDS,             # включно з updated_at ⚡
                    batch_size=500,
                )
        except IntegrityError:
            # ⚡ fallback: порядкова вставка — один аномальний рядок не забирає з собою 499 нормальних
            for offer in batch:
                try:
                    with transaction.atomic(savepoint=True):
                        SupplierOffer.objects.update_or_create(
                            price_source=src, sku=offer.sku, defaults={...})
                except IntegrityError as e:
                    log(ERROR, ROW_INVALID, sku=offer.sku, message=str(e))
                    run.rows_failed += 1

    # 8c. ⚡ ДЕАКТИВАЦІЯ ЗНИКЛИХ ОФФЕРІВ — ТІЛЬКИ у своїй пісочниці
    SupplierOffer.objects.filter(price_source=src, is_active=True) \
                         .exclude(last_seen_run=run.id) \
                         .update(is_active=False, availability=OUT_OF_STOCK,
                                 stock_qty=0, updated_at=Now())
    # ↑ жодного перетину з іншими джерелами. Прогін Alpha більше НЕ деактивує товари Complex.

    # 8d. ПРОЄКЦІЯ → Product
    stats = project_offers(sku_list=[r.sku for r in rows], run=run)
```

**`OFFER_FIELDS`** (`update_fields` для оффера):
`name, base_price, base_old_price, source_currency, stock_qty, availability, order_lead_days,
 category_ext_id, brand_raw, country_raw, mpn, photo_urls, raw, is_active, last_seen_run,
 sheet, row_number, updated_at`

⚡ **`updated_at` — ОБОВ'ЯЗКОВО в `update_fields`.** `auto_now` виставляється Django в pre_save
при формуванні **INSERT**-частини; при конфлікті колонка не входить у `SET` → у **існуючих** рядків
вона **не змінюється ніколи**. Наслідки в v1: `sitemap.xml lastmod` бреше, ISR-ревалідація
«що змінилось» не працює, інкрементальний експорт по `updated_at__gte` мовчки пропускає всі оновлення цін.
Те саме правило — для кожного `qs.update()`: **завжди `updated_at=Now()`**.

### Крок 9 — `availability` з `qty` ⚡

```python
def resolve_availability(qty: int | None, src: PriceSource) -> tuple[str, int | None]:
    if qty is None:                      # ⚡ колонки немає АБО клітинка порожня
        return src.default_availability, src.default_lead_days
        # наявність визначає САМ ФАКТ присутності рядка в прайсі
    if qty <= 0:
        return Availability.OUT_OF_STOCK, None
    return src.default_availability, src.default_lead_days
```
**Чому (був critical).** У v1: `qty > 0 → default; qty <= 0 → OUT_OF_STOCK`. Але `stock_qty` — nullable,
а в прайсі стороннього постачальника колонки «К-сть» **може не бути взагалі** (товар «під замовлення» —
кількість не вказують). `None > 0` у Python — `TypeError`; після наївного приведення до `0` — **всі**
товари такого постачальника миттєво стають «Немає в наявності». Тобто рівно той сценарій, який ТЗ
просить закласти на майбутнє, ламався першим.

### Крок 10 — Категорії ⚡

```python
cat_by_ext = {c.external_id: c for c in Category.objects.all()}   # 59 рядків, один SELECT
unmapped   = Category.objects.get(external_id="__unmapped__")     # is_active=False, is_service=True
```
* Невідомий `external_id` → товар **створюється** в `__unmapped__` з **`is_active=False`** ⚡
  (у v1 він був `is_active=True` і міг просочитись у FTS, sitemap і на `/p/{id}`),
  `SyncLogEntry(WARN, CATEGORY_MISSING)`, ID додається в `run.categories_missing` → **червоний банер
  в адмінці**: «У прайсі 3 невідомі ID категорій: 50549829, … — створіть їх або виправте прайс».
* **Автоматично категорію не створюємо**: у прайсі є тільки ID, немає назви. Категорія з іменем
  «50549829» у мегаменю — гірше, ніж її відсутність.
* ⚡ **`Product.source_category_ext_id` пишеться ЗАВЖДИ** (це дані прайсу), і після проєкції йде **крок 10b**:

```python
# ⚡ КРОК 10b — автопідбір товарів з __unmapped__ (був critical)
Product.objects.filter(category=unmapped, source_category_ext_id__in=cat_by_ext.keys()) \
               .update(category=Subquery(...), is_active=True, denorm_dirty=True, updated_at=Now())
```
Ідемпотентно, один UPDATE. Плюс сигнал `post_save` на `Category` → `remap_unmapped_products.delay(external_id)`
— щойно контент-менеджер створить категорію, товари **самі** туди переїдуть.
**У v1 цього шляху назад не було зовсім:** `category` писалась тільки при створенні товару,
тому товари з `__unmapped__` лишались там **назавжди**, а сирий ID ніде на товарі не зберігався.

### Крок 11 — Бренди / країни
`resolve_brand(raw)` — пошук по `Brand.name` (case-insensitive) і по `aliases` (GIN).
Не знайдено → `Brand(name=raw, needs_review=True)` + `WARN`. Злиття дублів — **тільки** дією
«Злити з…» (`merge_brands`), пряме видалення в адмінці заборонене (див. DATA_MODEL §2.2).

### Крок 12 — `on_commit` + heal ⚡

```python
transaction.on_commit(lambda: chain(
    fill_specs_for_new.si(run.id, new_ids),                 # черга sync
    group(download_product_image.si(...) for ...),          # черга images
    rebuild_product_denorm.si(batch),                       # черга default
    refresh_category_counts.si(),
    revalidate_next.si(tags=["catalog", f"category:{cid}"]),
).apply_async())
```
**`on_commit` — це швидкий шлях, а НЕ гарантія.** Якщо процес помирає між `COMMIT` і `.delay()`,
задачі не поставлені **назавжди**. Гарантію дають heal-задачі (§9): `denorm_dirty` виставляється
**синхронно, у тій самій транзакції**, тому навіть повна втрата черги самозагоюється за 5 хв.

### Крок 13 — Фініш
`status = FAILED | ABORTED | PARTIAL (rows_failed > 0) | SUCCESS`, `finished_at`, `duration_ms`.
Пінг у **Healthchecks.io** (dead-man's switch) при `SUCCESS`/`PARTIAL`.
Алерт у **Telegram** при `FAILED` / `ABORTED` / `PARTIAL з rows_failed > 1%` / `products_deactivated > 5%` /
`categories_missing` непорожній / `images_failed > 10%`.

---

## 3. `project_offers()` — SQL проєкції ⚡

Ядро v2. Три окремі `UPDATE`, кожен зі своїм `WHERE`-замком — **саме тому per-row локи стали тривіальними**.

```sql
-- Матеріалізуємо переможців
CREATE TEMP TABLE winner ON COMMIT DROP AS
SELECT DISTINCT ON (o.sku)
       o.id AS offer_id, o.sku, o.name, o.base_price, o.base_old_price, o.source_currency,
       o.stock_qty, o.availability, o.order_lead_days, o.category_ext_id,
       o.brand_raw, o.country_raw, o.mpn, o.photo_urls,
       ps.id AS price_source_id, ps.price_markup_percent AS markup, ps.overwrite_names
FROM   sync_supplieroffer o
JOIN   sync_pricesource  ps ON ps.id = o.price_source_id
WHERE  o.is_active AND ps.is_active AND o.sku = ANY(%(skus)s)
ORDER BY o.sku, ps.priority ASC, ps.is_primary DESC, o.id ASC;   -- ← МЕНШИЙ priority ВИГРАЄ

-- 1) НОВІ ТОВАРИ (тільки INSERT; існуючі не чіпає)
INSERT INTO catalog_product (
    sku, name_uk, slug_uk, category_id, brand_id, country_id, source_category_ext_id,
    base_price, base_old_price, source_currency, markup_percent,
    price, old_price, usd_rate_used, price_updated_at,
    availability, order_lead_days, stock_qty,
    hotline_enabled,               -- ⚡ = Category.hotline_enabled_default
    is_active, source, price_source_id, winning_offer_id,
    last_seen_run, synced_at, denorm_dirty, created_at, updated_at, ...)
SELECT w.sku, w.name, slugify(w.name), c.id, b.id, ct.id, w.category_ext_id,
       w.base_price, w.base_old_price, w.source_currency, w.markup,
       complex_price_uah(w.base_price,     w.source_currency, %(rate)s, w.markup, %(rule)s),
       complex_price_uah(w.base_old_price, w.source_currency, %(rate)s, w.markup, %(rule)s),
       CASE WHEN w.source_currency = 'USD' THEN %(rate)s END, now(),
       w.availability, w.order_lead_days, w.stock_qty,
       c.hotline_enabled_default,
       (NOT c.is_service),          -- ⚡ товар у __unmapped__ створюється НЕАКТИВНИМ
       'sheet', w.price_source_id, w.offer_id,
       %(run_id)s, now(), true, now(), now(), ...
FROM winner w
JOIN catalog_category c ON c.external_id = COALESCE(NULLIF(w.category_ext_id,''), '__unmapped__')
LEFT JOIN ... brand b, country ct
ON CONFLICT (sku) DO NOTHING;      -- ⚡ оновлення робить наступний блок, не цей

-- 2) ЦІНА — тільки для НЕзаблокованих
UPDATE catalog_product p SET
    base_price      = w.base_price,
    base_old_price  = w.base_old_price,
    source_currency = w.source_currency,
    markup_percent  = w.markup,
    price      = complex_price_uah(w.base_price,     w.source_currency, %(rate)s, w.markup, %(rule)s),
    old_price  = complex_price_uah(w.base_old_price, w.source_currency, %(rate)s, w.markup, %(rule)s),
    usd_rate_used    = CASE WHEN w.source_currency = 'USD' THEN %(rate)s END,
    price_updated_at = now(),
    updated_at       = now()                                      -- ⚡
FROM winner w
WHERE p.sku = w.sku AND NOT p.price_locked;                       -- ⚡ ЄДИНЕ, що робить price_locked

-- 3) НАЯВНІСТЬ — тільки для НЕзаблокованих
UPDATE catalog_product p SET
    availability    = w.availability,
    order_lead_days = w.order_lead_days,
    stock_qty       = w.stock_qty,
    updated_at      = now()
FROM winner w
WHERE p.sku = w.sku AND NOT p.availability_locked;                -- ⚡ MAJOR FIX

-- 4) СЛУЖБОВІ — ЗАВЖДИ (включно з price_locked-товарами!)
UPDATE catalog_product p SET
    price_source_id        = w.price_source_id,
    winning_offer_id       = w.offer_id,
    source_category_ext_id = w.category_ext_id,                   -- ⚡
    last_seen_run          = %(run_id)s,                          -- ⚡ CRITICAL
    synced_at              = now(),
    denorm_dirty           = true,
    updated_at             = now(),
    name_uk = CASE WHEN w.overwrite_names THEN w.name ELSE p.name_uk END
FROM winner w
WHERE p.sku = w.sku;

-- 5) ДЕАКТИВАЦІЯ ТОВАРІВ, які не мають ЖОДНОГО активного оффера
UPDATE catalog_product p SET
    availability = 'out_of_stock', stock_qty = 0, synced_at = now(), updated_at = now()
WHERE p.source = 'sheet'
  AND p.availability IN ('in_stock','on_order')
  AND NOT p.availability_locked
  AND NOT EXISTS (SELECT 1 FROM sync_supplieroffer o JOIN sync_pricesource ps ON ps.id=o.price_source_id
                  WHERE o.sku = p.sku AND o.is_active AND ps.is_active);
```

### Що це закриває

| Баг v1 | Як закрито |
|---|---|
| `price_locked` товар **зникав з продажу** на першому ж синку (не мав `last_seen_run` → деактивувався) | Блок 4 виконується **для всіх** товарів. `price_locked` більше не виключає товар з прогону — він блокує **тільки блок 2** |
| «Під замовлення», поставлене вручну, затиралось | Блок 3 має `WHERE NOT availability_locked` |
| Низькопріоритетний прайс крав SKU | `DISTINCT ON … ORDER BY priority` |
| Взаємна деактивація каталогів | Блок 5 дивиться на **всі** оффери, а не на `price_source` товару |
| `recalc` губив націнку | `complex_price_uah()` — **та сама** функція, що й у `recalc_prices()` (ADR-005) |
| `updated_at` не оновлювався | `updated_at = now()` у **кожному** блоці |
| `old_price` міг стати нижчим за `price` | обидві рахуються **однією** функцією з однаковим курсом |
| PriceHistory не писалась при масовому UPDATE | PG-тригер `price_history_trg` спрацьовує на **кожен** блок |

---

## 4. Перерахунок цін `recalc_prices(scope, old_rate, new_rate, user_id)` ⚡

```python
@shared_task(queue="sync", acks_late=True)
def recalc_prices(scope="usd", old_rate=None, new_rate=None, old_rounding=None, user_id=None):
    # ⚡ ТОЙ САМИЙ advisory-lock, що й у sync_prices — явно, а не через конкурентність воркера
    with advisory_lock("sync:prices:*"):
        s = SiteSettings.get_solo()
        run = SyncRun.objects.create(kind=PRICE_RECALC, trigger=MANUAL, started_by_id=user_id)
        with transaction.atomic(), price_context(reason="recalc", run_id=run.id):
            qs = Product.objects.filter(price_locked=False)
            if scope == "usd":
                qs = qs.filter(source_currency="USD")
            n = qs.update(
                price=RawSQL("complex_price_uah(base_price, source_currency, %s, markup_percent, %s)",
                             [s.usd_rate, s.price_rounding]),
                old_price=RawSQL("complex_price_uah(base_old_price, source_currency, %s, markup_percent, %s)",
                                 [s.usd_rate, s.price_rounding]),
                usd_rate_used=Case(When(source_currency="USD", then=Value(s.usd_rate)), default=None),
                price_updated_at=Now(),
                updated_at=Now(),          # ⚡
                denorm_dirty=True,         # ⚡
            )
        UsdRateChange.objects.create(old_rate=old_rate, new_rate=new_rate,
                                     old_rounding=old_rounding, new_rounding=s.price_rounding,
                                     products_recalculated=n, run=run, changed_by_id=user_id)
        revalidate_next.delay(tags=["catalog"])
```

**Три фікси, кожен з яких був окремим багом:**
1. ⚡ **Націнка.** У v1 `recalc` рахував `base * rate` **без markup** → перша ж зміна курсу стирала
   маржу на всіх USD-товарах. Тепер обидва шляхи викликають `complex_price_uah()` з `markup_percent`
   (денормалізованим на товарі, бо `F()` не дотягнеться до `price_source__price_markup_percent`).
2. ⚡ **`scope="all"`.** Зміна `price_rounding` або `price_markup_percent` перераховує **всі** товари,
   не тільки USD. У v1 каталог після зміни округлення був наполовину в старому правилі до наступного синку.
3. ⚡ **Гонка з синком.** `recalc` бере **той самий advisory-lock**. Плюс `sync` читає `usd_rate`
   **всередині тієї ж транзакції**, що й проєкція (`SELECT … FOR SHARE` на `SiteSettings`).
   У v1 серіалізація трималась на недокументованому `-c 1` воркера sync — підняття конкурентності
   до 2 миттєво давало перезапис цін старим курсом.

**Обов'язковий тест-інваріант:**
```python
def test_recalc_with_same_rate_is_noop():
    """sync(price=100, USD, markup=15%) і recalc(той самий курс) дають ІДЕНТИЧНУ ціну."""
```

---

## 5. Відкат прогону ⚡

`SyncRunAdmin` → дія **«Відкотити прогін»** (`rollback_run(run_id, user)`):
```python
with transaction.atomic(), price_context(reason="rollback", run_id=new_run.id):
    # відкочуємо ЛИШЕ товари, яких після цього прогону ніхто не чіпав
    cur.execute("""
        UPDATE catalog_product p SET
            base_price = s.old_base_price, price = s.old_price, old_price = s.old_old_price,
            source_currency = s.old_source_currency, markup_percent = s.old_markup_percent,
            usd_rate_used = s.old_usd_rate_used, availability = s.old_availability,
            order_lead_days = s.old_order_lead_days, stock_qty = s.old_stock_qty,
            synced_at = s.old_synced_at, denorm_dirty = true, updated_at = now()
        FROM sync_productpricesnapshot s
        WHERE s.run_id = %(run)s AND p.id = s.product_id
          AND p.synced_at = %(run_finished)s   -- ⚡ ніхто не змінював після цього прогону
    """, ...)
```
Створюється `SyncRun(kind=ROLLBACK, rolled_back_run=run)`. PriceHistory отримує `reason='rollback'`.
Товари, змінені після прогону (менеджером або наступним синком), **не чіпаються** — і в звіті
відкату видно, скільки таких.

---

## 6. Характеристики зі SpecSheet — `fill_specs_for_new(run_id, product_ids)`

Черга `sync`, окрема задача (не блокує прайс).

1. Читає `SpecSheet` **один раз** за прогін (та сама жорстка звірка `rowCount`), будує `{sku: SpecRecord}`;
   кеш у Redis, TTL 6 год, ключ включає `spreadsheet_id + tab + rowCount`.
2. Парсить **повторювані трійки позиційно**: після фіксованих колонок ідуть
   N × (`Назва_Характеристики`, `Одиниця_виміру`, `Значення_Характеристики`). Читаємо рядок до кінця,
   беремо колонки трійками, зупиняємось на першій порожній назві.
3. Для кожної трійки:
   * `attr_code = slugify(translit(name))`;
   * шукаємо `Attribute` за `code` **або за `aliases`** (GIN) — це і є нормалізація («Общий объем» → `obiem`);
   * не знайдено → `Attribute(code, name, unit, group=<Інші>, value_type=…, **is_filterable=False,
     filter_widget=NONE, needs_review=True**)` + `SyncLogEntry(ATTR_CREATED, WARN)`.
     **`is_filterable=False` — критично:** новий атрибут одразу видно в картці, але у фільтри він
     не лізе, поки людина не увімкне. Саме цим ми не повторюємо брудні фасети старого сайту
     (`Sharp` і `SHARP` як два різні фільтри).
   * `value_type` — евристика: парситься як число + є `unit` → `NUMBER`; «Так/Ні/є/немає» → `BOOL`;
     інакше `OPTION` (знаходимо/створюємо `AttributeOption` по `slug` або `aliases`).
4. `ProductAttributeValue.bulk_create(update_conflicts=True, unique_fields=…)` — ідемпотентно.
   ⚠️ **Дедуп PAV у межах товару** перед `bulk_create` — та сама пастка «ON CONFLICT двічі».
5. Додатково зі SpecSheet: **«Розміри в упаковці»** → парсер `"600x650x1850"` / `"60×65×185 см"` →
   `width_cm/height_cm/depth_cm` (**вхід калькулятора НП**), **«Країна-виробник»**, ⚡ **`mpn`** (якщо є колонка).
6. Наприкінці — `Product.objects.filter(id__in=ids).update(denorm_dirty=True)` (синхронно!) +
   `rebuild_product_denorm.delay(ids)`.

Немає sku в таблиці характеристик → `WARN` + фільтр адмінки «Без характеристик» → масова дія
«Зібрати характеристики через ШІ» (`aispecs`).

---

## 6.6. ⚡ Довідник категорій — `manage.py sync_categories` (нове, 14.07.2026)

Лист **«Категорії»** файла характеристик — це **готове дерево категорій замовника**, яким ми
не користувались (§1.5.5). 70 рядків, 3 рівні, колонки `id | level_1 | level_2 | level_3`.

```bash
uv run python manage.py sync_categories              # реальна таблиця
uv run python manage.py sync_categories --dry-run    # показати diff і відкотити
```

* `external_id` = `id` **без префікса `c`** (`c50549829` → `50549829`) — рівно так само чистить
  значення з прайсу `norm_category_id()`, тому ключі сходяться;
* батько шукається за **префіксом імен** (`level_1` → `level_1/level_2`), а **не** за порядком
  рядків: лист можуть відсортувати як завгодно;
* **ідемпотентна**: повторний запуск → `без змін 70`;
* `slug` будується **транслітерацією** (`translit_slug`), бо `slugify()` на кирилиці дає порожній
  рядок, а `slug` — `NOT NULL`. Наявний `slug` **не перезаписується** (це URL, він уже може бути
  в індексі Google);
* категорії, яких у листі більше немає, **не видаляються** (на них FK з товарів, `PROTECT`).

**Навіщо:** без довідника кожен товар з категорією `c50549829` осідав у службовій `__unmapped__`
(`is_active=False`) і **не потрапляв на сайт**. Після імпорту знаходяться **всі 37** категорій,
що реально вживаються в прайсі. Товари переїжджають з `__unmapped__` **самі** (крок 10b).

---

## 7. Фото — черга `images`

**Три рівні дедуплікації** (щоб не качати те саме 4 рази на добу):
1. `UniqueConstraint(product, source_url_hash) WHERE source_url_hash != ''` — запис не дублюється.
2. **ETag → `If-None-Match` → 304** — тіло взагалі не качаємо.
3. `content_hash = sha256(bytes)` — байти ті самі → не ллємо в R2.

**Безпека:** SSRF-guard (тільки `https`, резолв DNS, **заборона private/loopback/link-local IP**),
`Content-Type` whitelist, ліміт 10 МБ (перевірка і по `Content-Length`, і по факту стріму),
`Image.verify()` через Pillow, `timeout=(5, 30)`, `rate_limit="20/s"`, `acks_late=True`.

**Деривативи:** 3 webp (300/700/1600) **з латинськими іменами** (`{url_hash[:16]}_md.webp`) —
кирилиця в шляху = товар без фото на Hotline.

⚡ **Прибирання застарілих фото (був major):** `sync.services.prune_stale_images(product_ids, run_id)` —
фото з `source=sheet`, у яких `last_seen_run` ≠ поточний прогін. Викликається двічі: з
`_queue_images()` (постачальник ПРИБРАВ фото — жодного завантаження не буде, прибирати нікому)
і з `download_product_image()` після успішного завантаження заміни.

Без цього: постачальник замінив URL → синк створює **новий** ProductImage, старий лишається
**назавжди** і (з меншим `position`) далі є головним. Товар показує чуже фото, R2 накопичує сміття.

Чотири запобіжники — кожен закриває свій спосіб залишити картку без фото:
1. **тільки `source=sheet`** — ручні фото (`manual`) додала людина, синк не чіпає їх НІКОЛИ;
2. **тільки товари, у яких у цьому прогоні Є фото в прайсі** — порожня комірка «Фото» ≠ «видали
   всі фото»: вона не створює жодного рядка, тому товар у прибирання не потрапляє взагалі;
3. **тільки коли заміна вже на диску** (`downloaded_at`) — мертве нове посилання (404) не має
   залишити картку без фото: краще старе фото, ніж порожнє місце;
4. **не чіпаємо, поки хоч одне фото цього прогону ще в черзі** — інакше прибирання станеться
   РАНІШЕ, ніж доїде заміна.

Далі `ensure_main_image()`: головним стає перше за `position` фото, у якого вже є файл. Наявне
головне фото не перепризначається — його міг поставити руками контент-менеджер. `position` фото
З ПРАЙСУ переписується з прайсу на кожному прогоні (постачальник міг поставити нове фото першим);
позиції **ручних** фото синк не чіпає. Кожне прибирання пишеться в журнал: `SyncLogEntry`
(INFO / `image_purged`). Файли (оригінал + 3 деривативи) видаляються **в `on_commit`**, поза
транзакцією: R2 не вміє rollback.

⚡ **Ретраї:** `attempts` + `next_retry_at`. Невідновна помилка (403, битий файл) → `failed_reason`,
без ретраїв. Відновна (5xx, таймаут) → `next_retry_at = now + 2^attempts хв`, максимум 5 спроб.

---

## 8. Карта Celery-задач

| Черга | Задача | Розклад / тригер | Параметри |
|---|---|---|---|
| `sync` (-c 1) | `sync_prices(source_id, trigger, force)` | `0 8,12,16,20 * * *` | `acks_late`, `soft_time_limit=600`, `time_limit=720`, `expires=600` |
| `sync` | `fill_specs_for_new(run_id, ids)` | on_commit | |
| `sync` | `recalc_prices(scope, …)` | `SiteSettings.save()` diff | той самий advisory-lock |
| `sync` | `rollback_run(run_id)` | дія в адмінці | |
| `images` (-c 2, 20/s) | `download_product_image(image_id)` | on_commit / heal | `autoretry_for=(RequestException,)` |
| `default` (-c 4) | `rebuild_product_denorm(ids)` | on_commit / heal | |
| `default` | `refresh_category_counts()` | on_commit + `0 3 * * *` | |
| `default` | `set_hotline_for_category(cat_id, value)` ⚡ | дія в адмінці | |
| `default` | `remap_unmapped_products(ext_id)` ⚡ | post_save Category | |
| `default` | `generate_hotline_feed()` | `0 6,14,22 * * *` | |
| `default` | `sync_np_refs()` | `0 3 * * *` | guard + last_seen_run |
| `default` | `revalidate_next(tags)` | on_commit | `autoretry`, 3 спроби |
| `ai` (-c 1) | `harvest_specs(product_id)` | масова дія | бюджетний ліміт |

### ⚡ Heal / watchdog-задачі (черга `default`)

| Задача | Період | Що робить |
|---|---|---|
| `heal_denorm` | 5 хв | `Product.filter(Q(denorm_dirty=True) \| Q(denorm_at__lt=F("updated_at")))` → rebuild батчами |
| `heal_images` | 1 год | `ProductImage.filter(downloaded_at=None, failed_reason="", next_retry_at__lte=now)` → назад у чергу |
| `heal_usd_prices` | 1 год | `Product.filter(source_currency="USD", price_locked=False).exclude(usd_rate_used=rate)` → recalc + алерт, якщо > 0 |
| `reconcile_pending_payments` | 5 хв | `Payment.status in (created, pending)`, вік 5 хв…48 год → `provider.get_status()` |
| `reap_stale_runs` | 10 хв | `SyncRun.status=RUNNING` старше `time_limit+5хв` → `FAILED`, traceback=`reaped`, алерт |
| `heal_hotline_feed` | 1 год | `FeedArtifact.is_current` старший 24 год **або файл на диску відсутній** → регенерація + алерт |
| `watchdog_last_sync` | 1 год | останній `SUCCESS/PARTIAL` старше 8 год → critical-алерт + червоний банер в адмінці |
| `purge_sync_logs` | `0 4 * * *` | 90 днів, **батчами по 10k з комітами** |
| `purge_snapshots` | `0 4 * * *` | `ProductPriceSnapshot` > 30 днів |
| `purge_delivery_quotes` | `0 4 * * *` | > 30 днів |
| `purge_webhook_events` | `0 4 * * *` | > 90 днів |
| `purge_price_history` | `0 4 * * 0` | > 2 років, батчами |
| `purge_orphan_media` | `0 5 * * 0` | файли в R2 без рядка в БД |
| `restore_drill` | `0 2 * * 1` | підняти postgres з бекапу, `manage.py check`, counts → пінг |

**Розклад — `django-celery-beat` (`DatabaseScheduler`)**: редагується в адмінці (unfold), як вимагає ТЗ.
⚡ `CELERY_TIMEZONE = TIME_ZONE = "Europe/Kyiv"`, `DJANGO_CELERY_BEAT_TZ_AWARE=True`.
Без цього `'0 8,12,16,20 * * *'` інтерпретується в UTC → синки о 6:00/10:00 замість 8:00/12:00,
ще й стрибають на переході DST, а менеджер не розуміє чому.
**Тест у CI:** `PeriodicTask.crontab.timezone == "Europe/Kyiv"`.

⚡ **Redis:** broker = `db0` з `appendonly yes` + **`maxmemory-policy noeviction`** (обов'язково —
`allkeys-lru` на broker'і **мовчки їсть задачі**); cache = `db1` з `allkeys-lru`.
Локи — в Postgres (advisory), тому втрата Redis не породжує подвійний синк.

---

## 9. Матриця обробки помилок

| Збій | Наслідок | Реакція системи |
|---|---|---|
| Sheets 429 / 5xx | нічого не записано | retry з backoff (6 спроб) → `FAILED`, каталог недоторканий |
| Зник шарінг таблиці (403) | — | `FAILED` + алерт. Каталог **не** зануляється |
| Колонку перейменували | не проходить валідація заголовків | `FAILED` **голосно** |
| ⚡ Прочитано менше рядків, ніж `rowCount` | — | `FAILED` (не PARTIAL!), каталог недоторканий |
| У таблиці різко менше рядків | guard #1 | `ABORTED`, алерт |
| ⚡ Зсунута колонка «К-сть» / з'їхала кома | guard #2 (пороги масової зміни) | `ABORTED` + кнопка «Застосувати попри» |
| ⚡ Помилковий прогін уже застосовано | — | дія «Відкотити прогін» (ProductPriceSnapshot) |
| Битий рядок (ціна «домовитись», -1) | 1 рядок | валідація **в парсері** → `ERROR`, `rows_failed++`, `PARTIAL` |
| ⚡ Дубль sku | 1 рядок | дедуп **до** запису, `WARN`, `rows_duplicated++`, прогін **не падає** |
| ⚡ Батч усе одно впав на IntegrityError | 1 батч | savepoint → fallback на порядкову вставку (499 нормальних виживають) |
| Невідомий ID категорії | товар у `__unmapped__` (**is_active=False**) | `WARN` + банер; ⚡ автопідбір після створення категорії |
| Невідомий бренд | `needs_review=True` | `WARN`, злиття — вручну |
| Фото 404 / CDN лежить | `failed_reason` / `next_retry_at` | ретраї; **ціни й наявність уже актуальні** |
| ⚡ SKU є у двох прайсах | — | виграє менший `priority`; `SyncLogEntry(LOW_PRIORITY)` для програвшого |
| ⚡ OOM-kill воркера посеред прогону | транзакція відкотилась | advisory-lock **звільнився сам**; `reap_stale_runs` закриє `SyncRun` |
| ⚡ Процес помер між COMMIT і `.delay()` | задачі не поставлені | `denorm_dirty=true` уже в БД → `heal_denorm` підбере за 5 хв |
| Дві beat-репліки | другий тік | `SKIPPED` через advisory-lock |
| ⚡ Beat взагалі не піднявся | синків немає | **Healthchecks.io** не отримав пінг → алерт у Telegram |

---

## 10. Dry-run

```
manage.py sync_prices --source=complex-main --dry-run
```
Усе виконується в `atomic()`, наприкінці `raise Rollback`. `SyncRun(dry_run=True)` зберігається
**окремою транзакцією** і містить повний diff: скільки б створилось / оновилось / деактивувалось,
`guard_report`, список невідомих категорій, топ-20 найбільших змін ціни.
**Обов'язковий крок перед першим бойовим прогоном і перед підключенням кожного нового постачальника.**

---

## 11. Обов'язкові тести

### Парсинг (`tests/sync/test_parsing.py`)
- [ ] `test_price_ukrainian_format` — `"27 445,50 ₴"` (NBSP) → `Decimal("27445.50")`
- [ ] `test_category_id_cyrillic_c` — `"с50549829"` (кирилична `с`!) → `"50549829"`
- [ ] `test_negative_price_rejected_in_parser` — рядок відкинуто **до БД**, `rows_failed=1`
- [ ] `test_price_over_limit_rejected` — з'їхала кома (`2744500`) → відкинуто
- [ ] `test_empty_sku_or_name_rejected`
- [ ] `test_photos_split_by_comma_semicolon_newline`

### Upsert / оффери (`tests/sync/test_upsert.py`)
- [ ] ⚡ `test_duplicate_sku_in_one_sheet` — 2 рядки з тим самим sku → **1 оффер, 1 WARN, прогін SUCCESS/PARTIAL, НЕ FAILED**
- [ ] ⚡ `test_duplicate_sku_between_uah_and_usd_sheets` — виграє лист з меншим `sort_order`
- [ ] ⚡ `test_batch_with_bad_row_survives` — savepoint: 499 з 500 товарів записались
- [ ] ⚡ `test_updated_at_changes_on_price_update` — інакше sitemap і ISR брешуть
- [ ] `test_idempotent_rerun` — повторний прогін тих самих даних → 0 змін по суті

### Мультипостачальники (`tests/sync/test_multisource.py`) ⚡ — усе нове
- [ ] `test_low_priority_source_does_not_steal_sku` — Alpha(200) не перезаписує ціну Complex(100)
- [ ] `test_alpha_sync_does_not_deactivate_complex_products` — прогін Alpha не чіпає каталог Complex
- [ ] `test_nisa_sync_still_sees_shared_sku` — після прогону Alpha прогін NISA бачить «свій» товар
- [ ] `test_winner_switches_when_complex_offer_disappears` — SKU зник з прайсу Complex → переможцем стає Alpha
- [ ] `test_deactivate_only_when_no_active_offers`

### Замки (`tests/sync/test_locks.py`) ⚡
- [ ] `test_price_locked_product_stays_in_stock` — **головний регресійний тест v2**:
      товар з `price_locked=True`, який є в прайсі, після синку лишається `IN_STOCK`
      (у v1 він ставав `OUT_OF_STOCK` на **першому ж** прогоні = зникав з продажу)
- [ ] `test_price_locked_price_not_changed`
- [ ] `test_price_locked_availability_IS_updated`
- [ ] `test_availability_locked_keeps_manual_on_order`
- [ ] `test_availability_locked_still_gets_last_seen_run`

### Ціна (`tests/catalog/test_pricing.py`) ⚡
- [ ] **`test_price_parity_python_vs_sql`** — fuzz по сітці `base × rate × markup × rule`:
      `compute_uah_price()` (Python) == `complex_price_uah()` (SQL) до копійки
- [ ] **`test_sync_and_recalc_give_identical_price`** — інваріант «recalc при незмінному курсі = no-op»
- [ ] `test_recalc_preserves_markup` — 15% націнки **не зникає** після зміни курсу
- [ ] `test_rounding_change_recalcs_uah_products_too` — `scope="all"`
- [ ] `test_old_price_never_below_price`
- [ ] `test_price_history_written_by_trigger_on_mass_update` — `qs.update()` теж логується

### Guards і відкат (`tests/sync/test_guards.py`) ⚡
- [ ] `test_guard_min_rows_aborts` (з v1)
- [ ] `test_guard_mass_deactivation_aborts` — 60% товарів зникло → ABORTED
- [ ] `test_guard_median_price_jump_aborts` — усі ціни ×2 → ABORTED
- [ ] `test_guard_qty_column_zeroed_aborts` — «Артур зсунув колонку»
- [ ] `test_force_true_bypasses_guard_and_records_user`
- [ ] `test_rollback_restores_prices_and_availability`
- [ ] `test_rollback_skips_products_touched_after_run`

### Читання (`tests/sync/test_reader.py`) ⚡
- [ ] `test_incomplete_read_fails_hard` — прочитано 10k з 15k → **FAILED**, каталог недоторканий
- [ ] `test_retry_on_429` (через `HttpMockSequence`)
- [ ] `test_missing_column_fails_loudly`

### Наявність (`tests/sync/test_availability.py`) ⚡
- [ ] `test_qty_none_uses_source_default` — прайс **без колонки К-сть** → `ON_ORDER`, не `OUT_OF_STOCK`
- [ ] `test_qty_zero_is_out_of_stock`
- [ ] `test_on_order_source_without_lead_days_fails_fast`

### Категорії (`tests/sync/test_categories.py`) ⚡
- [ ] `test_unknown_category_goes_to_unmapped_inactive`
- [ ] `test_products_remapped_after_category_created` — **автопідбір**
- [ ] `test_source_category_ext_id_always_written`

### Фото (`sync/tests/test_images.py`)
- [ ] `test_etag_304_skips_download`
- [x] `test_ssrf_private_ip_blocked` (`sync/tests/test_tasks.py`)
- [ ] `test_oversize_rejected`
- [x] ⚡ `test_replaced_url_deletes_old_image_and_new_one_becomes_main` — головний регресійний
- [x] ⚡ `test_manual_image_is_never_deleted_by_sync`
- [x] `test_empty_photo_cell_does_not_touch_existing_images`
- [x] `test_dead_new_url_keeps_the_old_photo`
- [x] `test_product_never_ends_up_without_main_image`
- [x] `test_purge_deletes_files_from_storage_outside_transaction`
- [x] `test_purge_is_logged_to_sync_log`
- [ ] ⚡ `test_two_manual_images_no_integrity_error` (частковий unique)

### Самозагоювання (`tests/sync/test_healing.py`) ⚡ — усе нове
- [ ] `test_heal_denorm_picks_up_dirty_products`
- [ ] `test_lost_on_commit_task_is_healed` — симулюємо втрату задачі → через `heal_denorm` товар оживає
- [ ] `test_reap_stale_run_marks_failed`
- [ ] `test_heal_usd_prices_finds_undercalculated`
</content>
</invoke>

---

## 12. ⚡ Звіт про проблеми в даних — `DataIssue` + `manage.py data_issues` (нове, 14.07.2026)

Журнал синку (`SyncLogEntry`) — інструмент **розробника**: сотні рядків, коди, `payload`.
Замовник його не відкриє **ніколи**. Але саме він — єдиний, хто може полагодити дані. Його слова:

> «І може таке бути, що я десь провтикав і два товари будуть з одним артикулом.
> **Якщо тобі це відобразиться десь, скажи плз, я виправлю**»

Тому поруч із журналом є **`DataIssue`** — список для ЛЮДИНИ: що не так, **у якому листі**,
**у якому рядку**, і **що зробити**.

| Тип | Коли | Що каже замовнику |
|---|---|---|
| `DUPLICATE_SKU` | той самий артикул у 2+ рядках | номери **обох** рядків, назви **обох** товарів, який узято, який відкинуто |
| `MISSING_SKU` | «Уточнюється» в артикулі | «товар створено, але схований; впишіть артикул — з'явиться сам» |
| `MISSING_PHOTO` | немає посилання на фото | «вставте посилання — товар з'явиться після синхронізації» |
| `UNKNOWN_CATEGORY` | ID категорії немає в довіднику | скільки товарів це зачепило + «запустіть sync_categories» |
| `SPEC_NOT_FOUND` | артикул є, рядка в «Основній» немає | «товар без габаритів → доставку НП порахує неточно» |

```bash
uv run python manage.py data_issues                          # останній прогін, згруповано
uv run python manage.py data_issues --type=duplicate_sku     # лише дублі
uv run python manage.py data_issues --csv issues.csv         # ← файл замовнику в месенджер
```

**CSV — найпрактичніше:** `utf-8-sig` (без BOM Excel показує кракозябри), колонки
`Тип проблеми | Лист | Рядок | Артикул | Назва товару | Що зробити`. Замовник відкриває його
в Google Sheets і йде правити рядок за рядком.

> ⚠️ **`DataIssue` пишеться ПОЗА транзакцією запису** — як і `SyncLogEntry`. Тому список
> **переживає dry-run**: саме заради нього dry-run і запускають («покажи, що я наламав у
> таблиці, ДО того як воно поїде на сайт»). З тієї ж причини `SPEC_NOT_FOUND` рахується
> **до** запису, по ВСІХ рядках прайсу, а не лише по новостворених товарах.

> ⚠️ **Дедуп рятує ПРОГІН, але не ЛІКУЄ ДАНІ.** Дубль артикула не валить синк (перший виграє,
> другий → `WARN`), але один із двох товарів усе одно не потрапить на сайт. Без `DataIssue`
> замовник про це не дізнається ніколи.

**Лічильники по кожному типу** — у `SyncRun.stats["issues"]` і у виводі `sync_prices`.
