# Complex — Архітектура v3

> Статус: **готова до кодування**. Версія 2 закрила всі 22 critical і всі 31 major прогалини,
> знайдені трьома незалежними критиками в архітектурі v1.
> **Версія 3 (13.07.2026)** вносить підтверджені замовником вхідні дані — див.
> [reference/INPUTS.md](./reference/INPUTS.md). Зведення змін v3 — у §7.
> Документи-супутники: [DATA_MODEL.md](./DATA_MODEL.md), [SYNC.md](./SYNC.md),
> [INTEGRATIONS.md](./INTEGRATIONS.md), [OPEN_QUESTIONS.md](./OPEN_QUESTIONS.md), [ROADMAP.md](./ROADMAP.md).
> Дослідження: [research/DESIGN_SYSTEM.md](./research/DESIGN_SYSTEM.md),
> [research/TRANSLATION.md](./research/TRANSLATION.md), [research/LIQPAY.md](./research/LIQPAY.md),
> [research/HOTLINE.md](./research/HOTLINE.md).

---

## 0. Що це за система

Інтернет-магазин побутової техніки (переписуємо з нуля; `galiton.com.ua` — референс попередника). Ключові властивості:

* Каталог живе **не в адмінці, а в Google Sheets** — прайс редагують люди, синк 4×/добу.
* **Особистого кабінету немає.** Кошик/порівняння/бажання — на клієнті. Замовлення — гостьове.
* ⚡ **Дві мови в MVP: UA (дефолт) + RU.** i18n наскрізний: моделі, проєкції, пошук, кеш, маршрути, sitemap.
  Переклад — **машинний через Claude API з ручним схваленням** (ADR-024, ADR-027).
* Два прайси-листи (UAH, USD), курс USD — в адмінці (у таблиці він теж є — комірка `E4`).
* Обов'язкові інтеграції: Нова Пошта (довідники + калькулятор), **LiqPay** (ADR-025),
  фід Hotline у форматі **XML** (ADR-026), Claude API (характеристики + переклад).
* ⚡ **Новий домен і новий сервер** (не `galiton`). Старі URL **не мігруємо як є**;
  потреба в 301-редіректах зі старого домену — **відкрите питання** (див. §6, ADR-002).
* ⚡ **Дизайн — власний**: сіро-чорна нейтральна тема, референс — Ябко (`jabko.ua`).
  Див. §4 і [research/DESIGN_SYSTEM.md](./research/DESIGN_SYSTEM.md).
* Один VPS. Отже: **усе, що можна не додавати як сервіс — не додаємо** (пошук — Postgres FTS, черги — Redis+Celery).

### Стек (зафіксовано)

