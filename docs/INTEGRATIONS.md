# Complex — Інтеграції v3

> **Джерела фактів (не з пам'яті):**
> * **Нова Пошта** — **live-профілювання API** (`api.novaposhta.ua/v2.0/json/`, 07.2026), бо
>   `developers.novaposhta.ua` закритий WAF-ом.
> * **Hotline** — офіційна специфікація + офіційні файли (`pricelists_specs`, `hli_ukr_21092023.xml`,
>   `example_ukr.xls`, `hotline_tree_uk.csv`). Розбір: `docs/research/HOTLINE.md`.
> * **LiqPay** — офіційна дока, тарифи, довідник ПриватБанку. Розбір: `docs/research/LIQPAY.md`.
> * **Claude API** — `platform.claude.com/docs` (звірено 13.07.2026). Розбір: `docs/research/TRANSLATION.md`.

**Що зафіксовано в цій версії (проти v2):**

| # | Було | Стало |
|---|---|---|
| 1 | Платіжка не обрана (mono/LiqPay) | ⚡ **LiqPay** — єдиний провайдер. Абстракція лишається, реалізація одна (§3) |
| 2 | Hotline: XML **або** XLS | ⚡ **тільки XML**, повна специфікація тегів (§2) |
| 3 | Claude — тільки збір характеристик | ⚡ **два застосування**: переклад UA→RU + `aispecs` (§5) |
| 4 | Габарити для НП — «десь взяти» | ⚡ **з характеристик** «Габарити упаковки» / «Вага в упаковці» (§1.7) |

---

## 1. Нова Пошта

### 1.1 Базове

* Endpoint — **один на все**: `POST https://api.novaposhta.ua/v2.0/json/`
* Тіло: `{"apiKey": "<key>", "modelName": "...", "calledMethod": "...", "methodProperties": {...}}`
* Ключ: `my.novaposhta.ua` → Налаштування → Безпека / API 2.0 → «Створити ключ». Безкоштовно.
* 🔴 **HTTP-статус ЗАВЖДИ 200 — навіть на помилках.** `resp.raise_for_status()` **не врятує**.
  Єдина правда — поле `success`. І `errors` буває **порожнім масивом** навіть при `success:false`
  (напр. на неіснуючому модулі), тому на `errors[0]` теж покладатись не можна.
  Логувати треба `errorCodes` — вони стабільні, тексти ні.
* Rate-limit заголовків **немає** (`X-RateLimit-*`, `Retry-After` відсутні). Офіційних цифр НП не публікує.
  Про ліміт дізнаєшся через раптовий `success:false` → **retry з backoff обов'язковий**.
* `Accept-Encoding: gzip` — **8× економії** на синку довідників (httpx робить це сам).

### 1.2 Клієнт — власний, тонкий (`integrations/novaposhta/client.py`)

**Рішення: писати своє, не брати бібліотеку.** Нам треба рівно 5 методів однакової форми
(один endpoint, один POST). Обгортка = ~60 рядків. `novaposhta-python-client` — 0.1.x (pre-1.0),
`novaposhta-api-client` мертвий з 2020. І головне: 90% реальної роботи — це **власний шар синку/кешу
в Postgres**, якого не покриває жодна бібліотека.

```python
NP_URL = "https://api.novaposhta.ua/v2.0/json/"
UZHHOROD_CITY_REF = "e221d627-391c-11dd-90d9-001a92567626"   # City/DeliveryCity ref, константа

class NovaPoshtaError(RuntimeError):
    def __init__(self, errors: list[str], codes: list[str]):
        self.errors, self.codes = errors, codes
        super().__init__("; ".join(errors) or f"NP call failed (codes={codes})")

def call(model: str, method: str, *, timeout: tuple[float, float], **props) -> list[dict]:
    r = httpx.post(NP_URL, json={"apiKey": KEY, "modelName": model,
                                 "calledMethod": method, "methodProperties": props},
                   timeout=httpx.Timeout(connect=timeout[0], read=timeout[1], write=2, pool=2))
    r.raise_for_status()              # ловить лише 5xx/мережу
    d = r.json()
    if not d.get("success"):          # ← справжня перевірка
        raise NovaPoshtaError(d.get("errors") or [], d.get("errorCodes") or [])
    return d["data"]
```

### 1.3 ⚡ Circuit breaker + жорсткі таймаути (CRITICAL, ADR-020) — **без змін у v3**

> Зміна джерела габаритів (§1.7) **нічого тут не послаблює**: калькулятор і далі ходить у НП з
> критичного шляху, отже breaker і жорсткі таймаути лишаються обов'язковими.

**Проблема, яку це закриває.** «API НП не викликається з критичного шляху, КРІМ розрахунку ціни» —
і **це і є діра**. Якщо НП відповідає 30 с або висить, кожен запит `/delivery/quote` тримає
gunicorn/uvicorn-воркер. Кілька десятків користувачів на калькуляторі — **усі web-воркери зайняті,
лягає весь сайт**, а не лише калькулятор.

| Контекст | connect | read | ретраї | breaker |
|---|---|---|---|---|
| **Веб-запит** (`/delivery/quote`) | **2 с** | **3 с** | максимум **1** | так |
| Celery (`sync_np_refs`) | 5 с | 60 с | 5 з backoff | ні |

```python
# core/breaker.py — Redis, db2
class CircuitBreaker:
    """5 помилок за 60 с → 60 с не ходимо в сервіс взагалі."""
    FAIL_THRESHOLD = 5
    WINDOW = 60
    OPEN_FOR = 60

    def is_open(self) -> bool: ...
    def record_failure(self) -> None: ...
    def record_success(self) -> None: ...   # скидає лічильник
```

**Поведінка при відкритому breaker:**
```python
# API одразу віддає 200, НЕ 5xx і НЕ таймаут
{"ok": False, "reason": "np_unavailable", "cost": None}
```
**Фронт НІКОЛИ не блокує кнопку «Оформити»** на quote. При `ok:false` показує
«Вартість доставки повідомить менеджер», замовлення оформлюється з `delivery_cost_estimate=NULL`.

### 1.4 Довідники — власні таблиці + нічний синк

**Реальні обсяги (заміряно):**

| Метод | totalCount | Сторінок @ Limit=5000 | gzip |
|---|---|---|---|
| `Address/getAreas` | 25 | 1 | ~5 КБ |
| `Address/getCities` | **11 102** | 3 | ~1 МБ |
| `Address/getSettlements` | **26 792** | 6 | ~5 МБ |
| `Address/getWarehouses` | **52 974** | 11 | ~22 МБ |

**Разом ~21 запит, ~28 МБ gzip, хвилина роботи.**

* `Limit=5000` **реально працює**. **Старий ліміт 150 для `getSettlements`, який досі пишуть
  у всіх SDK і блогах, — застарілий міф.** Не копіюйте цю логіку з PHP-бібліотек.
* `Page` — **1-based**, не 0. `info.totalCount` є в `getCities`/`getSettlements`/`getWarehouses`.
* `getWarehouses` повертає **54 поля** на рядок — **обов'язково проєктуйте до ~15** при збереженні,
  інакше 178 МБ сирих даних.

**🔴 Пастка: `SettlementRef ≠ CityRef`.** `searchSettlements` повертає **два різні UUID**:
```json
{"Present":"м. Ужгород, Закарпатська обл.",
 "Ref":"e71f4773-4b33-11e4-ab6d-005056801329",           // SettlementRef
 "DeliveryCity":"e221d627-391c-11dd-90d9-001a92567626",  // CityRef ← КАНОНІЧНИЙ
 "Warehouses":317, "Area":"Закарпатська", "SettlementTypeCode":"м."}
```
Зберігаємо **обидва** (`NPSettlement.ref` + `NPSettlement.delivery_city_ref`).
`DeliveryCity` — канонічний для `CitySender`/`CityRecipient` і `getWarehouses.CityRef`.
> Хороша новина: `getDocumentPrice` **толерантний** — приймає і settlement, і city ref, ціна однакова.
> Погана: поле `Warehouses: 317` у `searchSettlements` **не збігається** з реальним
> `getWarehouses(CityRef=Ужгород) → totalCount: 178`. **Не використовуйте його як лічильник.**

**Фільтри при збереженні:**
* `WarehouseStatus == "Working"` — інакше пропонуємо закриті відділення.
* `CategoryOfWarehouse` → `Branch` | `Postomat` | `DropOff`. **В Ужгороді: Postomat 148, Branch 20,
  DropOff 10** — поштоматів у 7 разів більше. **Не змішувати їх в один плоский список у UI** — групувати.
* `ReceivingLimitationsOnDimensions` (`{"Width":40,"Height":30,"Length":60}`) — **фільтрувати поштомати
  за габаритами товару**. Холодильник туди фізично не влізе. Це найчастіший баг у чекаутах.

### 1.5 ⚡ Синк довідників `sync_np_refs()` — guard + деактивація (був major)

```python
@shared_task(queue="default", acks_late=True, soft_time_limit=600)
def sync_np_refs():
    run = SyncRun.objects.create(kind=NP_REFS)
    with advisory_lock("sync:np_refs"):
        areas       = fetch_all("Address", "getAreas")
        cities      = fetch_all("Address", "getCities")
        settlements = fetch_all("Address", "getSettlements")
        warehouses  = fetch_all("Address", "getWarehouses")   # будь-яка помилка → FAILED цілком

        # ⚡ GUARD (у v1 його не було ЗОВСІМ — guard був тільки в прайс-синку)
        for model, fetched in [(NPWarehouse, warehouses), (NPSettlement, settlements)]:
            current = model.objects.filter(is_active=True).count()
            if current and len(fetched) < current * 0.8:
                run.status = ABORTED
                alert(f"НП віддала {len(fetched)} з {current} — довідник НЕ чіпаємо")
                return                      # ⚠️ довідники недоторкані

        with transaction.atomic():          # ⚡ увесь довідник в одній транзакції
            upsert(...)                     # по Ref
            # ⚡ ЗНИКЛІ — is_active=False, НІКОЛИ не DELETE (на них посилаються старі замовлення)
            NPWarehouse.objects.filter(is_active=True).exclude(last_seen_run=run.id) \
                               .update(is_active=False)
            NPSettlement.objects.filter(is_active=True).exclude(last_seen_run=run.id) \
                                .update(is_active=False)

        # ⚡ Синтетичний smoke-тест: інакше rollback + алерт
        assert NPWarehouse.objects.filter(city_ref=UZHHOROD_CITY_REF, is_active=True).exists()
        assert NPWarehouse.objects.filter(city_ref=KYIV_CITY_REF, is_active=True).exists()
    healthchecks_ping("np-refs")
```

**Чому це critical.** Відділення НП закриваються і переносяться регулярно (під час війни — масово).
Ref зниклого відділення просто не приходить у відповіді, а рядок у нашій таблиці лишався
`is_active=True` **назавжди** → покупець обирає неіснуюче відділення → `CheckConstraint` проходить,
замовлення створюється, **а ТТН не вибивається**. Дзеркально: без guard'а частковий збій НП
збіднював `NPWarehouse` → у дропдауні **немає відділень** → замовлення оформити **неможливо**.
Повна зупинка продажів через збій **чужого** API — при тому, що вся ідея локальних довідників
була саме в тому, щоб від нього не залежати.

### 1.6 Автокомпліт міста — з ВЛАСНОЇ БД

```
GET /api/delivery/settlements?q=Ужг&limit=10
→ NPSettlement.objects.filter(is_active=True).extra(...)  # pg_trgm по present
  ORDER BY similarity(present, q) DESC, warehouses_count DESC
```
**< 5 мс з Postgres** проти 200–400 мс через НП, і аптайм НП **не блокує чекаут**.
27k населених пунктів — це ніщо для Postgres з `GIN(present gin_trgm_ops)`.
> `Address/searchSettlements` (параметр — **`CityName`**, не `FindByString`!) лишається
> як **аварійний фолбек**, якщо наша БД порожня після першого деплою.

### 1.7 Розрахунок вартості — `InternetDocument/getDocumentPrice`

```json
{"apiKey":"<key>", "modelName":"InternetDocument", "calledMethod":"getDocumentPrice",
 "methodProperties": {
   "CitySender":    "e221d627-391c-11dd-90d9-001a92567626",   // Ужгород
   "CityRecipient": "<city_ref або settlement_ref>",
   "Weight":        "2",
   "ServiceType":   "WarehouseWarehouse",
   "Cost":          "1500",          // оголошена вартість = ціна товару
   "CargoType":     "Parcel",
   "SeatsAmount":   "1",
   "OptionsSeat":   [{"weight":"2","volumetricWidth":"50",
                      "volumetricHeight":"50","volumetricLength":"60"}]
 }}
```
Відповідь мінімальна: `{"Cost": 539.5, "AssessedCost": 1500}`. **Строку доставки метод НЕ повертає**
(для неї — окремий `getDocumentDeliveryDate`).

#### 🔴 Об'ємна вага — найдорожча пастка проєкту

**Коефіцієнт = 250 кг/м³**, ціна рахується від **`max(фактична вага, об'ємна вага)`**.

| Запит (Ужгород→Київ, Cost=1500) | Cost |
|---|---|
| `Weight=2` **без габаритів** | **97.5** |
| `Weight=2` + `VolumeGeneral=0.15` | **539.5** |
| `Weight=2` + `OptionsSeat 50×50×60 см` | **539.5** |
| `Weight=37.5` (фактична, без об'єму) | **539.5** |

`0.15 м³ × 250 = 37.5 кг` — усі три сходяться в копійку. **Різниця 5.5×.**
Для магазину побутової техніки (холодильники, пральні) калькулятор без габаритів **систематично
бреше покупцеві вниз**, і магазин попадає на гроші **при кожному замовленні**.

#### ⚡ Габарити і вага — ЄДИНЕ ДЖЕРЕЛО: ХАРАКТЕРИСТИКИ ТОВАРУ (ADR-021)

**Блокер знято** (INPUTS §3.4). Габарити **НЕ вводяться руками**, **НЕ живуть окремими полями
`Product`** і **НЕ вигадуються** — вони **вже є в таблиці характеристик** і приїжджають звичайним
синком SpecSheet, як будь-яка інша характеристика.

**Ланцюжок:** `SpecSheet (трійки з колонки J)` → `ProductAttributeValue` → `effective_dims(product)`
→ `OptionsSeat` у `getDocumentPrice` **і** в `InternetDocument/save` (ТТН). Один код на всі три точки.

| Характеристика (дослівно як у таблиці) | Куди йде |
|---|---|
| ⚡ **`Габарити упаковки (ВхШхГ) (см)`** = `171,5 x 56 x 35` | `OptionsSeat.volumetricHeight` / `volumetricWidth` / `volumetricLength` |
| ⚡ **`Вага в упаковці (кг)`** = `61.5` | `OptionsSeat.weight` + верхньорівневий `Weight` |
| ~~`Висота / Ширина / Глибина (мм)`~~ | **НЕ для НП.** Це габарити самого виробу → `<param>` Hotline + сторінка товару |
| ~~`Вага (кг)`~~ = `58` | **НЕ для НП.** Вага виробу без упаковки |

🔴 **Це різні числа, і плутати їх — дорого.** Холодильник: виріб `1635×490×278 мм`, вага `58 кг`;
упаковка `171,5×56×35 см`, вага `61,5 кг`. Об'ємна вага рахується **по упаковці** — з габаритів
виробу вийде на ~15% менше і магазин недоплатить на кожному відправленні.

**Парсер (`delivery/services/dims.py`) — пастки формату, усі видно на скріні:**
* роздільник — `x` (латинська) / `х` (**кирилична!**) / `×`, з довільними пробілами;
* десятковий роздільник — **і кома, і крапка**: у джерелі буквально `171,5 x 56 x 35` (кома)
  **поруч із** `Вага в упаковці (кг): 61.5` (крапка). Приймати **обидва**;
* одиниця — **см**, і в `OptionsSeat` НП теж **см** → конвертація не потрібна (а от `Висота (мм)`
  — це мм, ще одна причина не брати габарити виробу);
* порядок — **В × Ш × Г** (висота, ширина, глибина). **Не** ШхВхГ. Порядок читаємо з самої назви
  характеристики (`(ВхШхГ)`), а не з позиції — якщо постачальник колись дасть `(ШхВхГ)`, парсер
  має це помітити, а не мовчки переставити;
* невідповідний формат → **НЕ падаємо**: `dims_source="category"` + WARN у `SyncRun`.

```python
# delivery/services/dims.py
DIMS_RE = re.compile(r"^\s*([\d.,]+)\s*[xх×]\s*([\d.,]+)\s*[xх×]\s*([\d.,]+)\s*$", re.I)
NUM_RE  = re.compile(r"^\s*([\d]+(?:[.,]\d+)?)\s*$")      # і "61,5", і "61.5"

def effective_dims(product) -> EffectiveDims:
    """ПРІОРИТЕТ ДЖЕРЕЛ:
       1) ⚡ ХАРАКТЕРИСТИКИ: «Габарити упаковки (ВхШхГ)» + «Вага в упаковці»   ← ОСНОВНЕ
       2) фолбек: Category.default_package_{h,w,d}_cm / default_package_weight_kg
       3) фолбек: SiteSettings глобальний дефолт
       ЦЯ Ж функція — і в /delivery/quote, і в OrderItem, і в генерації ТТН."""
    return EffectiveDims(h_cm, w_cm, d_cm, weight_kg,
                         source="spec" | "category" | "default")
```

**⚡ `OptionsSeat` шлеться ЗАВЖДИ**, навіть коли `source="default"` — інакше НП рахує тільки за
фактичною вагою і магазин недоплачує в 5.5×.

`OrderItem` зберігає **ефективні** значення (`weight_kg`, `volume_m3`, `dims_source`) — те, що **реально
пішло в НП**, а не сирі з `Product`. У v1 `volume_m3` був `GeneratedField` → NULL при відсутньому вимірі
→ ТТН вибивався без габаритів → магазин **знову** недоплачував.

**Моніторинг покриття** (тепер це вимірювана метрика, а не побажання):
* в адмінці — фільтр «`dims_source != spec`» + лічильник у `SyncRun` і `FeedArtifact`;
* категорія, де **>20% товарів на дефолтах**, — це категорія, де магазин системно втрачає гроші;
* `Category.default_package_*` заповнюються менеджером **один раз на категорію** (холодильники,
  пральні, дрібна техніка) — страховка, а не «якось порахуємо»;
* ⚡ товар **без** характеристики габаритів — кандидат на `aispecs` (§5.3): ШІ пропонує
  `proposed_dims`, людина підтверджує. Це другий за важливістю (після `mpn`) сценарій ШІ-збору.

#### ServiceType — має відповідати обраній точці ⚠️

| ServiceType | Cost (2 кг, Ужгород→Київ) | Коли |
|---|---|---|
| `WarehouseWarehouse` | 97.5 | `CategoryOfWarehouse == "Branch"` |
| `WarehousePostomat` | **107.5** | `CategoryOfWarehouse == "Postomat"` |
| `WarehouseDoors` | 157.5 | кур'єр на адресу |

Обрали поштомат, а послали `WarehouseWarehouse` → **недорахували 10 грн на кожному замовленні**.

#### Інше
* `CargoType: "Cargo"` → НП **мовчки міняє** на `Parcel` (`warnings: ["CargoType is changed to Parcel"]`).
  Ставимо `Parcel`.
* Накладений платіж: `"RedeliveryCalculate": {"CargoType":"Money","Amount":"1500"}`
  → `CostRedelivery` окремим полем (~50 грн).
* Warnings `["VolumeWeight is changed", "Weight is changed"]` при `OptionsSeat` — **це норма**, не помилка.

### 1.8 Кешування quote

1. **Redis** (db1), TTL **24 год**, ключ:
   `np:quote:{city_ref}:{service_type}:{weight}:{volume}:{cost_declared}:{YYYYWW}`
   ⚡ Ключ **включає `cost_declared`** (оголошена вартість = ціна товару, а вона змінюється 4×/добу)
   **і тиждень** — інакше рядок з ключем, у який не входить актуальна ціна, віддавав би застарілу
   вартість доставки **нескінченно**.
2. **Postgres `DeliveryQuote`** — ⚡ **тільки аналітика**: пишемо на cache-miss **з семплюванням 10%**.
   `unique(cache_key)` **знято**. `purge_delivery_quotes` — 30 днів.
   Причина: `/delivery/quote` — публічний неавторизований ендпоінт, і патерн «публічний запит →
   INSERT у БД» — вектор забивання диска.
3. **Throttling** — per-IP (10/хв) **і глобальний** (щоб не спалити ліміт НП).

---

## 2. Hotline

### 2.1 ⚡ Формат — ОБРАНО XML (рішення замовника, ADR-026)

Замовник підтвердив вибір (INPUTS §1). Обґрунтування — **не «простіше генерувати»**, а те, що в XLS
фізично **немає рівно тих тегів, заради яких ми туди йдемо**. Розібрано офіційний шаблон
`example_ukr.xls` (він же — файл замовника `docs/reference/files/`, 14 колонок, `Лист1`):

| Що нам потрібно | XML | XLS |
|---|---|---|
| **`<param>` — характеристики** (у нас повна таблиця трійок) | ✅ | ❌ **немає взагалі** |
| **`<payment type="installment">`** — оплата частинами (§7 ТЗ) | ✅ | ❌ немає |
| `<delivery>` / `<store>` — доставка і пункти видачі | ✅ | ❌ немає |
| Ієрархія категорій (`parentId`) | ✅ | ❌ плоский рядок |
| Гарантія — **структуровано** (`type`, `unit`) | ✅ | ❌ одна колонка, змішує `12`, `36`, `«12 місяців від магазину»`, `«від виробника»` |
| `<region>`, `<custom>`, `<priceRUSD>`/`<rate>` | ✅ | ❌ |

🔴 **Це не питання зручності.** Правила Hotline: за наявності **унікальних пропозицій** магазин
**зобов'язаний** передати характеристики — або через `<param>` у фіді, або **руками через шаблон в
кабінеті**. XLS = ручне заповнення шаблонів на 3000+ товарів назавжди.

**Спільне обмеження обох форматів (не перевага XML):** `<image>` — **одне зображення на товар**.
Галерея у фід **не передається в жодному форматі**.

### 2.2 Структура фіда (точна специфікація Hotline)

```xml
<?xml version="1.0" encoding="UTF-8"?>   <!-- перший рядок з нульового символу; UTF-8 -->
<price>
  <date>2026-07-13 09:00</date>          <!-- строго РРРР-ММ-ДД ГГ:ХХ -->
  <firmName>Complex</firmName>
  <firmId>{SiteSettings.hotline_firm_id}</firmId>

  <!-- type:    pickup | warehouse | pochtomat | address
       carrier: ТІЛЬКИ SLF | ME | NP | UP. «Delivery» в enum НЕМАЄ (див. 2.5)
       cost:    грн · 0 = безкоштовно · null = «за тарифами перевізника»
       freeFrom: сума замовлення, від якої безкоштовно · city: тільки для type="address" -->
  <delivery id="1" type="pickup"    cost="0"/>
  <delivery id="2" type="warehouse" carrier="NP" cost="null"/>   <!-- null = за тарифами перевізника -->
  <delivery id="3" type="pochtomat" carrier="NP" cost="null"/>   <!-- поштомати НП — окремий type -->
  <delivery id="4" type="warehouse" carrier="ME" cost="null"/>
  <store id="1" name="{PickupPoint.name}" address="Ужгород, …"
         coordinates="48.62083,22.28778"
         workdays_from="9:00" workdays_to="20:00"
         sat_from="10:00" sat_to="17:00" sun_from="10:00" sun_to="17:00"></store>

  <categories>
    <!-- id — ціле, ≤18 знаків, унікальне і НЕЗМІННЕ між вивантаженнями -->
    <!-- name — назва з рубрикатора Hotline, НЕ наша маркетингова -->
    <category><id>{HotlineCategory.pk}</id><name>{HotlineCategory.name}</name></category>
    <category><id>{child.pk}</id><parentId>{parent.pk}</parentId><name>{child.name}</name></category>
  </categories>

  <items>
    <item>
      <id>{Product.pk}</id>                       <!-- ОБОВ'ЯЗК. ≤20 симв., [0-9a-zA-Z_-], ВІЧНИЙ -->
      <categoryId>{HotlineCategory.pk}</categoryId>  <!-- ОБОВ'ЯЗК. рівно ОДНА категорія -->
      <vendor>{Brand.name}</vendor>               <!-- ОБОВ'ЯЗК. рівно один; країну класти ЗАБОРОНЕНО -->
      <name>{Product.name_uk}</name>              <!-- ОБОВ'ЯЗК. тільки назва моделі, без реклами -->
      <url>https://complex.ua/ua/p/{id}/{slug}</url> <!-- ОБОВ'ЯЗК. HTTPS, УКРАЇНОМОВНА сторінка -->
      <priceRUAH>23600</priceRUAH>                <!-- ОБОВ'ЯЗК. БЕЗ роздільників розрядів -->

      <code>{Product.mpn}</code>                  <!-- ⚡ ТІЛЬКИ MPN виробника і ТІЛЬКИ якщо != "" -->
      <barcode>{Product.barcode}</barcode>        <!-- тільки штрихкод виробника -->
      <image>{main_image_url}</image>             <!-- ⚡ ОДНЕ зображення. Галереї у фіді НЕМАЄ -->

      <stock>В наявності</stock>                  <!-- рівно 3 значення, див. 2.5 -->
      <shipping>0</shipping>                      <!-- ОБОВ'ЯЗКОВО для «Під замовлення» -->
      <guarantee type="manufacturer">12</guarantee>
      <condition>0</condition>                    <!-- 0 новий · 1 refurb · 2 уцінка · 3 б/в -->

      <param name="Країна виготовлення">Сербія</param>
      <param name="Висота">1635 мм</param>        <!-- ⚡ одиниця клеїться до ЗНАЧЕННЯ (INPUTS §2) -->
      <param name="{Attribute.hotline_param_name}">{value}{unit}</param>

      <payment type="installment" from="Privatbank" min_payment="500"
               max_period="{installment_max_period}" no_down_payment="true">true</payment>
      <payment type="pay-card" fee="0">true</payment>
    </item>
  </items>
</price>
```

#### Довідник тегів `<item>` — обов'язковість і обмеження

| Тег | Обов. | Обмеження | Наше джерело |
|---|---|---|---|
| `<id>` | **ТАК** | ≤20 симв., `[0-9a-zA-Z_-]`, унікальний, **вічний**, **перевикористання id видаленого товару заборонене** | `Product.pk` (bigserial — гарантує) |
| `<categoryId>` | **ТАК** | рівно **ОДНА** категорія на товар | `Category.hotline_category_id` |
| `<vendor>` | **ТАК** | рівно один; **країну класти ЗАБОРОНЕНО** | `Brand.name` |
| `<name>` | **ТАК** | тільки назва моделі: без реклами, без переліку, без опису | `Product.name_uk` |
| `<url>` | **ТАК** | **HTTPS**, **україномовна** сторінка, домен з анкети магазину | без `/ru/` |
| `<priceRUAH>` | **ТАК** | грн з податками, **без роздільників розрядів** | `6 600,00` → `6600` |
| `<code>` | умовно | **ТІЛЬКИ MPN виробника.** Внутрішній артикул — **заборонено** | `Product.mpn`, тільки якщо `!= ""` |
| `<barcode>` | умовно | тільки штрихкод виробника | `Product.barcode` |
| `<image>` | ні | **ОДНЕ** зображення; шлях — лише `[a-z0-9-_]` | головне фото з R2 |
| `<stock>` | ні | рівно 3 значення (2.2.2) | `qty` |
| `<shipping>` | **так для «Під замовлення»** | ціле або діапазон днів | 2.2.2 |
| `<guarantee>` | ні | `type`, `unit` (2.2.1) | характеристика `Гарантійний термін` |
| `<param name>` | ні | назва/одиниця/значення — **як у каталозі Hotline** | `Attribute.hotline_param_name` |
| `<condition>` | ні | `0` новий · `1` refurb · `2` уцінка · `3` б/в (**`3` не вивантажується**) | `Product.condition` |
| `<payment>` | ні | installment + комісії (2.2.3) | LiqPay |
| `<region>` | ні | обласний центр укр., через кому. **Фід має пріоритет над налаштуванням в акаунті** | не віддаємо → показ у всіх областях |
| `<delivery>` | ні | на рівні товару: `cost`, `freeFrom`, `unavailable` | не віддаємо (тільки рівень магазину) |
| `<custom>` | ні | ціле; відбір в Аукціоні Hotline; вимагає `<id>` | не використовуємо |
| `<rate>` / `<priceRUSD>` | ні | курс USD; **обов'язковий, якщо ціни в USD** | ❌ не потрібен — віддаємо `priceRUAH` |
| `<description>` | ні | **на сайті НЕ показується**, HTML **заборонений** | ⚡ **не віддаємо** (див. нижче) |

⚡ **`<description>` ми НЕ віддаємо.** На сайті Hotline він не показується, HTML у ньому **заборонений**,
а наш опис — це rich HTML із вбудованими фото (INPUTS §2). Характеристики передаємо через `<param>` —
саме для цього і обрано XML. Якщо колись знадобиться — тільки плоский `Ключ: значення;`.

⚡ **`<rate>` нам не потрібен.** Ціни в прайсі є в USD, але у фід іде **тільки `priceRUAH`** (уже
сконвертована ціна каталогу). Віддавати `priceRUSD` + `rate` означало б, що Hotline перераховує ціну
**своїм** курсом на **свій** момент — і вона розійдеться з ціною на нашому сайті. Це прямий шлях до
скарги «ціна на Hotline не збігається з ціною в магазині» і зняття з майданчика.

### 2.2.1 Гарантія — від виробника, термін із характеристики

Замовник підтвердив: **«Гарантія від виробника»** (INPUTS §1).

* `type="manufacturer"` — **завжди**. `type="shop"` не використовуємо. Для одного товару допустимий
  **лише один** тип.
* Значення — **число, за замовчуванням у місяцях**. Джерело — характеристика
  **`Гарантійний термін (міс)`** (`12`) → `<guarantee type="manufacturer">12</guarantee>`.
* `unit="days"` — якщо колись з'явиться термін у днях.
* ⚡ **Немає характеристики → тег ОПУСКАЄМО** (він опційний). **Дефолт 12 не підставляємо** — це
  обіцянка покупцеві від імені магазину, вигадувати її не можна.

### 2.2.2 Наявність і строк відвантаження

| `<stock>` | Коли ставимо | Поведінка Hotline |
|---|---|---|
| `В наявності` | `qty > 0` | вивантажується, `<shipping>0</shipping>` |
| `Під замовлення` | є в прайсі, `qty = 0` | вивантажується, **`<shipping>` ОБОВ'ЯЗКОВИЙ** (строк постачання) |
| `Немає` | немає в поточному зрізі прайсу | **на сайт НЕ вивантажується** |

Будь-яке інше значення = порожній тег. На сторінці товару значення `stock` **не показується** — воно
керує самим фактом завантаження.

`<shipping>` — ціле або діапазон днів; градації показу: `0` → «сьогодні», `1–2`, `3–6`, `7–14`,
`>15` → «більше 14 днів»; за діапазону значущим є **максимум**. **Немає тега → Hotline пише
«уточнюйте у продавця»** (гірша конверсія), тому віддаємо завжди.
Час відсічки для «сьогодні»: `<shipping workdays="16:00" saturday="18:00" sunday="14:00">0</shipping>`.

**Мапінг Complex:** `qty > 0` → `В наявності` + `<shipping>0</shipping>` (самовивіз в Ужгороді того ж дня);
`qty = 0`, але товар є в зрізі прайсу → `Під замовлення` + `<shipping>` з терміну постачання
(`Category.lead_time_days`, дефолт — уточнити в замовника).

### 2.2.3 ⚡ Оплата частинами у фіді ← ПРОВАЙДЕР ВИЗНАЧЕНО: LIQPAY

`from` — з **фіксованого списку** Hotline: `Магазин`, `Monobank`, **`Privatbank`**, `Sense bank`,
`A-bank`, `PUMB`, `Oschadbank`, `OTPBank`, `Izibank`, `Ukrsibbank`. Провайдер — **LiqPay = ПриватБанк**,
отже єдине коректне значення: **`from="Privatbank"`**. (В офіційному прикладі Hotline трапляється
`from="Монобанк, ПриватБанк"` — **не копіювати**: у нас монобанку в чекауті немає, це обіцянка,
якої ми не виконаємо.)

```xml
<payment type="installment" from="Privatbank" min_payment="500"
         max_period="6" no_down_payment="true">true</payment>
<payment type="pay-card" fee="0">true</payment>   <!-- ⚡ fee ЗАВЖДИ 0, див. нижче -->
```

| Атрибут | Значення в Complex |
|---|---|
| `from` | **`Privatbank`** (жорстко) |
| `min_payment` | мінімальний платіж — узгодити з замовником; ліміт суми LiqPay — **300 грн** знизу |
| `max_period` | `Product.installments_max_payments` — ⚠️ **маркетингова обіцянка**, не технічне обмеження |
| `no_down_payment` | `true` (ОЧ від ПБ — без першого внеску) |

🔴 **`max_period` — обіцянка, а не обмеження.** LiqPay Checkout **не має параметра «максимум N
платежів»**: кількість (2–25) обирає **покупець** на сторінці оплати (§3.7). Тобто якщо у фіді
написано `max_period="6"`, а покупець обере 12 — магазин заплатить **13,7%** замість 6,5%.
Це треба **проговорити з замовником до релізу**.

⚡ **`fee="0"` на `pay-card` — не наша щедрість, а вимога LiqPay:** surcharge (перекладання комісії
еквайрингу на покупця) **прямо заборонений** умовами (§3.0). Ціна у фіді = ціна на сайті = ціна на
чекауті. Інші типи комісій — той самий тег: `cash-on-delivery`, `card-in-shop`, `card-card`,
`vat`/`non-vat`.

⚡ Тег `installment` віддаємо **тільки для товарів з `installments_enabled=True`** і **тільки після**
підтвердження замовником `min_payment` — інакше не віддаємо взагалі (тег опційний). Товар дешевший
за **300 грн** розстрочку не отримає фізично → тег не віддаємо навіть при увімкненому тумблері.

### 2.3 ⚡ Правила скіпу (був critical)

| Причина | Скіп? | Лічильник |
|---|---|---|
| `hotline_enabled = False` | так (не рахуємо) | — |
| `is_active = False` або `category.is_active = False` | **так** | `inactive` |
| `Category.hotline_category_name == ""` | **так** | `no_hotline_category` |
| Немає ціни / `price <= 0` | **так** | `no_price` |
| Немає жодного завантаженого фото | **так** | `no_image` |
| `availability = out_of_stock` | **так** (Hotline: «Немає» → товар не вантажиться) | `out_of_stock` |
| **`mpn == ""`** | ⚡ **НІ!** | `no_mpn_count` (тільки лічильник) |

**Чому це було critical.** У v1 `<code>` = `Product.mpn` **був умовою скіпу**, а `mpn` **нічим не
заповнюється**: у прайсі колонки «Артикул виробника» немає, у SpecSheet немає, синк його не пише.
Тобто на старті `mpn` порожній у **100%** товарів → **фід порожній**, і вся вимога ТЗ «експорт для
Hotline» (заради якої і тумблер, і масове вмикання) не працює з коробки.

За специфікацією Hotline `<code>` — **умовно обов'язковий**: фід без нього валідний, просто товари
гірше мержаться з картками (менше трафіку). Тому:
* `<code>` віддаємо **тільки якщо `mpn != ""`**;
* ⚡ `mpn` додано як **опційну колонку** в `column_map` (PriceSheet і SpecSheet);
* ⚡ `mpn` — серед полів, які пропонує `aispecs` (ШІ цілком здатен витягти артикул виробника);
* ⚡ в адмінці — фільтр «`hotline_enabled=True`, `mpn` порожній» + `FeedArtifact.no_mpn_count`.

### 2.4 ⚡ Масове вмикання на категорію (був critical)

```python
@shared_task(queue="default")
def set_hotline_for_category(category_id: int, value: bool, include_descendants=True, user_id=None):
    cat = Category.objects.get(pk=category_id)
    qs = Product.objects.filter(category__path__startswith=cat.path) if include_descendants \
         else Product.objects.filter(category=cat)
    n = qs.update(hotline_enabled=value, updated_at=Now())
    # аудит: хто, коли, скільки
```
**Плюс `Category.hotline_enabled_default`** — успадковується **новим** товаром при створенні
(в INSERT-частині проєкції, **але не в UPDATE** — щоб не затирати ручне вимкнення).

**Чому і те, і те.** У v1 масова дія була стандартним Django-екшеном по **виділених рядках сторінки**
(100 шт.), а не по категорії з нащадками. І навіть якби менеджер один раз проставив тумблер на 800
товарів, синхронізація 4×/добу створювала б **нові** товари в цій же категорії з `hotline_enabled=False`
— категорія «протікала» **назавжди**.

### 2.5 Мапінг категорій і доставки

**Рубрикатор** (перевірено live): `https://hotline.ua/download/hotline/hotline_tree_uk.csv`
— HTTP 200, `application/octet-stream`, ~28 КБ, **1223 рядки**, кодування **windows-1251** (не UTF-8 →
`decode("cp1251")`). Це **не CSV**, а дерево з відступами: **кількість провідних `;` = глибина**.
4 рівні: 17 кореневих → 108 → 663 → 435.

```
Побутова техніка
;Велика побутова техніка
;;Холодильники
;;Пральні машини
```

* **ID категорій Hotline у файлі НЕМАЄ** — тільки назви. Зв'язок робиться **по ТОЧНІЙ назві кінцевої
  (листової) категорії**: «кінцева категорія у фіді повинна відповідати аналогічній у каталозі Хотлайн».
* ⚡ **Модель `HotlineCategory(path, name, depth, parent)`** — Celery-таск `sync_hotline_tree` раз на
  тиждень тягне CSV, декодує cp1251, парсить відступи, робить upsert по `path`.
* `Category.hotline_category` — **FK на `HotlineCategory`** (nullable), обирається в Django Admin
  автокомплітом. **Не рядок** — рядок ламається від першої ж описки в назві.
* У `<category><name>` віддаємо **назву з рубрикатора**, `parentId` будуємо з **дерева рубрикатора**
  (не з нашого!) — тому в фід йде гілка Hotline, а не наша маркетингова структура.
* Порожній `hotline_category` → товари категорії у фід **не йдуть** + WARN у
  `FeedArtifact.skipped_reasons` (лічильник `no_hotline_category`), а не мовчазне вивантаження.
* `<param name>` теж прив'язані до каталогу Hotline → `Attribute.hotline_param_name`.
  Порожньо → параметр у фід не йде (товар йде).

**🔴 Пастка `<delivery carrier>`:** enum — **тільки `SLF`, `ME`, `NP`, `UP`**.
Нова Пошта = `NP`, Meest = `ME`, Укрпошта = `UP`. **Перевізника «Delivery» в enum НЕМАЄ** —
у фід не передаємо (або мапимо на `SLF` = власна доставка). Самовивіз в Ужгороді = `type="pickup"`
+ `<store>` з координатами і графіком (ПН–ПТ 09:00–20:00, СБ–НД 10:00–17:00 — INPUTS §4).

### 2.6 Вимоги до віддачі фіда (жорсткі)

Дослівно з правил Hotline: файл «має бути доступним по HTTP(S). **Не допускається використання
авторизації**, а також **використання JavaScript** для перенаправлення або динамічного підвантаження.
**Неприпустиме блокування доступу робота**.»

🔴 **Фід публікується 7 днів.** Якщо за цей час не завантажено оновлений — публікація **припиняється**
і товари зникають з майданчика. Beat: генерація **4×/добу** (слідом за прайс-синком), запобіжник —
`heal_hotline_feed` (нижче). Hotline **не публікує** ані User-Agent, ані IP-діапазони робота →
**allowlist за UA/IP будувати не можна**, шлях фіда має бути повністю «тупим».

⚡ **Наслідки для інфраструктури:**
* `https://complex.ua/feeds/hotline.xml` → **статичний файл з named volume**, віддає Caddy.
  **Named volume, а не шар контейнера** — інакше будь-який `compose up -d` **знищує файл**
  → бот Hotline отримує 404 → **товари знімаються з майданчика**.
* Volume змонтований і в `backend`/`worker` (запис), і в `caddy` (читання).
* **Жодних редіректів, Basic Auth, Cloudflare JS-челенджу** на цьому шляху. UA Hotline — whitelist.
* Генерація — **atomic write + rename** (бот не має спіймати напівзаписаний файл).
* ⚡ **Моніторинг свіжості:** `heal_hotline_feed` (щогодини) — `FeedArtifact.is_current` старший 24 год
  **АБО файл на диску відсутній** → регенерація + алерт. Плюс зовнішній probe: HTTP 200,
  `Content-Length > 0`, **немає редіректів**.
  Без цього фід може мовчки не генеруватися тижнями і віддавати старі ціни — **за це Hotline
  штрафує/відключає**.

### 2.7 Санітизація (чек-лист)

1. **Заборонені control-символи ASCII 0–31** (крім 9/10/13) — описи з CMS часто містять `\x0b`, `\x1f`.
2. **HTML заборонено у ВСІХ без винятку елементах** — не тільки в `description`. Наш rich-HTML опис
   у фід не йде взагалі.
3. Екранувати `< > & ' "` → `&lt; &gt; &amp; &apos; &quot;`.
4. **Ціна** — тільки число, **без роздільників розрядів**: парсер бачить `6 600,00` → у фід іде `6600`.
5. **Зображення** — шлях **лише латиниця/цифри/`-`/`_`**. Кириличні імена = **товар без фото**.
   → **ключі об'єктів у R2 генеруємо транслітом/slug'ом** (`siemens-hz66d910-1.jpg`) — це вимога фіда,
   а не побажання. Формат: JPEG (бажано), GIF/PNG **без прозорих областей**, білий фон, без написів,
   логотипів бренду і водяних знаків.
6. `<url>` — **HTTPS**, **україномовна** сторінка **того самого домену**, що в анкеті магазину.
   Ніяких `/ru/`.
7. `<vendor>` — рівно **один виробник**; **класти країну-виробника ЗАБОРОНЕНО** (для неї є
   `<param name="Країна виготовлення">`). `<name>` — тільки назва моделі: без реклами, без переліку,
   без описової інформації.
8. `<code>` — **тільки MPN виробника**. Класти туди наш внутрішній артикул (`2400042`) **прямо
   заборонено** — він іде в `<id>`. Це остаточно закриває відкрите питання №2 з INPUTS.
9. Заборонені: контакти (тел/email), КАПС, рекламні слова («знижка», «акція», «новинка»), написи на фото.
10. `<id>` товару — **вічний**, ≤20 симв., `[0-9a-zA-Z_-]`. Перевикористання id видаленого товару
    **заборонено** → `Product.pk` (bigserial) це гарантує.
    `<id>` категорії — ціле, ≤18 знаків, унікальне і незмінне → `HotlineCategory.pk`.
11. `<condition>`: `0` новий (дефолт) · `1` refurbished (Grade A) · `2` **уцінений** · `3` б/в.
    ⚡ Товари з `3` Hotline на сайт **не вивантажує** взагалі. Уцінка (INPUTS питання №6) → `condition=2`.
12. Ліміт — **150 000 товарів**. Кодування — UTF-8 (або windows-1251).

---

## 3. Платежі — LiqPay ⚡ (ADR-025)

### 3.0 Рішення і комерційні наслідки

**Провайдер обрано: LiqPay (ПриватБанк).** Питання Q-PAY-1 закрите.

**Чому не monobank.** Єдиний критерій, який реально відрізняв провайдерів, — **оплата частинами**:

| | **LiqPay / ПриватБанк** | monobank |
|---|---|---|
| Оплата частинами | **галочка в кабінеті** («Кредити») + `paytypes` — **той самий чекаут**, ~15 хв | **ОКРЕМА інтеграція**: інший API, HMAC-SHA256, окремий договір, до 2 днів |
| Комісія еквайрингу | **1,5%** | від 2,9% |

**Цифри, які треба знати ДО того, як обіцяти замовнику плашку «6 платежів»:**

| Що | Скільки | Хто платить |
|---|---|---|
| Еквайринг | **1,5%** (індивідуальний тариф — від 300 тис. грн обороту) | магазин |
| **«Оплата частинами»** (`paypart`) | **2,3%** (2 платежі) · **3,6%** (4) · **6,5%** (6) · **13,7%** (12) · **27,3%** (25) — **+ еквайринг зверху** | **магазин** |
| **«Миттєва розстрочка»** (`moment_part`) | **0%** + еквайринг | **покупець** (1,9%/міс) |
| Ліміт суми розстрочки | **300 – 300 000 грн**, тільки UAH, тільки клієнти ПБ з кредитним лімітом | — |

🔴 **Холодильник за 30 000 грн на 6 платежів коштує магазину 8% = 2 400 грн.** Якщо маржа <10% —
розстрочка з'їдає її повністю. Тому `installments_enabled` — **тумблер на товарі**, а не глобальна
опція. Замовнику показати цю таблицю **до** релізу (див. §3.7).

🔴 **Surcharge заборонений.** LiqPay **прямо забороняє** додавати комісію еквайрингу до ціни товару
(«+2% за оплату карткою»). Комісію не можна перекласти на покупця **ні за еквайринг, ні за розстрочку**,
і **заборонено завищувати ціну для покупок у розстрочку**. Наслідки: ціна на сайті = ціна на чекауті
= ціна у фіді Hotline, і `<payment type="pay-card" fee="0">` (§2.2.3).

### 3.1 Абстракція лишається (`payments/providers/base.py`)

Абстракція є, реалізація **одна**. Це не «на всяк випадок» — це те, що дозволяє додати другий контур
(напр. накладений платіж або Checkbox-фіскалізацію) без переписування `orders`.

```python
class PaymentProvider(Protocol):
    def create_checkout(self, payment: Payment) -> CheckoutForm: ...   # {url, data, signature}
    def verify_callback(self, form: Mapping[str, str]) -> bool: ...    # підпис
    def parse_callback(self, form: Mapping[str, str]) -> ProviderEvent: ...
    def fetch_status(self, reference: str) -> ProviderEvent: ...       # для reconcile
    def refund(self, payment: Payment, amount: Decimal) -> ProviderEvent: ...
```
Активний провайдер — `SiteSettings.payment_provider` (**зміна = значення в адмінці, не деплой**).
Єдина реалізація — **`LiqPayProvider`**.

```
payments/
  models.py                 # Payment, PaymentCallback, Refund
  providers/
    base.py                 # PaymentProvider (Protocol) + ProviderEvent
    liqpay/
      client.py             # LiqPayClient (httpx): підпис, checkout, api()
      provider.py           # LiqPayProvider(PaymentProvider)
      statuses.py           # LIQPAY_STATUS_MAP → наш enum
      checkout.py           # побудова params з Order
  services.py               # ⚡ apply_payment_status() — ЄДИНА точка переходу станів
  api.py                    # Ninja: POST /orders/{id}/pay, POST /webhooks/payments/liqpay
  tasks.py                  # reconcile_liqpay_payments, fiscalize_order
```

### 3.2 Підпис і транспорт

Усе API LiqPay — це **два поля**:

```
data      = base64( json_utf8(params) )
signature = base64( sha1_binary( private_key + data + private_key ) )
```

🔴 **Три місця, де це ламається:**
1. `sha1` береться від **бінарного digest**, а **не** від hex-рядка.
2. `private_key` конкатенується **з обох боків** саме `data` (**base64-рядка**), а не JSON.
3. Порівняння підпису — **тільки `hmac.compare_digest`** (constant-time), інакше timing-oracle.

| Ціль | URL | Транспорт |
|---|---|---|
| Чекаут (редірект покупця) | `https://www.liqpay.ua/api/3/checkout` | HTML-форма **POST** (`data`, `signature`) |
| Server-to-server (`status`, `refund`, `reports`) | `https://www.liqpay.ua/api/request` | POST form-urlencoded |

```python
# integrations/liqpay/client.py
CHECKOUT_URL = "https://www.liqpay.ua/api/3/checkout"
API_URL      = "https://www.liqpay.ua/api/request"

class LiqPayClient:
    def encode_data(self, params: dict) -> str:
        payload = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
        return base64.b64encode(payload.encode("utf-8")).decode("ascii")

    def make_signature(self, data: str) -> str:
        raw = self._private_key + data.encode("ascii") + self._private_key
        return base64.b64encode(hashlib.sha1(raw).digest()).decode("ascii")   # ⚠️ .digest(), не .hexdigest()

    def verify_signature(self, data: str, signature: str) -> bool:
        return hmac.compare_digest(self.make_signature(data), signature)      # ⚠️ constant-time
```

⚠️ **`version` тримаємо в конфізі, не в коді.** У прикладі Checkout і в URL — `3`; у таблиці параметрів
LiqPay поле описане як «поточне значення 7». **Перевірити на пісочниці 3 vs 7** і взяти те, що віддає
**повний callback**.

⚠️ **Офіційний SDK `liqpay/sdk-python` використовувати ЗАБОРОНЕНО.** Це Python 2 (`from urlparse import
urljoin`, `unicode`, `iteritems()`), останній коміт — правка README, і там **`requests.post(..., verify=False)`**
— **вимкнена перевірка TLS-сертифіката**. Пишемо свій клієнт (~80 рядків) на `httpx`: нам однаково
потрібні async, типізація і `verify_signature` з `compare_digest`, яких у SDK немає.

### 3.3 Checkout

| Параметр | Значення в Complex |
|---|---|
| `version` / `public_key` / `action` | з конфігу · ключ магазину · **`pay`** |
| `amount` / `currency` | `float(order.total)` · `UAH` |
| `description` | `Замовлення №{order.number} — Complex` |
| **`order_id`** | ⚡ **`Payment.reference` (UUID), НЕ `Order.pk`** — див. нижче |
| `paytypes` | ⚡ **завжди передаємо явно** (див. §3.7) |
| `server_url` | `https://complex.ua/webhooks/payments/liqpay` — **публічний HTTPS**, ≤510 симв. |
| `result_url` | `https://complex.ua/ua/order/{public_token}` — ⚠️ **це НЕ підтвердження оплати**, лише UX |
| `expired_date` | **UTC**, `%Y-%m-%d %H:%M:%S` — «оплатити протягом 24 год» |
| `language` | `uk` (для RU-версії сайту — `ru`) |
| `sandbox` | `1` у dev/staging (§3.8) |
| `rro_info` | фіскальний чек (§3.9) |

🔴 **`order_id` = окреме поле `Payment.reference` (UUID), а не PK замовлення.** LiqPay прив'язує
статус і рефанд до `order_id`. Якщо покупець скасував оплату і платить удруге з тим самим `order_id`,
LiqPay **поверне старий платіж**. Тому: **одне `Order` → багато `Payment`**, кожна спроба — новий
`reference`.

**Спосіб інтеграції — Redirect (POST-форма).** Бекенд віддає `{url, data, signature}` → Next.js
будує приховану форму і сабмітить.
* **Widget** (`static.liqpay.ua/libjs/checkout.js`, `mode: popup|embed`) — можна додати пізніше як
  прогресивне покращення. ⚠️ Подія `liqpay.callback` **у браузері не є доказом оплати** — довіряємо
  **тільки** `server_url`-вебхуку.
* **Server-to-server `card_payment`** (номер картки через наш сервер) — **категорично ні**: це PCI DSS.

### 3.4 Callback (`server_url`)

`POST`, `Content-Type: application/x-www-form-urlencoded`, два поля: **`data`** (base64 JSON) і
**`signature`**.

```python
@csrf_exempt
@require_POST
def liqpay_callback(request):
    data, signature = request.POST.get("data", ""), request.POST.get("signature", "")
    if not data or not client.verify_signature(data, signature):
        log.warning("liqpay: bad signature", extra={"ip": request.META.get("REMOTE_ADDR")})
        return HttpResponseBadRequest("bad signature")      # єдиний випадок НЕ-200

    payload = client.decode_data(data)                      # ⚡ дані ТІЛЬКИ звідси, ніколи з query/GET
    fingerprint = sha256(data.encode()).hexdigest()

    with transaction.atomic():
        payment = Payment.objects.select_for_update().filter(
            reference=payload["order_id"]).first()
        if payment is None:
            log.error("liqpay: unknown order_id %s", payload.get("order_id"))
            return HttpResponse("ok")                       # 200 — щоб LiqPay не довбав ретраями

        # (1) ІДЕМПОТЕНТНІСТЬ: точно такий самий callback уже оброблено
        _, created = PaymentCallback.objects.get_or_create(
            fingerprint=fingerprint, defaults={"payment": payment, "payload": payload})
        if not created:
            return HttpResponse("ok")

        # (2) ЗВІРКА СУМИ — класична діра
        if (Decimal(str(payload["amount"])) != payment.amount
                or payload["currency"] != payment.currency):
            alert(f"LIQPAY AMOUNT MISMATCH on {payment.reference}")
            return HttpResponse("ok")                       # гроші НЕ проводимо

        # (3) OUT-OF-ORDER: старіший callback не перетирає новіший
        end_date = payload.get("end_date") or payload.get("create_date") or 0
        if payment.last_end_date and end_date < payment.last_end_date:
            return HttpResponse("ok")

        # (4) ФІНАЛЬНИЙ СТАН не відкочується (єдиний дозволений перехід: paid → reversed)
        if payment.status == "paid" and payload["status"] != "reversed":
            return HttpResponse("ok")

        apply_payment_status(payment, payload, end_date)    # ⚡ ТА САМА функція, що й у reconcile

    return HttpResponse("ok")                               # LiqPay має отримати 200, інакше ретраїтиме
```

**Чому кожен із чотирьох запобіжників обов'язковий:**
* LiqPay **може прислати той самий callback кілька разів** і **не гарантує порядок**. `pending` після
  `paid` — типова ситуація при ретраях. Без (3) і (4) обробник **відкочував би `PAID` назад у `PENDING`**.
* Дедуп **тільки** по `sha256(data)` недостатній: у тілі є `end_date`, тому кожен ретрай має інше тіло
  → **перший дедуп не спрацює взагалі**. Саме тому (3) і (4) — не «додатковий захист», а основний.
* Без (2) будь-хто, хто підбере `order_id`, міг би провести замовлення на 1 грн.

⚡ **Побічні ефекти — тільки через `transaction.on_commit` → Celery**: лист покупцю, резерв товару,
фіскальний чек. Самі таски **ідемпотентні** (`Order.paid_notified_at`, `Order.fiscalized_at`).

### 3.5 Статуси LiqPay → наш enum

`statuses.py` — **один словник, одна точка правди**. Все, чого немає в мапі, → `PENDING` + WARN
(новий статус не має мовчки ставати «оплачено» або «провалено»).

| LiqPay | Наш стан | Дія |
|---|---|---|
| `success` | **PAID** | резерв, лист, фіскальний чек, `Order.status=confirmed` |
| `wait_compensation` | **PAID** | успішний, зарахується в добовій виплаті — товар віддаємо |
| `sandbox` | **PAID** *(dev/staging)* | 🔴 **у проді — ALERT і товар НЕ віддаємо**: означає витік sandbox-ключа або `sandbox:1` у бойовому конфізі |
| `hold_wait` | **HELD** | кошти заблоковані (`action=hold`) — потрібен окремий `hold_completion` |
| `wait_accept` | **PENDING** + `needs_bank_review` | ⚠️ **кошти з покупця вже списані**, магазин на верифікації. Замовлення беремо в роботу, але позначаємо |
| `wait_secure` | **PENDING** + `needs_bank_review` | платіж на перевірці — **може висіти годинами** |
| `processing`, `prepared`, `wait_card`, `wait_lc`, `wait_reserve`, `cash_wait`, `invoice_wait` | **PENDING** | чекаємо |
| `3ds_verify`, `otp_verify`, `cvv_verify`, `captcha_verify`, `ivr_verify`, `password_verify`, `phone_verify`, `pin_verify`, `sender_verify`, `receiver_verify`, `senderapp_verify`, `wait_qr`, `wait_sender` | **PENDING** | покупець ще щось підтверджує |
| `failure` | **FAILED** | «спробувати ще», `Order` лишається `pending` |
| `error` | **FAILED** + **Sentry** | ⚡ некоректні дані = **наш баг у параметрах**, а не проблема покупця |
| `reversed` | **REFUNDED** | повертаємо товар на склад |
| `subscribed` / `unsubscribed` | — | підписок немає, ігноруємо |

**Поля callback, які пишемо в БД:**
* ⚡ **`receiver_commission`** — **фактична комісія**. Це **джерело правди по юніт-економіці**: тільки
  тут видно, скільки реально коштувала розстрочка на 12 платежів.
* `paytype` (`card` / `paypart` / `moment_part` / `privat24`…), `moment_part` (bool), `payment_id`,
  `liqpay_order_id`, `sender_card_mask2`, `sender_card_bank`, `err_code`, `err_description`,
  `create_date`, `end_date`.

### 3.6 ⚡ Звірка pending-платежів (`action=status`)

```python
@shared_task(queue="default")
def reconcile_liqpay_payments():
    """Вебхук міг не дійти: бекенд лежав 10 хв на деплої, Caddy віддав 502, LiqPay вичерпав ретраї.
       Без цієї задачі: гроші зняті, замовлення ВІЧНО «Очікує оплати»."""
    stale = Payment.objects.filter(
        provider="liqpay",
        status__in=["created", "pending", "processing", "held"],
        created_at__lt=now() - timedelta(minutes=3),      # фора вебхуку
        created_at__gt=now() - timedelta(days=3),
    )[:200]

    for p in stale.iterator():
        try:
            payload = client.status(str(p.reference))     # POST /api/request, action=status
        except httpx.HTTPError as exc:
            log.warning("liqpay status failed for %s: %s", p.reference, exc)
            continue                                      # ⚡ не валимо весь прогін
        apply_payment_status_locked(p, payload)           # ⚡ ТА САМА функція, що й у callback
        p.last_polled_at = now(); p.save(update_fields=["last_polled_at"])

    # старші 48 год і досі не оплачені → EXPIRED + алерт менеджеру
    Payment.objects.filter(status__in=["created", "pending"],
                           created_at__lt=now() - timedelta(hours=48)).update(status="expired")
```

* **Beat: кожні 5 хвилин.** Плюс алерт «pending-платіж > 30 хв».
* ⚡ **Щоденний таск `reconcile_liqpay_register`**: `action=reports` тягне **реєстр платежів** за добу
  і звіряє суми **і `receiver_commission`** з нашою БД. Це закриває бухгалтерію і ловить випадок
  «callback не дійшов ВЗАГАЛІ і платіж не в нашій БД».
* 🔴 **Правило модуля: `apply_payment_status()` — одна функція, викликається і з callback, і зі звірки.**
  Ніякого дубльованого мапінгу статусів. Це та помилка, після якої стани розходяться.

### 3.7 ⚡ Оплата частинами — тумблер і його межі

**Як вмикається:** бізнес-кабінет LiqPay → меню **«Кредити»** (безкоштовно, ~15 хв, підключаються
обидва сервіси одразу) → у чекауті передаємо `paytypes`.

```python
paytypes = ["card", "privat24", "apay", "gpay"]
if allow_installments:
    paytypes += ["paypart", "moment_part"]     # paypart — платить магазин; moment_part — платить покупець
```

⚡ **`paytypes` передаємо ЗАВЖДИ явно.** Якщо не передати — LiqPay покаже те, що ввімкнено в
налаштуваннях магазину, і **по-товарний тумблер перестане працювати взагалі**.

🔴 **Обмеження, яке треба проговорити з замовником ДО релізу.**
**У Checkout API немає параметра «максимум N платежів».** Кількість (2–25) обирає **покупець** на
сторінці LiqPay. ПриватБанк це підтверджує прямо: *«Спеціального налаштування не передбачено.
Кількість платежів визначається індивідуально під час формування кожної угоди»*.
→ Бейдж «6 платежів» на картці (референс galiton) і `max_period="6"` у фіді Hotline — **маркетинг**.
Покупець обере 12 → магазин заплатить **13,7%** замість 6,5%.
→ Жорстке обмеження = **друга інтеграція** (прямий API «Оплата частинами» ПБ зі `storeId`/`password`,
повз LiqPay Checkout). **Не MVP.**
→ Альтернатива, яку варто запропонувати замовнику: класти в `paytypes` **тільки `moment_part`**
(миттєва розстрочка) — для магазину **безкоштовно**, відсотки платить покупець.

```python
# orders/services.py
INSTALLMENTS_MIN, INSTALLMENTS_MAX = Decimal("300"), Decimal("300000")

def cart_allows_installments(cart) -> bool:
    """Розстрочка доступна, лише якщо КОЖНА позиція її підтримує + сума в межах ліміту LiqPay."""
    return (cart.items.exists()
            and cart.currency == "UAH"
            and INSTALLMENTS_MIN <= cart.total <= INSTALLMENTS_MAX
            and all(i.product.installments_enabled for i in cart.items.all()))

def cart_installments_badge(cart) -> int | None:
    """Скільки платежів показувати: МІНІМУМ по всіх товарах (найконсервативніше)."""
    return (min(i.product.installments_max_payments for i in cart.items.all())
            if cart_allows_installments(cart) else None)
```

⚡ **Бекенд перераховує `allow_installments` НЕЗАЛЕЖНО при створенні чекауту** — фронту не довіряємо:
```python
params = checkout_params_for_order(order, allow_installments=cart_allows_installments(order))
```
Поля: `Product.installments_enabled` (bool) + `Product.installments_max_payments` (2–25, **тільки для
бейджа**), глобальний дефолт — у `SiteSettings` (щоб не клацати 3000 товарів руками).

### 3.8 Sandbox

Два способи, працюють обидва: **sandbox-ключі** (`sandbox_i…` / `sandbox_…` — окрема пара з кабінету)
або **`sandbox: 1`** у `data` при бойових ключах.

⚡ Усі тестові платежі приходять у callback зі статусом **`sandbox`**, а **не** `success` — мапер має
це знати (§3.5), і **в проді `sandbox` = ALERT**.

**Тестові картки** (CVV — будь-які 3 цифри, термін — будь-яка майбутня дата):

| Картка | Сценарій |
|---|---|
| `4242 4242 4242 4242` · `4000 0000 0000 3063` | успішна оплата |
| `4000 0000 0000 3089` / `3055` / `0002` | успішна з 3DS / OTP / CVV |
| `4000 0000 0000 9995` | неуспішна (помилка ліміту) |

* 🔴 **`server_url` має бути публічним HTTPS** → локально `cloudflared tunnel` / `ngrok`; на staging —
  реальний субдомен через Caddy.
* ⚡ **`manage.py liqpay_simulate_callback --order <ref> --status success`** — формує **валідно
  підписаний** callback і б'є у наш власний в'ю. Це єдиний спосіб тестувати ідемпотентність,
  out-of-order і amount-mismatch **без інтернету**. Обов'язково в тестах.
* ⚠️ **Розстрочку в sandbox повноцінно не протестуєш** (потрібен реальний кредитний ліміт ПБ) →
  перевірка реальним платежем на **300 грн** бойовими ключами + refund. Закласти в план релізу.

### 3.9 Фіскалізація (ПРРО) — блокер, який треба закрити до релізу

**Фіскальний чек обов'язковий.** При онлайн-оплаті карткою за фізичний товар ФОП груп 2–4 і юрособи
**зобов'язані** видати чек; еквайринг сам по собі це **не закриває**. Штраф — 100% суми першої
непробитої операції, 150% за кожну наступну.

**LiqPay має власний ПРРО, безкоштовний, вбудований у Checkout** — просто додаємо `rro_info` в `data`:

```python
params["rro_info"] = {
    "items": [{"amount": float(i.qty), "price": float(i.unit_price), "cost": float(i.line_total),
               "name": i.product_name[:255], "barcode": i.product.sku, "unitcode": "2009",
               "taxs": [{"name": "Без ПДВ", "letter": "А", "prc": 0, "type": 0}]}
              for i in order.items.all()],
    "delivery_emails": [order.customer_email],
}
```

🔴 **Три умови, кожна з яких може все зламати:**
1. **ПРРО від LiqPay доступний ТІЛЬКИ мерчантам із рахунком у ПриватБанку.** Рахунок в іншому банку →
   ПРРО немає, зарахування раз на добу, а **повернення робляться за рахунок майбутніх платежів**.
   → **Рекомендація: рахунок ФОП/ТОВ відкривати саме в ПБ.**
2. ⚡ **`sum(rro_info.items[].cost)` МАЄ дорівнювати `amount` платежу**, інакше фіскалізація **впаде**.
   → Це робить **відкрите питання INPUTS №5 (округлення після USD→UAH) блокером платіжного модуля**:
   округлення має бути **по-позиційне**, а не по підсумку.
3. 🔴 **Накладений платіж НП / готівка при самовивозі — LiqPay ПРРО їх НЕ закриває** (це не його
   транзакції). Якщо замовник хоче післяплату (а в побутовій техніці він її захоче) — потрібен
   **окремий ПРРО (Checkbox)**, і тоді логічніше **одразу поставити Checkbox на всі типи оплат**,
   ніж тримати два фіскальні контури. `rro_info` в LiqPay у цьому разі **не передаємо** (щоб не було
   подвійних чеків).

Архітектурно: `fiscal/providers/{liqpay_rro, checkbox}.py` — той самий патерн, що й платежі.
`Order.fiscal_receipt_id` / `fiscal_receipt_url` / `fiscalized_at`; таск `fiscalize_order` —
ідемпотентний, з ретраями.

**Питання до замовника (блокери):** (1) накладений платіж буде? (2) рахунок — у ПБ?
(3) розстрочка — `paypart` чи тільки `moment_part`? (4) `min_payment` для фіда Hotline?

### 3.10 ⚡ Секрети і PII в платежах

* **`LIQPAY_PRIVATE_KEY`**, `ANTHROPIC_API_KEY`, `google_sa.json` — через **`docker compose secrets:`**
  (файли), **не env-змінні**. Env світиться в `docker inspect`, `/proc/<pid>/environ` і breadcrumbs Sentry.
* ⚡ **`private_key` НІКОЛИ не потрапляє на фронт.** У Next.js їде тільки `{url, data, signature}` —
  готова форма. `public_key` публічний за визначенням, `private_key` живе **тільки** в Django.
* ⚡ `Payment.raw_request` / `raw_response` — **редакція за allowlist** перед збереженням.
  Ніяких ключів, підписів, PAN у БД (вони назавжди осідають ще й у бекапах). `sender_card_mask2`
  (маска `41****1234`) — можна, повний PAN ми і не побачимо.
* `PaymentCallback.payload` + сирий `data` потрібні для перевірки підпису і розборів → ретеншн **90 днів**.
* Sentry: `send_default_pii=False` + `before_send` зі скрабом (`signature`, `private_key`, `data`,
  `password`, `phone`, `email`).
* `gitleaks` у CI на кожен PR.
* ⚡ **Дві пари ключів** (бойові `i…` / тестові `sandbox_…`) — **різні секрети в різних середовищах**.
  Бойовий ключ на staging = реальні списання; sandbox-ключ у проді = статус `sandbox` і безкоштовні
  «замовлення» (тому в §3.5 на `sandbox` у проді стоїть ALERT).

---

## 4. Google Sheets

### 4.1 Auth — service account

1. GCP Console → проєкт → **Enable API: Google Sheets API**. (Drive API **не треба** — читання
   по `spreadsheetId` працює через Sheets API.)
2. IAM → Service Accounts → Create → **без жодних IAM-ролей** (доступ дає шарінг у самому Sheets).
3. Keys → Add key → JSON → `sync-bot@project-id.iam.gserviceaccount.com`.
4. Таблиця → **Share** → email SA → права **Viewer** (не Editor! принцип найменших привілеїв)
   → зняти «Notify people».
5. Scope: `https://www.googleapis.com/auth/spreadsheets.readonly`.
6. **Domain-wide delegation НЕ потрібна.**

**Зберігання:** `google_sa.json` через `docker secrets` (JSON має переноси рядків у `private_key`,
які ламають `.env`). Підтримуємо і base64-env (`GOOGLE_SA_CREDENTIALS_B64`) — зручно для CI.
**Ротація ключа — раз на рік** (в GCP можна тримати 2 активні: додали новий → задеплоїли → видалили старий,
нуль даунтайму). Записати в runbook.

### 4.2 Читання

```python
build("sheets", "v4", credentials=..., cache_discovery=False, static_discovery=True)
```
* `static_discovery=True` — без мережевого запиту за discovery-документом на кожен старт воркера.
* **`valueRenderOption="UNFORMATTED_VALUE"`** — ціна приходить числом `27445`, а не рядком `"27 445 ₴"`.
  Прибирає 90% болю з українською локаллю.
* `dateTimeRenderOption="FORMATTED_STRING"` — інакше дати приходять serial-числами Lotus.
* Ліміт payload ~2 МБ, таймаут запиту 180 с → великий лист читаємо **чанками діапазонів через
  `values.batchGet`** (кілька діапазонів = **один** запит квоти). 10k рядків ≈ **1–3 HTTP-запити**.
* ⚠️ **Антипатерн:** `worksheet.row_values(i)` у циклі (gspread-стиль) — 1 HTTP-запит **на рядок**.

**Бібліотека:** `google-api-python-client` + `google-auth` (офіційні, живі).
**Не gspread** — репо офіційно шукає мейнтейнерів.

### 4.3 Квоти

| Метрика | Ліміт |
|---|---|
| Read requests / хв / проєкт | **300** |
| Read requests / хв / **юзер (SA)** / проєкт | **60** |
| Денний ліміт | **немає** |

Один прогін = **~3 запити**. При 4 прогонах/добу це **<0.05% квоти**. Refill — щохвилини;
перевищення → `429`. Ретраї — експоненційний backoff + jitter, 6 спроб, `RETRYABLE = {429, 500, 502, 503, 504}`.

### 4.4 ⚡ Інваріант повноти (критично)

```python
expected = spreadsheets.get(fields="sheets(properties(title,gridProperties(rowCount)))")
rows     = values.batchGet(...)          # чанки
assert len(rows) + 1 == expected, "Неповне читання"    # → SyncRun.FAILED, каталог НЕ чіпаємо
```
Див. SYNC.md крок 2. **Ніякого «читаємо, що встигли».**

---

## 5. ⚡ Claude API — ДВА ЗАСТОСУВАННЯ (НОВИЙ РОЗДІЛ)

> Ціни, ліміти кешу і дефолти `thinking` **звірені з офіційною докою 13.07.2026**, не з пам'яті.
> Детальний дизайн перекладача — `docs/research/TRANSLATION.md`.

Claude API у нас **не одна фіча, а дві** — з різними моделями, різними чергами і різними політиками:

| | (а) **Переклад UA→RU** (`i18n`) | (б) **ШІ-збір характеристик** (`aispecs`, етап 4) |
|---|---|---|
| Навіщо | Замовник вимагає RU-версію і «розумний перекладач, грамотніший за Google» (INPUTS §1) | Товари без характеристик; порожні `mpn` і габарити |
| Обсяг | ~10 000 товарів одноразово + ~300/міс | точково, за кнопкою в адмінці |
| Транспорт | **Batch API** (−50%) | Sync + **web search tool** |
| Пише в каталог | тільки після `approve()` | **ніколи автоматично** |
| Бюджет | `TRANSLATION_MONTHLY_BUDGET_USD` | `SiteSettings.ai_monthly_budget_usd` |

### 5.1 Вибір моделі і ціни (звірено 13.07.2026)

| Модель | ID | Контекст | Input | Output | **Batch in/out** | Min cache prefix |
|---|---|---|---|---|---|---|
| **Claude Opus 4.8** | `claude-opus-4-8` | 1M | $5 | $25 | **$2,50 / $12,50** | **4 096** |
| **Claude Sonnet 5** | `claude-sonnet-5` | 1M | **$2** ¹ | **$10** ¹ | **$1,00 / $5,00** ¹ | **не підтверджено** ² |
| Claude Haiku 4.5 | `claude-haiku-4-5` | 200K | $1 | $5 | $0,50 / $2,50 | 4 096 |

¹ **Інтро-ціна Sonnet 5 діє до 31.08.2026.** З 01.09.2026 → **$3 / $15** (batch $1,50 / $7,50) — **+50%**.
Це треба закласти у фін-модель, а не дізнатися з рахунку.
² Рядка для Sonnet 5 в офіційній таблиці мінімального кешованого префікса **немає**. Не приймати 1 024
як факт — заміряти емпірично (два однакові запити + `usage.cache_read_input_tokens` на другому).

**Рішення:**
* **`claude-sonnet-5` — основна модель перекладу** (описи, назви, SEO). Переклад — не задача на ризонінг;
  різниця з Opus на всьому каталозі — $52 проти $130.
* **`claude-opus-4-8` — точково**: (1) **словник характеристик** (~4 300 рядків, $0,65 — але цей текст
  реплікується у 300 000 місць каталогу, економити тут безглуздо); (2) **ретраї після FAILED і після
  відхилення модератором**; (3) **`aispecs`** (web search + структурований вивід).
* **`claude-haiku-4-5` — НЕ використовуємо.** Економія на всьому проєкті ~$20, а натомість: 200K контекст,
  слабша багатомовність, **той самий поріг кешу 4 096**, що й у Opus 4.8 (тобто жодної переваги по кешу),
  і **web search tool `_20260209` він не підтримує** (потрібен Opus 4.8 / Sonnet 5) — тобто для `aispecs`
  він не годиться взагалі.

### 5.2 (а) Переклад UA→RU

**🔴 ГОЛОВНА ІДЕЯ: перекладаємо СЛОВНИК, а не товари.**
`Attribute`, `AttributeOption`, `Unit` у нас **уже нормалізовані таблиці** (DATA_MODEL). Отже рядок
`Колір виробу → Чорний` перекладається **один раз**, а не в кожному з 10 000 товарів.

| | Наївно (в кожному товарі) | Словник (один раз) |
|---|---|---|
| Одиниць перекладу | 10 000 × 30 = **300 000** | **4 300** |
| Токенів | **12,0 M** | **0,086 M** |
| Вартість (Sonnet 5 + Batch) | **$36** | **$0,26** |
| Повторний реімпорт прайсу | **знову $36** | **≈ $0** |
| Консистентність | ❌ | ✅ **структурно гарантована** |

🔴 **Це не оптимізація, а єдино правильна архітектура.** У наївному підході один термін отримає різний
переклад у різних запитах (`Чорний` → `Черный` / `Чёрный`), **фасетний фільтр після цього розпадається**:
два різні рядки = два різні значення фасета = дубльовані чекбокси. Це прямий баг у продакшні.
**Бонус:** схвалений словник → **глосарій у system-блоці** → описи перекладаються тими самими термінами.

**Що обов'язково зробити в коді** (інакше мовчки платимо вдвічі):
1. ⚠️ **`thinking={"type": "disabled"}`** — на **Sonnet 5 adaptive thinking увімкнений ЗА ЗАМОВЧУВАННЯМ**,
   коли поле `thinking` не передане. Для перекладу це чистий спалений бюджет на output-токени.
   (На Opus 4.8 — навпаки: пропуск поля = **без** thinking.)
2. ⚠️ **Prefill (останнє assistant-повідомлення) повертає 400** на всіх наших моделях. JSON форматуємо
   **тільки через structured outputs** (`output_config.format` з `json_schema`), не через «поверни JSON, будь ласка».
3. ⚠️ **`budget_tokens` теж 400.** Глибина — тільки через `output_config.effort` (`low` для перекладу).

```python
resp = client.messages.create(
    model="claude-sonnet-5",                 # або "claude-opus-4-8" для словника/ретраїв
    max_tokens=16000,
    thinking={"type": "disabled"},           # ⚠️ інакше на Sonnet 5 платимо за thinking-токени
    output_config={"effort": "low",
                   "format": {"type": "json_schema", "schema": TRANSLATION_SCHEMA}},
    system=[
        {"type": "text", "text": TRANSLATOR_RULES},          # статичні правила, ~900 tok
        {"type": "text", "text": glossary_block,             # глосарій зі схвалених термінів
         "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ],
    messages=[{"role": "user",
               "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)}],
)
```

**Кешування — пастка, яка не дає помилки:**
* Кеш = **префіксний збіг**. Порядок рендеру `tools → system → messages`.
* 🔴 **System-блок мусить перевищувати 4 096 токенів**, бо словник і ретраї йдуть на **Opus 4.8**.
  Коротший префікс **мовчки не кешується** — `cache_creation_input_tokens = 0`, **без жодної помилки**.
  → віддаємо в system **повний** глосарій (усі схвалені терміни, ~6–8k tok), а не «топ».
* Множники: write 5m ×1,25 · **write 1h ×2,0** · **read ×0,1**. **Стакаються з Batch −50%.**
* `ttl: "1h"` для батчу **обов'язково** — при 5-хвилинному TTL кеш протухне між частинами батчу.
* 🔴 **Тихі інвалідатори:** `datetime.now()` у system, `json.dumps()` без `sort_keys=True`, ітерація по
  `set()`, глосарій без `.order_by("pk")`. Глосарій **версіонується** (`glossary_version`, регенерація
  раз на добу), а не оновлюється при кожному схваленні терміна.
* Контроль: `usage.cache_read_input_tokens > 0` на повторних запитах.

**Batch API:** −50% на input і output; до 100 000 запитів / 256 МБ на батч; типово < 1 год, максимум 24 год.
🔴 **Результати приходять у ДОВІЛЬНОМУ порядку** — ключувати **тільки за `custom_id`** (`f"te-{entry.id}"`),
**ніколи за індексом**. Sync API — лише для кнопки «Перекласти зараз» в адмінці.

**Валідатори (жорсткі, не поради) — жоден невалідований переклад не потрапляє навіть у чергу:**
* **бренди/моделі/числа:** мультимножина латинських токенів у джерелі й перекладі має **збігатися**
  (`Bosch PNK6B2P40R` фізично не потрапляє в модель — відрізається препроцесингом);
* **HTML:** сегментний переклад через DOM — розмітка **не проходить через модель узагалі**; інлайнові
  теги кодуються як `<0>…</0>`; `img@src`, R2-URL, порядок вузлів — **біт-у-біт ідентичні**;
* провал валідації → `FAILED` + один ретрай на Opus 4.8 + алерт.

**Переклад НЕ йде одразу в продакшн:** `TranslationEntry.target_text` (чернетка) vs
**`published_text`** (те, що бачить сайт). Тільки дія «Схвалити» переносить одне в інше. Джерело
змінилось → сайт далі віддає старий схвалений RU, новий машинний варіант чекає в черзі.

**Вартість:** первинний прогін 10 000 товарів — **≈ $52** (Batch + кеш), бюджет **$100–150**.
Щомісяця — **≈ $4,50**, бюджет **$10–30**. 83% рахунку — це **описи**, тому перед повним прогоном
**обов'язково**: `client.messages.count_tokens()` на 50 реальних описах → прогнати 200 товарів →
порахувати фактичний `usage` → і тільки тоді запускати каталог. **Ніякого `tiktoken`** — це чужий
токенізатор, він бреше на кирилиці.

### 5.3 (б) ШІ-збір характеристик (`aispecs`, етап 4)

```python
resp = client.messages.create(
    model="claude-opus-4-8",                 # web_search_20260209 потребує Opus 4.8 / Sonnet 5
    max_tokens=16000,
    thinking={"type": "adaptive"},           # тут ризонінг ПОТРІБЕН
    output_config={"effort": "high",
                   "format": {"type": "json_schema", "schema": SPECS_SCHEMA}},
    tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}],
    messages=[{"role": "user", "content": f"{product.brand} {product.name} {product.mpn}"}],
)
```
* ⚠️ **`web_search_20260209`** (з динамічною фільтрацією) — **не** старий `_20250305`. Працює на
  **Opus 4.8 / Sonnet 5**; на Haiku 4.5 — ні. Ще одна причина, чому Haiku тут не варіант.
* ⚠️ Помилки web search **не кидають виняток**: приходить HTTP 200 з блоком
  `web_search_tool_result`, у якого `content` — **об'єкт з `error_code`**, а не список. Перевіряти тип.
* ⚠️ Довгий server-tool хід може віддати **`stop_reason: "pause_turn"`** — треба **перевідправити**
  повідомлення, щоб сервер продовжив. Ліміт продовжень — 5, інакше нескінченний цикл.

**🔴 ШІ НІКОЛИ не пише в каталог автоматично (ADR).**
`SpecHarvestJob(status=NEEDS_REVIEW)` → адмін бачить diff «пропоновано / поточне» + `confidence` +
**`sources`** (посилання) → кнопка «Застосувати» → `ProductAttributeValue(source=ai)`.
**Чому:** ШІ **правдоподібно галюцинує** характеристики побутової техніки. Помилкова діагональ у
фільтрі = повернення товару; **помилкові габарити = недоплата за доставку на КОЖНОМУ замовленні** (§1.7).

⚡ ШІ пропонує **два поля, яких немає в прайсі і які найдорожче обходяться порожніми**:
**`mpn`** (Hotline `<code>`, §2.3) і **габарити упаковки** (калькулятор НП, §1.7).

### 5.4 Ліміти, помилки, ретраї

| Помилка | Ретрай? | Що робимо |
|---|---|---|
| `RateLimitError` (429) | **так** | SDK ретраїть сам (`max_retries=2`); читаємо хедер **`retry-after`** |
| `APIStatusError` ≥500, `overloaded_error` (529) | **так** | експоненційний backoff + jitter |
| `APIConnectionError` (мережа) | **так** | те саме |
| `BadRequestError` (400) | **НІ** | наш баг: prefill / `budget_tokens` / `temperature` → Sentry, задача `FAILED` |
| `AuthenticationError` (401) | **НІ** | ключ протух → алерт, черга стає |

* 🔴 **Ловити ланцюжок типізованих винятків**, від конкретного до базового
  (`RateLimitError` → `APIStatusError` → `APIConnectionError`), **а не один широкий `except`** —
  інакше втрачаємо різницю між «повторити» і «це наш баг».
  ⚠️ **Ніколи не матчити текст помилки рядком.**
* Черга **`ai`** (`-c 1`) — окремий воркер, щоб ШІ-задачі не блокували синк і платежі.
* **Бюджетний hard-cap** у `SiteSettings`: при перевищенні задачі **не ставляться**, банер в адмінці.
  Дешевше поставити запобіжник, ніж пояснювати рахунок.
* Дашборд-віджет: `pending` / `stale` / `failed` / витрати за поточний місяць (`SUM(cost_usd)`).

### 5.5 Зберігання ключа

* **`ANTHROPIC_API_KEY` — через `docker compose secrets:`** (файл), **не env-змінна** — рівно з тієї ж
  причини, що й `LIQPAY_PRIVATE_KEY` (§3.10): env світиться в `docker inspect`, `/proc/<pid>/environ`
  і breadcrumbs Sentry.
* 🔴 **Ключ живе ТІЛЬКИ на бекенді.** Ані Next.js, ані браузер його не бачать — жодного
  `NEXT_PUBLIC_ANTHROPIC_*`. Публічний ключ Claude API **не існує**: будь-який ключ на фронті = чужі
  люди генерують токени за наш рахунок.
* Ротація — раз на рік + негайно при підозрі. `gitleaks` у CI на кожен PR (§3.10).
* У `TranslationEntry` / `SpecHarvestJob` зберігаємо `engine_model`, `prompt_version`,
  `glossary_version`, `input_tokens`, `output_tokens`, `cost_usd` — **аудит витрат по кожному рядку**.
</content>
</invoke>