| Шар | Технологія |
|---|---|
| API | Django 5.1 + Django Ninja (Pydantic v2), Python 3.12, `uv` |
| БД | PostgreSQL 14 (локально) / 16 (прод) |
| Черги | Celery 5 + Redis (broker db0, cache db1) |
| Адмінка | Django Admin + django-unfold + django-otp + django-axes |
| Фронт | Next.js App Router + TypeScript + Tailwind + shadcn/ui |
| Файли | Cloudflare R2 (S3-сумісний), окремий бакет для бекапів |
| Деплой | Docker Compose на VPS + Caddy + GitHub Actions (GHCR, теги = git SHA) |
| Бекапи | pgBackRest (base + WAL) → окремий R2-бакет з окремим токеном |
| Моніторинг | Sentry + Healthchecks.io (dead-man's switch) + Uptime probe + Telegram-алерти |

> ⚠️ **PostgreSQL 14 локально / 16 на проді — це ризик.** Ми свідомо не використовуємо нічого,
> чого немає в 14 (`GeneratedField` — з 12, `nulls_distinct` — з 15 ❌).
> Див. ADR-016: **`nulls_distinct=False` НЕ використовуємо**, замість нього — два часткові unique-індекси.
> Ціль: вирівняти локальну БД на 16 (задача `INFRA-02`).

---

## 1. Компоненти

```
                    ┌──────────────────────────────────────────────┐
   Покупець ───────▶│  Next.js (SSR/ISR)   revalidate = 600 s      │
                    │  /[locale]/…  (ua | ru)                      │
                    │  cart/compare/wishlist → localStorage        │
                    └───────┬──────────────────────────────▲───────┘
                            │ REST (Ninja, OpenAPI→TS)     │ on-demand revalidate (теги)
                    ┌───────▼──────────────────────────────┴───────┐
   Менеджер ───────▶│  Django (gunicorn ×2 репліки за Caddy)       │
   /_adm_xxxx/      │  Admin (unfold + OTP + axes)  |  Ninja API   │
                    └───┬───────────┬───────────┬──────────┬───────┘
                        │           │           │          │
              ┌─────────▼──┐  ┌─────▼─────┐ ┌───▼────┐ ┌───▼──────────┐
              │ PostgreSQL │  │   Redis   │ │   R2   │ │ Зовнішні API │
              │  (істина)  │  │ broker/   │ │ media  │ │ Sheets · НП  │
              │            │  │ cache/    │ │ feeds  │ │LiqPay·Claude │
              └─────▲──────┘  │ breaker   │ └────────┘ └───▲──────────┘
                    │         └─────▲─────┘                │
              ┌─────┴───────────────┴────────────────────  ┴─────────┐
              │  Celery workers:  sync(-c1) · images(-c2) ·          │
              │                   default(-c4) · ai(-c1, specs+i18n) │
              │  Celery beat (DatabaseScheduler, Europe/Kyiv)        │
              └──────────────────────────────────────────────────────┘
```

### Django-застосунки

| App | Відповідальність |
|---|---|
| `core` | TimeStampedModel, SEOMixin, SingletonModel, advisory-locks, circuit breaker, uk-стемер, санітайзер HTML |
| `catalog` | Category, Brand, Country, Product, ProductImage, EAV-атрибути, PriceHistory, денорм (×2 мови), фасети, пошук |
| `sync` | PriceSource, PriceSheet, SpecSheet, **SupplierOffer**, SyncRun, SyncLogEntry, ProductPriceSnapshot |
| `orders` | Order, OrderItem, OrderStatusHistory, checkout-сервіс |
| `payments` | Payment, PaymentWebhookEvent, провайдери (**liqpay** — активний; Protocol під інших), звірка |
| `delivery` | NPArea/NPCity/NPSettlement/NPWarehouse, quote-сервіс, circuit breaker |
| `cms` | Banner, NewsPost, StaticPage, MenuItem, PickupPoint |
| `siteconfig` | SiteSettings (singleton), WorkingHours |
| `feeds` | FeedArtifact, генератор **Hotline XML** |
| `aispecs` | SpecHarvestJob (Claude + web search), ручне схвалення |
| ⚡ `i18n` | **TranslationEntry** (словник UA→RU), GlossaryTerm, батчер Claude, черга схвалення в адмінці |
| `integrations/` | Чисті HTTP-клієнти (Sheets, НП, LiqPay, Claude). **Нуль Django-моделей, нуль ORM.** |

---

## 2. Потоки даних

### 2.1 Синхронізація прайсу (серце системи)

```
Google Sheets (UAH lист + USD лист)
   │  ① spreadsheets.get → rowCount        [жорстка звірка повноти]
   │  ② values.batchGet чанками 5000       [FAILED, якщо len(rows) != rowCount]
   ▼
parse_rows()  → PriceRow[]                 [валідація рядка ДО БД; битий рядок = skip+ERROR]
   │  ③ дедуп sku на рівні ВСЬОГО прогону  [ON CONFLICT не може двічі зачепити рядок]
   ▼
guard #1: рядків < 50% живих офферів?      → ABORTED
guard #2: >20% деактивацій / медіанна зміна ціни >30%? → ABORTED (з кнопкою «Застосувати попри»)
   ▼
┌─ transaction.atomic ───────────────────────────────────────────────┐
│  ④ ProductPriceSnapshot: COPY поточних цін/наявності (для відкату) │
│  ⑤ SupplierOffer upsert  ← ЛИШЕ в межах свого price_source         │
│  ⑥ Деактивація зниклих офферів ← ЛИШЕ в межах свого price_source   │
│  ⑦ project_offers(): вибір переможця (priority) → UPDATE Product   │
│     price рахує SQL-функція complex_price_uah() — єдине джерело формули│
│     PG-тригер price_history_trg пише PriceHistory на КОЖНУ зміну    │
└────────────────────────────────────────────────────────────────────┘
   ▼ on_commit (+ heal-задачі як страховка, бо on_commit губиться)
fill_specs · download_images · rebuild_denorm · refresh_counts · revalidate
```

**Ключова зміна v2: `SupplierOffer`.** Синк ніколи не пише в `Product` напряму.
Він пише в `SupplierOffer(price_source, sku)` — свою пісочницю, — а окремий крок-проєкція
переносить у `Product` дані **переможця за `priority`**. Це одним рухом закриває:
мультипостачальників, взаємну деактивацію каталогів, «низькопріоритетний прайс перезаписав ціну Complex»,
і робить `price_locked`/`availability_locked` тривіальними (це просто `WHERE NOT locked` у проєкції).

### 2.2 Читання каталогу

```
GET /api/catalog/products?lang=ru&category=5609730&brand=bosch&brand=gorenje&obiem=200-299&sort=price
   │
   ├─ фільтрація:  filter_tokens && ARRAY['brand:bosch','brand:gorenje']   (OR у групі)
   │               AND filter_tokens && ARRAY['obiem:200-299']             (AND між групами)
   │               AND price BETWEEN … (btree)   AND is_active AND category.is_active
   │               filter_tokens — МОВОНЕЗАЛЕЖНІ (slug-и), спільні для UA і RU
   │
   ├─ фасети:      ОДИН запит SELECT t, count(*) FROM product, unnest(filter_tokens) t
   │               WHERE <усі фільтри, крім поточної групи>  GROUP BY t
   │               → counts і результати ГАРАНТОВАНО з одного джерела
   │               підписи фасетів беруться з Attribute.name_{lang} (кеш, TTL 1 год)
   │
   ├─ пошук:       search_vector_{lang}  (uk: TS-конфіг `uk` + застосунковий стемер;
   │               ru: вбудований `russian`)  + pg_trgm по name_{lang}
   │
   └─ картка:      specs_json_{lang} (1 рядок, нуль JOIN)
```

⚡ **Мова наскрізна.** `lang ∈ {uk, ru}` (у URL — `ua`/`ru`) входить у **ключ кожного кешу**:
Redis-ключі каталогу/дерева/фасетів, Next.js ISR-теги (`catalog:ru`, `category:5609730:ru`,
`product:{id}:ru`), `Vary` не використовуємо — мова завжди в шляху, не в заголовку.

### 2.3 Checkout

```
Кошик (localStorage)
  → POST /api/cart/preview [{id, qty}]  → актуальні ціни, availability, installment_allowed
  → POST /api/orders  {items, expected_total, idempotency_key, np_refs…}
       ├─ сервер перераховує total з БД
       ├─ total != expected_total  →  409 {changed_items}  (фронт: «ціни оновились»)
       ├─ Order.number ← nextval('order_number_seq')       (нуль гонок)
       └─ Order.public_token ← uuid4                       (нуль IDOR)
  → якщо online: Payment → provider.create_invoice() → redirect на pageUrl
  → вебхук провайдера (ідемпотентний, стан-машина, звірка суми)
  → beat: reconcile_pending_payments кожні 5 хв (вебхук міг не дійти)
```

### 2.4 ⚡ Переклад UA→RU (новий потік)

Деталі, промпти й розрахунок вартості — [research/TRANSLATION.md](./research/TRANSLATION.md).

```
Синк / ручна правка → зміна українського тексту
   │  джерела: Category.name, Attribute.name, AttributeOption.value,
   │           Product.name, Product.description (rich HTML)
   ▼
collect_translatable()  → нормалізація + sha256(source)
   │  ① перевіряємо TranslationEntry по (kind, source_hash)
   │     HIT  → готово, нуль звернень до API
   │     MISS → у чергу
   ▼
translate_batch  (Celery, черга `ai`, -c1)
   │  ② словниковий рівень (ADR-027): перекладаємо УНІКАЛЬНІ рядки
   │     (назви характеристик, значення-опції, назви категорій), а НЕ товари
   │  ③ описи — сегментний переклад через DOM: розмітка й <img> НЕ йдуть у модель
   │  ④ назви товарів: бренд і модель НЕ перекладаються (маскуються)
   │  ⑤ Claude API: system(правила) + system(глосарій, prompt cache) + user(батч)
   ▼
TranslationEntry(status=PENDING)  → адмінка: черга схвалення
   │  APPROVED вручну (або авто — для kind з довіреного списку)
   ▼
apply_translations()  → *_ru поля моделей
   ▼
rebuild_denorm(lang='ru') → specs_json_ru, search_vector_ru
   ▼
revalidateTag('catalog:ru', 'product:{id}:ru')
```

**Гарантії.** Джерело істини — **українське поле**. `*_ru` — похідне.
`TranslationEntry.source_hash` ловить зміну оригіналу: змінився UA → RU-запис стає `STALE`,
на сайті **до схвалення** діє fallback на UA (порожній `*_ru` ніколи не показується як порожнеча).
Ключ кешу перекладу — `(kind, source_hash)`, тому та сама характеристика в 5 000 товарів
перекладається **один раз**.

---

## 3. ADR — ключові рішення

Формат: **Рішення / Обґрунтування / Що відкинуто**.
Позначка ⚡ = змінилось у v2, 🆕 = додано або переглянуто у **v3** (за вхідними даними замовника).

### ADR-001. Дерево категорій — self-FK + матеріалізований `path`
**Рішення.** `parent` (self-FK) + `path` (матеріалізований шлях з `external_id`, напр. `87727179/5609730`) + `depth`. Без MPTT/treebeard/ltree.
**Обґрунтування.** Дерево — 59 вузлів глибиною 2, змінюється раз на місяць. `path__startswith` дає всіх нащадків одним індексованим запитом — і саме на цьому будується масове вмикання Hotline на категорію. Все дерево кешується в Redis і віддається фронту цілком.
**Відкинуто.** MPTT (важкі оновлення lft/rght, `rebuild()`, конфлікт `TreeAdmin` з unfold), treebeard (те саме з адмінкою), `ltree` (нестандартний тип, ламає portable-міграції).

### ADR-002. 🆕 URL-схема `/{locale}/c/{ext_id}/{slug}`, `/{locale}/p/{id}/{slug}` — на НОВОМУ домені
**Рішення.** Схема шляхів лишається ID-канонічною: ID канонічний, slug декоративний; невірний slug → **301** на канонічний (старий сайт віддавав 200 → нескінченний дубль-контент). `locale ∈ {ua, ru}`, **сегмент завжди явний** (без «голого» кореня без локалі), `/` → 302 на `/ua/` за `Accept-Language`.
⚡ **v2:** магічної константи `RESTART WITH 3000000000` більше немає. Після імпорту легасі — `SELECT setval('catalog_product_id_seq', (SELECT COALESCE(MAX(id),0)+1 FROM catalog_product), false)` окремим кроком management-команди. Це коректно за будь-яких legacy-ID (10-значні Prom-ID цілком можуть бути > 3e9).
🆕 **v3 — обґрунтування змінилось, рішення лишилось.** Замовник підтвердив: **новий сервер, новий хостинг, нова назва домену** (не `galiton`). Отже:
* Аргумент «зберігаємо URL → зберігаємо SEO-вагу **без редіректів**» **більше не діє**: домен інший, будь-який старий бекlink усе одно проходить через редірект.
* Схему все одно лишаємо, бо `external_id` категорії (`c50549829`) — це **ключ мапінгу з прайсу**, він потрібен синку незалежно від URL, а числовий ID у шляху дає стабільний канонічний URL без ризику дублів при перейменуванні товару.
* **Старі URL не мігруємо як є.** Питання 301-редіректів `galiton.com.ua/*` → новий домен — **відкрите** (див. §6, п.1). Якщо замовник підтвердить, що стара вага цінна, — це **окремий, дешевий модуль**: `RedirectRule(old_path, new_path, code)` + мапа `legacy_id → product_id`. Архітектуру він не змінює, тому чекати відповіді не треба.
**Відкинуто.** (а) Slug-only URL (`/ua/p/xolodylnyk-gorenje-nrk6202`) — slug мовозалежний і змінюваний; при перейменуванні або зміні мови URL «пливе», потрібна таблиця історії slug-ів. (б) Перенести старі URL 1:1 на новий домен — це вимагало б тягнути легасі-структуру категорій, від якої ми і йдемо.

### ADR-003. 🆕 i18n — django-modeltranslation, обидві мови ЖИВІ в MVP
**Рішення.** Колонки `name_uk`/`name_ru` у тій самій таблиці. У URL локаль — `ua`/`ru`; у Django — `uk`/`ru` (мапінг у middleware, `ua` → `uk`).
⚡ **v2:** перекладними стають і **денормалізовані проєкції**: `specs_json_uk/_ru`, `search_vector_uk/_ru`, `og_image` та trigram-індекси по `name_uk` **і** `name_ru`.
🆕 **v3: RU — не «схема на майбутнє», а робоча мова з першого дня** (див. ADR-024). Наслідки, які треба закласти **до першої міграції й до першого рядка фронту**:

| Шар | Що конкретно |
|---|---|
| **Маршрути Next.js** | `app/[locale]/…` — сегмент **обов'язковий**. `generateStaticParams` повертає обидві локалі. `locale` пробрасується в кожен fetch до API (`?lang=`) |
| **Кеш** | `locale` — **частина ключа** скрізь: Redis (`cat:tree:{lang}`, `facets:{cat}:{lang}`), ISR-теги (`catalog:{lang}`, `product:{id}:{lang}`). Ревалідація після перекладу б'є **тільки теги тієї мови** |
| **Sitemap** | **Два набори URL** + `<xhtml:link rel="alternate" hreflang="uk-UA">` / `hreflang="ru-UA">` / `hreflang="x-default"` (→ UA) на **кожному** `<url>`. Взаємність обов'язкова: якщо `ua` посилається на `ru`, `ru` мусить посилатись на `ua`, інакше Google ігнорує всю групу |
| **`<head>`** | `<link rel="alternate" hreflang>` (та сама група), `<link rel="canonical">` — **на свою ж мову**, не на UA. `<html lang>` = поточна мова |
| **Проєкції** | `specs_json_ru` і `search_vector_ru` наповнюються **тим самим** `rebuild_denorm`, але з RU-полів. `denorm_dirty` — один прапорець на товар, перебудова робить обидві мови за прохід |
| **Токени фільтрів** | `filter_tokens` — **мовонезалежні** (slug-и: `brand:gorenje`, `obiem:200-299`). Мова живе **тільки в підписах** (`Attribute.name_ru`, `AttributeOption.value_ru`). Інакше фасети довелося б рахувати двічі, а `&&`-запит роздвоївся б |
| **Fallback** | Порожній `*_ru` → показуємо UA. Порожнеча користувачу **не показується ніколи** |
| **Адмінка** | Кожне перекладне поле — дві колонки (unfold-таби `UA` / `RU`) + кнопка «Перекласти» (ставить задачу) + бейдж статусу перекладу |
| **Замовлення / чекаут** | Мова **не впливає** на дані замовлення. `Order.locale` зберігаємо **тільки** щоб надіслати лист і SMS тією мовою, якою людина купувала |

**Обґрунтування.** Замовник підтвердив RU як вимогу, а не як опцію. Різниця «RU готове схемою» vs «RU працює» — це не колонки в БД (вони вже були), а **маршрути, ключі кешу і hreflang**. Якщо їх не закласти зараз, RU-запуск = переписування фронту й інвалідація всього кешу.
**Відкинуто.** django-parler (JOIN на кожен запит каталогу, біль з ORDER BY і фасетами), JSONB `{uk:…, ru:…}` (не індексується під FTS/trigram, не редагується як поле в адмінці), окремий піддомен `ru.` (другий сертифікат, другий кеш, розмиття ваги домену).

### ADR-004. ⚡ SupplierOffer — проміжна таблиця пропозицій
**Рішення.** Синк пише **виключно** в `SupplierOffer(price_source, sku)` — unique `(price_source, sku)`. Деактивація зниклих — теж **тільки в межах свого `price_source`**. Окремий крок `project_offers()` вибирає переможця (`ORDER BY price_source.priority, is_primary DESC, offer.id`) і переносить дані в `Product`.
**Обґрунтування (це був critical #1).** У v1 `sku` глобально унікальний на `Product`, а `price_source`/`last_seen_run` — одиничні колонки. Наслідок: (а) прогін стороннього постачальника перезаписував ціну товару зі складу Complex (прямі збитки), (б) після перезапису `price_source` прогін Complex робив `filter(price_source=complex)` і цього товару **вже не бачив** → він ніколи не деактивувався, а прогін постачальника натомість деактивував товари Complex. `PriceSource.priority` був чистою фікцією — `ON CONFLICT DO UPDATE` не має жодної перевірки пріоритету, вигравав той, хто синкнувся останнім.
**Відкинуто.** (а) Raw `INSERT … ON CONFLICT … WHERE EXCLUDED.priority <= product.owner_priority` — атомарно, але не вирішує проблему деактивації (кожен прайс усе одно бачить лише «свої» товари через одиничну колонку). (б) Чесно прибрати `priority` і задекларувати «один SKU = один прайс» — суперечить ТЗ («закласти прайси інших постачальників»).

### ADR-005. ⚡ Ціна рахується ОДНІЄЮ SQL-функцією `complex_price_uah()`
**Рішення.** Формула ціни існує рівно в одному місці — **immutable PL/pgSQL функція**, створена міграцією:

```sql
complex_price_uah(base numeric, currency text, rate numeric, markup numeric, rule text) → numeric
-- currency='USD' → base * rate * (1 + markup/100), інакше base * (1 + markup/100)
-- rule: none | unit | ten | nine
```

Її викликають **обидва** місця, де ціна змінюється: `project_offers()` (синк) і `recalc_prices()` (зміна курсу/округлення/націнки). Python-двійник `pricing.compute_uah_price()` існує **лише для preview/адмінки** і покритий обов'язковим parity-fuzz-тестом на сітці входів.
⚡ Плюс `Product.markup_percent` — **денормалізована** ефективна націнка (пишеться проєкцією з `PriceSource`, для ручних товарів = 0).
**Обґрунтування (critical).** У v1 було дві розбіжні реалізації: синк рахував `base*rate*(1+markup)`, а `recalc_usd_prices` — `base*rate` **без markup**. Перша ж зміна курсу стирала націнку на всьому каталозі (для постачальника з 15% — миттєва втрата маржі, і ніхто не помітить, бо «ціни просто трохи впали»). Плюс `F()`-вираз фізично не може дотягнутись до `price_source__price_markup_percent` в одному `UPDATE`.
**Відкинуто.** Python як єдине джерело + `qs.update(F(...))` — це неминуче два різні вирази (Decimal-логіка ≠ SQL-вираз) і неминучий дрейф. SQL як єдине джерело прибирає можливість дрейфу за побудовою.

### ADR-006. ⚡ Історія цін — PostgreSQL-тригер, а не Python
**Рішення.** `catalog_pricehistory` (append-only) наповнюється тригером
`AFTER INSERT OR UPDATE ON catalog_product FOR EACH ROW WHEN (OLD.price IS DISTINCT FROM NEW.price)`.
Контекст (`reason`, `run_id`, `user_id`) передається через `SET LOCAL complex.price_reason = 'sync'` на початку транзакції; тригер читає `current_setting('complex.price_reason', true)`.
**Обґрунтування (critical).** Історії цін не було взагалі. Єдиний слід — `SyncLogEntry(PRICE_CHANGED)`, який (а) чиститься через 90 днів, (б) **не пишеться при `recalc`** — там один масовий `UPDATE` без логування по товарах. Питання «чому вчора холодильник коштував 27 445, а сьогодні 28 900» було без відповіді. Тригер покриває і `bulk_create(update_conflicts)`, і `qs.update()`, і ручну правку в адмінці — **без жодного шансу забути**.
**Відкинуто.** Логування в Python — масовий `UPDATE` його обходить за визначенням. `django-simple-history` — важкий (дублює всю таблицю), нам треба 6 колонок.

### ADR-007. ⚡ Range-фільтри — бакет-токени, а НЕ `GIN(jsonb_path_ops)`
**Рішення.**
1. **Дискретні діапазони (дефолт, `filter_widget=range_buckets`)** — дискретизуються в токени: `obiem:200-299` → лягають у той самий `filter_tokens` (ArrayField + GIN). Межі бакетів — `Attribute.range_buckets` (JSON, редагується в адмінці; є management-команда автогенерації по перцентилях).
2. **Неперервний слайдер (`filter_widget=range_slider`, escape hatch)** — значення живе в `specs_num` (JSONB, payload), а під нього створюється **B-tree expression-індекс** management-командою `sync_range_indexes`:
   `CREATE INDEX CONCURRENTLY prod_num_obiem ON catalog_product (((specs_num->>'obiem')::numeric)) WHERE specs_num ? 'obiem';`
3. `specs_num` **більше не має GIN-індексу** і **не є механізмом фільтрації** за замовчуванням.

**Обґрунтування (critical).** Заявлене в v1 `specs_num @@ '$.obiem >= 200 && $.obiem <= 399'` по `GIN(jsonb_path_ops)` **фізично не працює**: PostgreSQL витягує з jsonpath для GIN лише клаузи виду `accessors_chain = constant`. Предикати `>=`, `<=`, `>`, `<` **не витягуються взагалі** → нуль quals → Seq Scan з детоастингом jsonb на кожному рядку. Тобто найчастіший фільтр у побутовій техніці не мав індексу.
Бакет-токени обрані як **дефолт**, бо (а) старий сайт уже так і робив (`data-range="200|399"` — фіксовані діапазони, не слайдер), (б) вони дають **єдине джерело для фільтрації і для лічильників фасетів** (див. ADR-008), (в) один GIN-індекс обслуговує всі фільтри одразу.
**Відкинуто.**
* `EXISTS` по EAV з індексом `(attribute_id, value_number, product_id)` — працює і швидко, але **не може живити уніфікований запит лічильників** (фасети рахувалися б з іншого джерела, ніж фільтрація → «Bosch (12)», клік → 9 товарів). Лишається як довідковий шлях для адмінських звітів.
* GIN(jsonb_path_ops) для нерівностей — **міф, викреслено з архітектури**.
* Слайдер по кожному числовому атрибуту — вимагав би десятків expression-індексів на найтовщій таблиці.

### ADR-008. ⚡ Фасети і фільтрація — з ОДНОГО джерела, мультивибір через `&&`
**Рішення.** Один запит на всі групи:
```sql
SELECT t.token, count(*) FROM catalog_product p, unnest(p.filter_tokens) AS t(token)
WHERE <усі активні фільтри, КРІМ поточної групи (drop-self)> GROUP BY t.token;
```
Фільтрація — `filter_tokens && ARRAY[…]` (**overlap**, тобто OR) **на кожну групу окремо**; між групами — AND.
«Системні» фасети (бренд, наявність, `condition`, ціна) — теж токени (`brand:gorenje`, `avail:in_stock`, `cond:2`), тому потрапляють у той самий запит; для ціни додатково `Min/Max` агрегат на тому ж qs.
EAV лишається **виключно джерелом метаданих фасета** (назва, порядок, віджет, одиниця) — вони змінюються раз на тиждень і кешуються надовго.
**Обґрунтування (2 gaps).** (1) v1 заявляв «5 фільтрів = один `filter_tokens @> ARRAY[…]`». `@>` — це **AND по всіх елементах**: `@> ['brand:bosch','brand:gorenje']` шукає товар, який є І Bosch, І Gorenje → **нуль результатів**. Мультивибір у фасетах не працював би зовсім. (2) v1 фільтрував по денорму, а counts рахував по EAV — два джерела, які розходяться на час асинхронного `rebuild_denorm`.
**Відкинуто.** Counts з EAV (N важких JOIN-запитів + комбінаторний вибух Redis-кешу по комбінаціях фільтрів). Матеріалізовані фасети (передчасно на 5–10k товарів).

### ADR-009. ⚡ Повнотекстовий пошук українською
**Рішення (три шари, усі в v1-міграціях):**
1. **TS-конфіг `uk`**, створений міграцією: `CREATE TEXT SEARCH CONFIGURATION uk (COPY = simple); ALTER … ALTER MAPPING FOR hword, hword_part, word WITH unaccent, simple;` — нормалізація без стемінгу.
2. **Стемер у застосунку** — `core/text/uk_stem.py` (~40 правил відсікання закінчень + словник винятків), застосовується **симетрично**: при побудові `search_vector_uk` (текст стемиться в Python **до** `to_tsvector('uk', …)`) і при парсингу запиту. Це дає реальний стемінг («холодильники» знаходить «Холодильник Gorenje») **без hunspell і без власного образу Postgres**.
3. **pg_trgm fallback** — `similarity(name_uk, query) > 0.3` для опечаток і коли FTS дав 0 результатів.
RU: `search_vector_ru` використовує **вбудований `russian`** (snowball у Postgres є з коробки).
**Обґрунтування (critical).** У PostgreSQL **немає** вбудованої конфігурації для української. `to_tsvector('ukrainian', …)` кине помилку, `simple` не стемить взагалі. У v1 конфігурація FTS не була зафіксована ніде — тобто основний сценарій пошуку в магазині був невизначений.
**Відкинуто (свідомо, з планом B).** Hunspell-словник `uk_UA` (`CREATE TEXT SEARCH DICTIONARY … TEMPLATE=ispell`) — дає найкращу якість, але **вимагає власного Docker-образу Postgres** (словники в `$SHAREDIR/tsearch_data`), що тягне за собою власний білд БД, ускладнює managed-Postgres у майбутньому і restore. Тримаємо як **escape hatch на Етапі 4**, якщо якість пошуку не влаштує (метрика: частка пошуків з 0 результатів > 15%). Elasticsearch/Meilisearch — окремий сервіс і окрема пам'ять на одному VPS заради каталогу на 5–10k товарів.

### ADR-010. ⚡ Замки — PostgreSQL advisory-lock, а не Redis
**Рішення.** `pg_try_advisory_xact_lock(hashtext('sync:prices:' || code))`.
**Обґрунтування (critical).** Наївний Redis-lock у v1: (а) `DEL` без fencing-токена — прогін, що пережив TTL, видаляв **чужий** лок і дозволяв два одночасні синки; (б) при OOM-kill лок висів до кінця TTL (30 хв) і блокував кнопку «Оновити зараз»; (в) `FLUSHALL` кешу зносив і локи. Advisory-lock **звільняється автоматично при обриві з'єднання**, живе в тій самій БД, що й дані, і не залежить від Redis взагалі.
Додатково: beat-задача `reap_stale_runs` (SyncRun у `RUNNING` старше `time_limit + 5хв` → `FAILED`, traceback `reaped`, алерт) і кнопка «Зняти лок» в адмінці.
**Відкинуто.** Redis SET NX PX + Lua-release — робоче, але лишає SPOF і не рятує від OOM-kill без reaper. Advisory-lock кращий по всіх осях.

### ADR-011. ⚡ Самозагоювання замість надії на `on_commit`
**Рішення.** Кожна асинхронна робота має **персистентний маркер** і **beat-задачу, що добирає відстале**:

| Маркер | Heal-задача | Період |
|---|---|---|
| `Product.denorm_dirty` (bool) + `denorm_at` (dt) | `heal_denorm` — `denorm_dirty=True OR denorm_at < updated_at` | 5 хв |
| `ProductImage.downloaded_at IS NULL, failed_reason=''` | `heal_images` — старші 1 год → назад у чергу | 1 год |
| `Product.usd_rate_used != SiteSettings.usd_rate` | `heal_usd_prices` | 1 год |
| `Payment.status IN (created,pending)` | `reconcile_pending_payments` | 5 хв |
| `SyncRun.status = RUNNING` (протух) | `reap_stale_runs` | 10 хв |
| `FeedArtifact.is_current` старший 24 год / файл зник | `heal_hotline_feed` | 1 год |

**Обґрунтування (critical).** Уся важка робота вішалась через `transaction.on_commit`. Якщо процес помирає **між COMMIT і `.delay()`** — ціни закомічені, задачі не поставлені, **назавжди**. Товар є в БД, але `specs_json`/`filter_tokens`/`main_image_url` порожні → він не знаходиться фільтрами і йде у фід без фото. І про це ніхто не дізнається. `on_commit` лишається як **швидкий шлях**, heal — як **гарантія**.
**Відкинуто.** Transactional outbox (таблиця подій + релей) — правильно, але надлишково: heal-задачі дають ту саму гарантію за 6 рядків коду кожна, бо всі наші задачі ідемпотентні і мають природний «брудний» маркер.

### ADR-012. ⚡ Відкат помилкової синхронізації + другий guard
**Рішення.**
1. **Guard #1 (з v1)** — кількість рядків < `guard_min_rows_ratio` × живих офферів → `ABORTED`.
2. ⚡ **Guard #2 — пороги масової зміни, ДО запису:** прогін деактивує > 20% живих товарів джерела **АБО** медіанна зміна ціни > 30% **АБО** > 20% рядків мають `qty<=0` при попередньому `qty>0` → `ABORTED` + алерт + кнопка «Застосувати попри попередження» (та сама задача з `force=True`, обов'язково з `started_by`).
3. ⚡ **`ProductPriceSnapshot(run, product, old_*)`** — COPY поточних цін/наявності **перед** проєкцією. Ретеншн 30 днів.
4. ⚡ **Дія «Відкотити прогін»** у `SyncRunAdmin`: `UPDATE` назад **лише для товарів, у яких `synced_at` досі == цього run** (тобто їх ніхто не змінював після).
5. ⚡ **Сирий знімок прочитаних рядків** (gzip CSV, ~1 МБ) у R2 під ключем `run_id` — форензика «що було в таблиці о 12:00» + можливість переграти прогін офлайн.
**Обґрунтування (critical).** Guard #1 ловить **лише зменшення кількості рядків**. Найімовірніший сценарій — «Артур обнулив/зсунув колонку К-сть» — проходить його **наскрізь**: рядків стільки ж, guard мовчить, 10k товарів стають `OUT_OF_STOCK`. Відкоту не було взагалі, а restore БД з бекапу вбив би замовлення, прийняті після синку.
**Відкинуто.** Soft-delete з таймером (деактивувати те, що зникло 2 прогони поспіль) — маскує проблему замість того, щоб про неї кричати; можна додати третім шаром пізніше.

### ADR-013. ⚡ Hotline — категорія як джерело дефолту, `<code>` опційний
> Формат фіда (XML) — див. **ADR-026**. Гарантія: 🆕 замовник підтвердив тип **«від виробника»**,
> строк тягнемо з характеристики `Гарантійний термін` (`12 міс`) → `<warranty>`.

**Рішення.**
* ⚡ `Category.hotline_enabled_default` (bool) — успадковується новим товаром **при створенні** (у INSERT-частині, але **не** в `update_fields` — щоб не затирати ручне вимкнення).
* ⚡ Celery-задача `set_hotline_for_category(category_id, value, include_descendants=True)` → `Product.objects.filter(category__path__startswith=cat.path).update(hotline_enabled=value)` — саме тому нам і потрібен матеріалізований `path`. В адмінці — `actions_row` у `CategoryAdmin`, запуск асинхронний (10k UPDATE не має жити в HTTP-запиті), з аудитом.
* ⚡ **`<code>` (mpn) — опційний**, а НЕ умова скіпу. Правила скіпу: немає `Category.hotline_category_name`, немає ціни, немає фото, `availability=out_of_stock`, `category.is_active=False`.
**Обґрунтування (2 critical).** (1) У v1 масове вмикання Hotline на категорію не мало ні моделі, ні механізму, а новий товар створювався з `hotline_enabled=False` → категорія «протікала» на кожному прогоні синку, тобто вимога ТЗ не виконувалась навіть після ручного вмикання. (2) `<code>` = `Product.mpn`, а `mpn` **нічим не заповнюється** (у прайсі колонки немає) → `skipped_reasons: no_mpn` на 100% товарів → **фід був би порожній**. За специфікацією Hotline `<code>` — умовно обов'язковий, тобто фід без нього валідний (просто гірше мержиться з картками). Паралельно: `mpn` додається як опційна колонка в `column_map`, як поле в `aispecs`, і в адмінці є фільтр «hotline_enabled=True, mpn порожній».

### ADR-014. ⚡ Order.public_token — закриття IDOR
**Рішення.** `Order.public_token = UUIDField(default=uuid4, unique=True)`. Публічний роут — `/order/{public_token}`. `Order.number` (`CMPX-260711-0042`) лишається **тільки** людським ідентифікатором для менеджера. Жодних публічних запитів по `number`.
⚡ Плюс `number` береться з **PostgreSQL-послідовності** (`nextval('order_number_seq')`), а не `count()+1`/`max()+1`.
**Обґрунтування (major ×2).** (1) Сторінка `/order/CMPX-260711-0042` — послідовний передбачуваний ідентифікатор. Перебір `0001..0100` за сьогоднішню дату віддавав ПІБ, телефон, e-mail, адресу і склад замовлення **будь-якого** покупця. (2) `count()+1` — гонка: два одночасні checkout'и отримають той самий номер, другий впаде з IntegrityError **вже після створення платежу**. Sequence не відкочується транзакцією і не має гонок за визначенням.

### ADR-015. 🆕 Платіжний провайдер — Protocol + вибір у `SiteSettings` (активний = **LiqPay**)
**Рішення.** `PaymentProvider` (Protocol, 5 методів) + реалізації. Активний — `SiteSettings.payment_provider`.
🆕 **v3: дефолт і єдиний провайдер на старті — `liqpay`** (рішення замовника, обґрунтування — **ADR-025**). Абстракцію провайдера **зберігаємо** (вона коштує один Protocol), але **другого провайдера не пишемо**, доки він не знадобиться: мертвий код гниє швидше за живий.
⚡ **v2 (лишається в силі, це вимоги до БУДЬ-ЯКОГО провайдера):** ідемпотентність вебхука — **два** ключі: `(provider, sha256(body))` **і** `(provider, invoice_id, status)` (другий не залежить від timestamp у тілі). Переходи в `PAID`/`REFUNDED` — **фінальні**: `Payment.objects.filter(pk=…, status__in=['created','pending']).update(status='paid')` — 0 рядків означає «подія проігнорована». ⚡ Обов'язкова звірка `payment.amount == order.total AND currency`; розбіжність → **гроші не проводимо**, алерт.
**Відкинуто.** Fondy — найвища комісія (2.2%), SDK не оновлювався з 2022, і головне: НБУ у 2024 відкликав ліцензію ТОВ «ФК Елаєнс» (мерчанти не отримували кошти). Контрагентський ризик рівня «гроші застрягнуть» — неприйнятний.

### ADR-016. ⚡ Ніякого `nulls_distinct=False` — сумісність з PG 14
**Рішення.** Унікальність EAV-рядка забезпечують **два часткові unique-індекси**:
```python
UniqueConstraint(fields=["product","attribute","option"], condition=Q(option__isnull=False), name="uniq_pav_option")
UniqueConstraint(fields=["product","attribute"],          condition=Q(option__isnull=True),  name="uniq_pav_scalar")
```
**Обґрунтування.** `UniqueConstraint(nulls_distinct=False)` (Django 5) вимагає **PostgreSQL 15+**. Локальна БД — 14. Це б зламало `migrate` на машині розробника і зробило локальне середовище нерепрезентативним. Два часткові індекси дають ту саму семантику на будь-якій версії.

### ADR-017. Кошик на клієнті + серверний preview
**Рішення.** Кошик/порівняння/бажання — `zustand persist` → localStorage. ⚡ Додано `POST /api/cart/preview` (body `[{id, qty}]`) → per-item актуальні `price`, `availability`, `installment_available`, `is_active`, `main_image_url` + cart-level `subtotal`, `installment_allowed` (**AND по всіх позиціях — рахується на сервері**), `changed_items`, `unavailable_items`. ⚡ `GET /api/catalog/products/bulk?ids=` для compare/wishlist (толерантний до неіснуючих id).
**Обґрунтування (major).** Без цього ціни в кошику протухали (синк міняє їх 4×/добу), а бейдж «Оплата частинами» рахувався на клієнті з протухлого localStorage — UX «вибрав спосіб оплати → отримав помилку на checkout». Тепер `installment_allowed` рахується **рівно в одному місці** — на сервері.

### ADR-018. ⚡ Захист адмінки
**Рішення.** `django-otp` (TOTP, обов'язково для всіх staff) + `django-axes` (лок після 5 невдалих спроб) + `ADMIN_URL` з env (не `/admin/`) + rate-limit на `/…/login/` у Caddy + `SESSION_COOKIE_AGE=8h`, `Secure/HttpOnly/SameSite=Lax` + алерт у Telegram на зміну `usd_rate` і на будь-який вхід з нового IP.
**Обґрунтування (critical).** Django Admin на публічному домені з логіном-паролем — головна поверхня атаки. Компрометація акаунта контент-менеджера = злив ПІБ/телефонів/адрес усіх замовлень + зміна цін і курсу.

### ADR-019. ⚡ Бекапи і моніторинг — не скрипт, а система
**Рішення.**
* **pgBackRest**: base backup щоночі + WAL-архівація кожні 5 хв → **RPO ≈ 5 хв, RTO ≤ 1 год**.
* **Окремий R2-бакет** для бекапів з **окремим токеном** (app-токен **не має** прав на бакет бекапів — інакше компрометація застосунку зносить і медіа, і бекапи). Object versioning + lifecycle 30 днів + шифрування.
* Versioning **і на бакеті медіа** — зараз перезапис/видалення фото невідновне.
* **Щотижнева автоматична restore-drill**: підняти тимчасовий postgres з останнього бекапу, `manage.py check` + `count(orders/products)` → пінг у моніторинг. **Неперевірений бекап = відсутній бекап.**
* **Dead-man's switch (Healthchecks.io)**: кожен успішний `sync_prices` / `sync_np_refs` / `generate_hotline_feed` пінгує свій check (grace = 2× інтервал). **Мовчазна смерть beat** (OOM, не піднявся після деплою) — найчастіший реальний збій cron-систем — тепер породжує алерт.
* `/healthz` (liveness) + `/readyz` (DB+Redis), зовнішній probe на головну, `/api/catalog/`, `/feeds/hotline.xml`.
* Алерти — **Telegram** (менеджер і так у телефоні), email — резервний канал. `PARTIAL` з `rows_failed > 1%` теж алертить.
**Обґрунтування (critical ×2).** У v1 бекапи = назви двох bash-скриптів; бекап на тому ж VPS — не бекап. Алерти були прив'язані до **факту виконання** задачі, тому мовчазна смерть beat не породжувала жодної події: синків просто немає, ціни заморожені на тиждень.

### ADR-020. ⚡ Circuit breaker на Нову Пошту
**Рішення.** `httpx timeout=(connect=2s, read=3s)`, максимум 1 ретрай. Circuit breaker у Redis: **5 помилок за 60 с → 60 с не ходимо в НП взагалі**, ендпоінт одразу віддає `200 {ok:false, reason:"np_unavailable"}`. Фронт **ніколи не блокує кнопку «Оформити»** на quote — показує «Вартість доставки повідомить менеджер», замовлення оформлюється з `delivery_cost_estimate=NULL`.
**Обґрунтування (critical).** «API НП не викликається з критичного шляху, КРІМ розрахунку ціни» — і це **і є діра**: якщо НП відповідає 30 с, кожен запит `/delivery/quote` тримає uvicorn-воркер. Кілька десятків користувачів на калькуляторі — і всі web-воркери зайняті, **лягає весь сайт**, а не лише калькулятор. Класичний каскадний збій.

### ADR-021. Габарити — `effective_dims()` як єдине джерело
🆕 **v3: джерело габаритів знайдено — це таблиця характеристик, а не ручне введення. Див. ADR-028.**
Ланцюг фолбеків стає чотириступеневим: **характеристики товару** → власні поля товару (ручний override) → `Category.default_*` → `SiteSettings`.

**Рішення.** Одна функція `effective_dims(product) -> (w, h, d, weight)`: товар → фолбек `Category.default_*` → глобальний дефолт з `SiteSettings`. Вона використовується **і** в калькуляторі, **і** при створенні `OrderItem` (зберігаємо **ефективні** значення, а не сирі), **і** при генерації ТТН.
**Обґрунтування.** Об'ємна вага НП = м³ × 250, ціна = `max(фактична, об'ємна)`. Виміряно live: Ужгород→Київ, 2 кг **без** габаритів = 97.5 грн, **з** габаритами 50×50×60 = **539.5 грн**. Це **5.5×**. У v1 `volume_m3` був `GeneratedField` (NULL, якщо хоч один вимір порожній), а фолбек жив у Python → в `OrderItem` зберігався NULL, ТТН вибивався без габаритів, і магазин **знову** недоплачував — рівно та пастка, заради якої габарити й вводили.

### ADR-022. ⚡ Розширення і TS-конфіг — у міграціях, не в init-скрипті
**Рішення.** Перша міграція `catalog/0001_initial`:
`TrigramExtension()`, `BtreeGinExtension()`, `UnaccentExtension()`, `RunSQL(CREATE TEXT SEARCH CONFIGURATION uk …)`, `RunSQL(CREATE FUNCTION complex_price_uah …)`, `RunSQL(CREATE SEQUENCE order_number_seq)`.
**Обґрунтування (major).** `infra/postgres/init/01-extensions.sql` виконується образом postgres **рівно один раз** при ініціалізації тому і **рівно в БД `$POSTGRES_DB`**. Тестова база pytest-django (`test_nisa`) клонується з `template1`, де розширень немає → перша ж міграція з `opclasses=['gin_trgm_ops']` падає в CI з `operator class "gin_trgm_ops" does not exist`. Так само ламається staging, restore в чисту БД і managed-Postgres.

### ADR-023. ⚡ ISR — on-demand + time-based fallback
**Рішення.** `export const revalidate = 600` на **всіх** ISR-сторінках + on-demand `revalidateTag` після синку (теги `catalog`, `category:{id}`, `product:{id}`), задача з `autoretry` (3 спроби, backoff), секрет у заголовку.
**Обґрунтування (major).** Якщо `revalidate_next()` впав (Next перезапускається на деплої, таймаут, секрет розійшовся) — у чистому on-demand ISR **немає самозагоєння**: сторінка лишається зі старою ціною **назавжди**. Покупець бачить стару ціну → сервер на checkout виставить нову → 409 і втрачений продаж. Плюс: **не ревалідуємо по 10k шляхах** — тільки теги, інакше Next захлинеться після синку.

---

## 4. Що змінилось у v2 і чому

### 4.1 Зміни моделі даних (найдорожчі — зробити ДО першої міграції)

| # | Зміна | Що ламалось без неї |
|---|---|---|
| 1 | **`SupplierOffer`** (нова таблиця) | Другий прайс перезаписував ціни Complex і взаємно деактивував каталог. `priority` був фікцією |
| 2 | **`Product.markup_percent`** (денорм) | `recalc` губив націнку → кожна зміна курсу стирала маржу |
| 3 | **`PriceHistory`** + PG-тригер | Неможливо відповісти «чому ціна змінилась»; масовий UPDATE не логувався |
| 4 | **`ProductPriceSnapshot`** | Не було жодного відкату помилкового прогону |
| 5 | **`Product.availability_locked`**, `locked_fields` → викинуто | «Під замовлення», поставлене вручну, затиралось синком. `locked_fields` обіцяв гарантію, якої не існувало |
| 6 | **`Product.source_category_ext_id`** | Товари з `__unmapped__` **ніколи** не переїжджали в новостворену категорію |
| 7 | **`Product.denorm_dirty` / `denorm_at`** | Втрачена Celery-задача = товар назавжди зі старим денормом, і ніхто не знає |
| 8 | **`Product.specs_json_uk/_ru`, `search_vector_uk/_ru`** | RU-картка показувала б українські характеристики; RU-пошук — по українському вектору |
| 9 | **`Category.hotline_enabled_default`** | Вимога ТЗ «увімкнути Hotline на категорію» не виконувалась (нові товари «протікали») |
| 10 | **`Attribute.range_buckets`, `filter_widget=range_buckets`** | Range-фільтри не мали індексу (GIN jsonb_path_ops не прискорює нерівності) |
| 11 | **`Order.public_token`** | IDOR: перебір номерів віддавав ПІБ/телефон/адресу будь-якого покупця |
| 12 | **`ProductImage.source`, `last_seen_run`, `attempts`, `next_retry_at`** | Замінене в прайсі фото лишалось головним назавжди; R2 накопичував сміття |
| 13 | **`Product.base_old_price`** (замість грн-`old_price`) | Після зміни курсу `old_price` міг стати **нижчим** за `price` |
| 14 | **`NPWarehouse.last_seen_run` / `NPSettlement.last_seen_run`** | Закрите відділення лишалось у дропдауні → ТТН не вибивається |
| 15 | **`PaymentWebhookEvent` unique `(provider, invoice_id, status)`** | Вебхуки не по порядку відкочували `PAID` назад у `PENDING` |
| 16 | `ProductAttributeValue` → `TimeStampedModel` | Не було чим порівняти свіжість денорму |

### 4.2 Зміни синхронізації

| Було (v1) | Стало (v2) |
|---|---|
| `bulk_create` прямо в `Product` | Синк пише в `SupplierOffer`; `project_offers()` переносить переможця в `Product` |
| `price_locked` товари **виключались** з `objs` | Проєкція оновлює їм наявність і службові поля; ціну — ні. Товар більше не «зникає з продажу» |
| Один статичний `update_fields` | Три окремі `UPDATE` у проєкції: ціна (`WHERE NOT price_locked`), наявність (`WHERE NOT availability_locked`), службові (завжди) |
| `updated_at` не входив у `update_fields` | Входить скрізь + `Now()` в кожному `qs.update()`. Інакше `sitemap lastmod` бреше і ISR не ревалідує |
| Дубль sku → «виграє останній» | **Явна дедуплікація по всьому прогону ДО запису** (`ON CONFLICT` не може двічі зачепити рядок — це `IntegrityError` на весь батч) |
| `CheckConstraint` як перша лінія | Валідація в **парсері**; constraint — друга лінія. `savepoint` на кожен батч + fallback на порядкову вставку |
| `qty is None` → TypeError / OUT_OF_STOCK | `qty is None` → `source.default_availability` (наявність визначає сам факт присутності рядка) |
| Guard тільки по кількості рядків | + Guard #2 по порогах масової зміни + snapshot + кнопка відкату |
| Redis-lock | `pg_try_advisory_xact_lock` + `reap_stale_runs` |
| Читаємо чанки, що встигли | `rowCount` з `spreadsheets.get` **до** читання; `len(rows) != expected` → **FAILED**, каталог не чіпаємо |
| Ціна рахується в Python (двічі, по-різному) | Ціна рахується **тільки** SQL-функцією `complex_price_uah()` |
| `on_commit` — єдина гарантія | `on_commit` + 6 heal-задач |

### 4.3 Зміни каталогу

* Range-фільтри: `GIN(jsonb_path_ops)` → **бакет-токени** (+ expression-індекси як escape hatch).
* Мультивибір: `@>` → **`&&` на групу**.
* Фасети: EAV → **той самий `filter_tokens`** (єдине джерело з фільтрацією).
* FTS: невизначеність → **TS-конфіг `uk` + застосунковий стемер + pg_trgm**.
* Злиття дублів: `on_delete=CASCADE` на `PAV.option` → **`PROTECT`** + сервіс `merge_into()` (пряме видалення `Brand`/`AttributeOption` **заборонене в адмінці** — воно тихо знищувало PAV сотень товарів каскадом).
* Денорм інвалідується не тільки з PAV, а й зі зміни `brand`/`country`/`category`/`ProductImage`/`Attribute`/`AttributeOption`.

### 4.4 Операційні зміни

* Бекапи, dead-man's switch, restore-drill, `/healthz`, ліміти ресурсів у compose, пінінг образів по git SHA, `AddIndexConcurrently`, `lock_timeout` перед migrate, `CELERY_TIMEZONE='Europe/Kyiv'`, розділені Redis-БД (`noeviction` на broker!), секрети через `docker secrets`, gitleaks у CI, скраб PII в Sentry.

---

## 5. Свідомо прийняті ризики

| Ризик | Чому приймаємо | Мітигація |
|---|---|---|
| **Овербукінг**: `stock_qty` не резервується під замовлення, синк його перезаписує | Менеджер підтверджує кожне замовлення телефоном | Показуємо «Залишилась 1 шт.», не блокуємо замовлення. Задокументовано |
| **PG 14 локально / 16 на проді** | Історична даність | Не використовуємо нічого з 15+ (ADR-016); задача `INFRA-02` — вирівняти на 16 |
| **Якість FTS без hunspell** | Власний образ Postgres дорого коштує в підтримці | Стемер у застосунку + trgm-fallback; метрика «0 результатів > 15%» → перехід на hunspell (Етап 4) |
| **Один VPS = SPOF** | Бюджет | RPO 5 хв / RTO 1 год через pgBackRest + restore-drill; 2 репліки backend за Caddy для rolling-деплою |
| **Ціни в кошику протухають між preview і checkout** | Немає серверного кошика | 409 з `changed_items` і повторним підтвердженням |
</content>
</invoke>
