# Complex — ROADMAP

> План виконання. Джерела: [ARCHITECTURE.md](./ARCHITECTURE.md), [DATA_MODEL.md](./DATA_MODEL.md),
> [SYNC.md](./SYNC.md), [INTEGRATIONS.md](./INTEGRATIONS.md), [reference/INPUTS.md](./reference/INPUTS.md),
> [research/*](./research/).
> Дата: 13.07.2026.

## Як читати

Кожна задача:

```
- [ ] **КОД · Назва** — оцінка
      ← залежить від: …            ‖ паралельно: так/ні (з чим конфліктує)
      ✓ готово, коли: конкретний перевірюваний факт
      ⛔ блокер: відкрите питання / відсутній доступ (якщо є)
```

**Правило оцінки:** усі задачі 0.5–3 дні. Якщо задача більша — вона неправильно нарізана.
**Правило готовності:** «зроблено» — не критерій. Критерій — команда, яку можна запустити, або факт, який можна побачити.
**Правило паралельності:** «паралельно: так» = задачу можна віддати окремому агенту в тій самій хвилі, вона не чіпає ті самі файли.

**Легенда блокерів:** ⛔A — немає доступу · ⛔Q — відкрите питання до замовника · ⛔D — залежить від іншої задачі.

---

# 🔥 ЩО РОБИМО ПРЯМО ЗАРАЗ

Наступні 5 задач у порядку виконання. Перші дві — **не код**, і саме тому вони перші: у них зовнішня затримка,
і поки замовник відповідає, ми вже пишемо каркас.

| # | Задача | Оцінка | Чому саме зараз |
|---|---|---|---|
| **1** | **ACCESS-01** — вислати замовнику чекліст доступів (Google SA, НП, LiqPay, R2, домен, Hotline firmId, Telegram) | 0.5 д | **Найдовший lead time у проєкті.** Без шарінгу Google Sheets синхронізація — пріоритет №1 — фізично не стартує. Замовляти доступи треба сьогодні, а не тоді, коли код буде готовий |
| **2** | **ACCESS-02** — вислати замовнику 13 відкритих питань **з нашими дефолтами** (щоб можна було відповісти «згоден» одним словом) | 0.5 д | Округлення ціни блокує фіскалізацію; `paypart` vs `moment_part` блокує економіку розстрочки; акцентний колір блокує FE-01. Питання з готовим дефолтом відповідаються за 10 хв, питання без дефолту — за 2 тижні |
| **3** | **INFRA-01** — каркас: `uv`, Django 5 + Ninja, `settings/{base,dev,prod}`, ruff+mypy+pytest, pre-commit, GitHub Actions (lint+test) | 1.5 д | Усе інше пишеться в цей каркас. Паралельно з (1)(2) |
| **4** | **MODEL-00** — PG14-аудит + **реконсиляція моделі даних** (звести розбіжності між DATA_MODEL / SYNC / INTEGRATIONS у ОДИН список полів) | 1.5 д | **Робиться ДО першої міграції.** Зараз у доках 12 підтверджених розбіжностей (див. MODEL-00). Виправити їх у міграції №1 коштує годину, у міграції №14 — тиждень |
| **5** | **MODEL-01 + MODEL-02** — `core` + `catalog` + міграція `0001_initial` (розширення, TS-конфіги `uk`/`ru_complex`, `complex_price_uah()`, тригер `price_history_trg`, `order_number_seq`) | 3 д | Серце системи. Після неї розпаралелюється все: синк, адмінка, API, фронт |

> Паралельно з (5) уже можна віддати окремому агенту **SYNC-03** (`normalize_model_name` — порт скрипта Артура)
> і **SYNC-02** (парсер чисел/пасток): це чисті функції без ORM, вони не чекають на міграцію.

---

# ⛔ БЛОКЕРИ: доступи і секрети

Без цього код пишеться, але **не працює**. Замовити **сьогодні**.

- [ ] **ACCESS-01 · Чекліст доступів замовнику** — 0.5 д
      ‖ паралельно: так (нічого не блокує в нас, блокує нас ззовні)
      ✓ готово, коли: лист надіслано, у трекері 10 рядків зі статусом «очікуємо»

| # | Доступ | Хто дає | Що блокує без нього | Критичність |
|---|---|---|---|---|
| 1 | **Шарінг прайсу на Google service account (Viewer)** + `spreadsheet_id` прайсу | Артур | **ВЕСЬ синк** (пріоритет №1 етапу 1) | 🔴 |
| 2 | **Шарінг таблиці характеристик** (`1qgMFRcTcH2Jt1q1kICmOYVJQiXSPzZpCVdP8UogqjDY`, лист «Гривнева») | Артур | Характеристики, фасети, **габарити для НП**, гарантія | 🔴 |
| 3 | **Google Cloud проєкт** → Sheets API + service account JSON | ми (потрібен GCP-акаунт) | те саме, що (1)(2) | 🔴 |
| 4 | **API-ключ Нової Пошти** (`my.novaposhta.ua` → Безпека → API 2.0) | замовник | Довідники НП → **checkout**; калькулятор | 🔴 |
| 5 | **Anthropic API key** + бюджет | замовник / ми | Модуль перекладу (RU в MVP!), `aispecs` | 🔴 |
| 6 | **Cloudflare R2**: бакет `media` + бакет `backups` + **два різні токени** | ми | Фото товарів, знімки прогонів, бекапи | 🔴 |
| 7 | **Домен** (новий, не galiton) + DNS | замовник | Прод, HTTPS, `<url>` у фіді Hotline | 🟠 |
| 8 | **VPS** | замовник | Деплой | 🟠 |
| 9 | **LiqPay**: `public_key`/`private_key` + sandbox-пара + увімкнене меню «Кредити» | замовник | Етап 2 (оплата), етап 3 (частини) | 🟡 |
| 10 | **Hotline**: `firmId` + анкета магазину (домен!) | замовник | Етап 4 (фід) | 🟡 |
| 11 | **Telegram bot token + chat_id**, Sentry DSN, Healthchecks.io | ми | Алерти. Без них збій синку **мовчазний** | 🟠 |

> 🔴 = блокує етап 1 · 🟠 = блокує реліз · 🟡 = блокує етап 2–4.

---

# ⛔ ВІДКРИТІ ПИТАННЯ

- [ ] **ACCESS-02 · Питання замовнику з дефолтами** — 0.5 д
      ‖ паралельно: так
      ✓ готово, коли: отримано письмову відповідь по Q1–Q5 і Q10–Q11 (решта може почекати)

| # | Питання | **Наш дефолт (працюємо по ньому, поки немає відповіді)** | Що реально блокує |
|---|---|---|---|
| Q1 | Прайс — **повний зріз** чи дельта? | Повний зріз + guard #2 на масову деактивацію | SYNC-04, SYNC-06. Якщо дельта — деактивацію зниклих **вимикаємо повністю** |
| Q2 | **Округлення ціни** після USD→UAH | До гривні (`unit`) | 🔴 **Блокує фіскалізацію (PAY-06):** `sum(rro_info.items[].cost)` мусить == `amount`, отже округлення **по-позиційне**. Не косметика |
| Q3 | **Націнка (markup)** — є? глобальна / по джерелу / по категорії? | 0%, поле `PriceSource.price_markup_percent` існує | MODEL-06, SYNC-06. Схема витримає будь-яку відповідь, але цифру треба знати до релізу |
| Q4 | **Курс USD** — з адмінки чи з комірки E4? | **Адмінка — єдине джерело.** Комірка E4 = підказка + кнопка «Підтягнути» (SYNC §3.6) | ADMIN-06 |
| Q5 | **Розстрочка:** `paypart` (платить магазин 2,3–27,3%) чи `moment_part` (платить покупець)? | Показати замовнику таблицю комісій, рекомендувати **`moment_part`** | 🔴 **Юніт-економіка.** Холодильник 30 000 ₴ на 6 платежів коштує магазину **2 400 ₴**. Блокує INS-01, `<payment>` у фіді, і визначає, чи потрібна **INS-02** взагалі (при `moment_part` — не потрібна, економія 3 днів) |
| Q6 | **Накладений платіж** буде? | Так (у побутовій техніці — обов'язково) | 🔴 Якщо так — **LiqPay ПРРО не закриває фіскалізацію**, потрібен Checkbox на всі типи оплат (PAY-06) |
| Q7 | Рахунок ФОП/ТОВ — **у ПриватБанку**? | Рекомендуємо ПБ | ПРРО від LiqPay доступний **тільки** мерчантам ПБ |
| Q8 | `min_payment` для фіда Hotline | 500 ₴ (ліміт LiqPay знизу — 300 ₴) | HOT-02 |
| Q9 | **MPN** (артикул виробника) — хто заповнить? | Ніхто; `<code>` віддаємо лише коли є. Фід від цього **не ламається** | HOT-02 (тільки якість мержу з картками). ШІ може витягти (AI-01) |
| Q10 | **Акцентний колір** — димчастий помаранчевий `#C2410C`? | Так (альтернатива — електрик-синій `#1D4ED8`) | 🔴 **Блокує FE-01, а FE-01 блокує ВЕСЬ фронтенд.** Перефарбувати після — це переверстка |
| Q11 | **Бейдж знижки** — сума (`-67 ₴`) чи відсотки? | Сума в гривнях (як на референсі) | FE-05, FE-07 |
| Q12 | **«Купити в 1 клік»** (є на референсі, немає в ТЗ) | Не робимо в MVP | FE-07 |
| Q13 | **301-редіректи** зі старого galiton | Не робимо, поки не підтвердять цінність SEO-ваги | SEO-04 (окремий дешевий модуль, архітектуру не міняє) |
| Q14 | Строк постачання для «Під замовлення» (`<shipping>` у Hotline) | 3–6 днів | HOT-02. Немає тега → Hotline пише «уточнюйте у продавця» → гірша конверсія |
| Q15 | Політика авто-схвалення перекладів | Словник — 100% руками (7 год); описи/SEO — авто за умови «валідатори OK + немає `model_note`» | I18N-06. Інакше **100 людино-годин** модерації описів |

---

# ЕТАП 0 — Фундамент (частково зроблено)

**Зроблено:** стек зафіксовано · архітектура спроектована і провалідована трьома критиками (22 critical + 31 major закрито) ·
вхідні дані від замовника зібрані й задокументовані · дослідження Hotline / LiqPay / дизайн-системи / перекладу ·
локальне оточення підняте (PG **14.21**, Redis, Python 3.12.13, uv 0.11.28 — перевірено).

- [ ] **ACCESS-01** · Чекліст доступів — див. вище — 0.5 д
- [ ] **ACCESS-02** · Відкриті питання з дефолтами — див. вище — 0.5 д

- [ ] **INFRA-01 · Каркас репозиторію** — 1.5 д
      ← залежить від: —          ‖ паралельно: так (з ACCESS-*, MODEL-00)
      ✓ готово, коли:
      · `uv sync` ставить оточення на Python 3.12; `uv run manage.py check` — 0 issues
      · `settings/{base,dev,prod}.py`, конфіг через env (`DATABASE_URL=postgres://maxrosul@localhost:5432/nisa`)
      · `uv run ruff check . && uv run mypy backend && uv run pytest` — зелено (на порожньому наборі тестів теж зелено)
      · pre-commit з ruff + gitleaks
      · GitHub Actions: lint + mypy + pytest на PR, **сервіс postgres той самий мажор, що локально**
      ⛔ немає

- [ ] **MODEL-00 · PG14-аудит + реконсиляція моделі даних** — 1.5 д
      ← залежить від: —          ‖ паралельно: так, але **МУСИТЬ завершитись ДО MODEL-02**
      ✓ готово, коли: створено `docs/DATA_MODEL_DELTA.md` з таблицею «поле → де було → як буде», і **всі 12 розбіжностей нижче закриті рішенням**

  **A. PG14 — перевірено емпірично на живій БД:**
  · ✅ `GENERATED ALWAYS AS (…) STORED` **працює на PG14** (перевірено). Отже `OrderItem.line_total = GeneratedField(db_persist=True)` **лишаємо** — це не блокер, всупереч поширеній думці. **У коментарі до моделі зафіксувати: «GeneratedField(db_persist=True) → PG12+, на PG14 OK; virtual — не підтримується Postgres»**
  · ❌ `UNIQUE NULLS NOT DISTINCT` **падає з syntax error на PG14** (перевірено) → ADR-016 підтверджено: **тільки два часткові unique-індекси** (`uniq_pav_option` / `uniq_pav_scalar`). У коментарі до `ProductAttributeValue.Meta` — точне посилання на ADR-016 і причину
  · `Product.volume_m3` як `GeneratedField` — **не повертаємо** (викинуто не через PG, а через `NULL` при відсутньому вимірі, ADR-021)
  · `CheckConstraint(check=…)` — у Django 5.1+ **deprecated**, використовуємо `condition=…`. Переписати всі 12 constraint'ів
  · Пройтись `grep` по доках: `nulls_distinct`, `MERGE`, `pg_stat_io`, `jsonb_path_ops` — переконатись, що нічого з PG15+ не просочилось

  **B. Розбіжності між документами (закрити ДО міграції):**

| # | Розбіжність | Рішення |
|---|---|---|
| 1 | `Product.installment_available` / `installment_max_payments` (DATA_MODEL) **vs** `installments_enabled` / `installments_max_payments` (LIQPAY, INTEGRATIONS) | Обрати **одні** імена, виправити в усіх доках. Пропозиція: `installments_enabled` / `installments_max_payments` |
| 2 | `Category.hotline_category_name` (CharField, DATA_MODEL) **vs** `Category.hotline_category` FK → `HotlineCategory` (INTEGRATIONS §2.5) | **FK на `HotlineCategory`.** Рядок ламається від першої описки. **Моделі `HotlineCategory` в DATA_MODEL немає взагалі** → додати (`path`, `name`, `depth`, `parent`) |
| 3 | `Category.lead_time_days` — використовується в INTEGRATIONS §2.2.2 (`<shipping>`), **у DATA_MODEL відсутнє** | Додати поле |
| 4 | `SyncRun.sheet_usd_rate`, `sheet_snapshot_date`, `needs_attention` — є в SYNC §3.6, **немає в DATA_MODEL** | Додати |
| 5 | `SyncRun.rows_section`, `rows_no_sku` — лічильники з SYNC §4.2, **немає в моделі** | Додати |
| 6 | `SyncLogEntry.Action` — бракує `ROW_NO_SKU`, `RATE_MISMATCH`, `AMBIGUOUS_NUMBER`, `STALE_PRICE_SHEET` | Додати в enum |
| 7 | `PriceSheet.header_row` / `data_start_row` / `rate_cell` / `date_cell` — є в SYNC §1.5.2, **немає в DATA_MODEL §3.1** | Додати + `CheckConstraint(data_start_row > header_row)` |
| 8 | `SpecSheet.spec_triplet_start_col` (позиційна константа = 10) — згадана в SYNC §1.5.3, поля немає | Додати `PositiveSmallIntegerField(default=10)` |
| 9 | `Payment.reference` (UUID, `order_id` для LiqPay) і `Payment.last_end_date` — є в INTEGRATIONS §3.3–3.4, **немає в DATA_MODEL §5** | Додати. **Це critical:** без `reference` повторна спроба оплати поверне старий платіж LiqPay |
| 10 | `PaymentCallback` (INTEGRATIONS §3.4) **vs** `PaymentWebhookEvent` (DATA_MODEL §5) — дві назви однієї сутності | Одна модель. Пропозиція: `PaymentWebhookEvent` (вона детальніша: `body_hash` + `(provider, invoice_id, status)`) |
| 11 | `Order.locale` (ARCHITECTURE ADR-003), `Order.fiscal_receipt_id/url/fiscalized_at`, `Order.paid_notified_at`, `Order.needs_bank_review` — **немає в DATA_MODEL §4** | Додати всі |
| 12 | `VariantGroup` (§2.7) досі спирається на `Product.variant_group/variant_label/variant_sort`, хоча коментар у §2.3 каже, що вони **прибрані** на користь `VariantItem`, якого в доці **немає** | Визначити `VariantItem(group, product, label[tr], sort, option→swatch)`. Етап 3, але **поле в `Product` не додавати зараз** |
| 13 | `SiteSettings.payment_provider` default = `"mono"`, `Payment.Provider` містить `MONO` | ADR-025: провайдер — **LiqPay**. Дефолт → `liqpay`, `MONO` з enum **прибрати** (мертвий код гниє швидше за живий) |
| 14 | `Category.default_width_cm` (DATA_MODEL) **vs** `Category.default_package_w_cm` (INTEGRATIONS §1.7) | Одні імена. Пропозиція: `default_package_*` — симетрично до `Product.package_*`, щоб не переплутати з габаритами виробу |

      ⛔ немає (це чиста робота з доками)

---

# ЕТАП 1 — MVP

**Пріоритет №1 всередині етапу — СИНХРОНІЗАЦІЯ.** Каталог, який не оновлюється, — не каталог.
Тому трек SYNC стартує одразу після MODEL-02 і не чекає ні адмінки, ні фронту.

## 1.1 Модель даних (трек MODEL)

- [ ] **MODEL-01 · `core`** — 1 д
      ← MODEL-00, INFRA-01     ‖ паралельно: ні (усі залежать від нього)
      ✓ готово, коли: `TimeStampedModel`, `SEOMixin`, `SingletonModel(get_solo + Redis-кеш)`,
      `core/db.py::advisory_lock()` (контекст-менеджер на `pg_try_advisory_xact_lock`),
      `core/breaker.py::CircuitBreaker` (Redis db2, 5 помилок/60 с → 60 с open),
      `core/text/uk_stem.py` (≈40 правил + словник винятків), `core/html.py::sanitize()` (bleach-allowlist з DATA_MODEL §2.3);
      `pytest tests/core/` — зелено, включно з `test_advisory_lock_released_on_disconnect`

- [ ] **MODEL-02 · `catalog` + міграція `0001_initial`** — 2 д
      ← MODEL-01               ‖ паралельно: ні
      ✓ готово, коли: **`uv run manage.py migrate` проходить на локальному PG 14.21 з нуля** і в БД реально існують:
      · розширення `pg_trgm`, `btree_gin`, `unaccent`
      · `SELECT to_tsvector('uk', 'холодильники')` не падає; `to_tsvector('ru_complex', 'холодильники')` не падає
      · `SELECT complex_price_uah(100, 'USD', 41.65, 15, 'unit')` повертає число
      · тригер `price_history_trg` на `catalog_product`
      · `SELECT nextval('order_number_seq')` = 1
      · таблиці: Category, Brand, Country, Product, PriceHistory, AttributeGroup, Unit, Attribute, AttributeOption, ProductAttributeValue, ProductImage
      ⚠️ **Ніяких `Meta.indexes` по перекладних колонках** — вони йдуть у MODEL-03 (modeltranslation додає колонки в `AppConfig.ready()`)

- [ ] **MODEL-03 · modeltranslation + індекси перекладних колонок** — 1 д
      ← MODEL-02               ‖ паралельно: ні
      ✓ готово, коли: `python manage.py makemigrations --check --dry-run` — «no changes»;
      у БД існують `prod_fts_uk_gin`, `prod_fts_ru_gin`, `prod_name_uk_trgm`, `prod_name_ru_trgm`;
      міграція індексів має `atomic = False` + `AddIndexConcurrently`;
      `Product.objects.create(name_uk="Холодильник")` → `product.name_ru == "Холодильник"` (fallback працює)

- [ ] **MODEL-04 · `sync` моделі** — 1 д
      ← MODEL-02               ‖ **паралельно з MODEL-05** (різні аппи)
      ✓ готово, коли: `PriceSource`, `PriceSheet` (з `header_row=3`, `data_start_row=5`, `rate_cell=E4`), `SpecSheet`,
      **`SupplierOffer`** (unique `(price_source, sku)`), `SyncRun`, `SyncLogEntry`, `ProductPriceSnapshot`, `UsdRateChange`;
      `migrate` проходить; `SupplierOffer.objects.bulk_create(update_conflicts=True, unique_fields=["price_source","sku"])` не падає

- [ ] **MODEL-05 · `orders` / `payments` / `delivery` / `cms` / `siteconfig` / `feeds` / `aispecs` / `i18n`** — 2 д
      ← MODEL-02               ‖ **паралельно з MODEL-04**
      ✓ готово, коли: `migrate` проходить; `OrderItem.line_total` реально `GENERATED … STORED` (перевірити `\d+ orders_orderitem`);
      `Order.number` береться з sequence, `public_token` — uuid4 unique; `TranslationEntry` з unique `(content_type, object_id, field, target_lang)`

- [ ] **MODEL-06 · Ціна: SQL + Python-двійник + parity-fuzz** — 1 д
      ← MODEL-02               ‖ паралельно: так
      ✓ готово, коли: **`pytest tests/catalog/test_pricing.py::test_price_parity_python_vs_sql` — зелено на сітці
      `base ∈ {0.01, 1, 145, 6600, 27445.5} × rate ∈ {41.65, 44.75} × markup ∈ {0, 15} × rule ∈ {none, unit, ten, nine}`**
      (160 комбінацій, розбіжність == 0 копійок);
      `test_sync_and_recalc_give_identical_price` — зелено

- [ ] **MODEL-07 · Seed-фікстури** — 0.5 д
      ← MODEL-02, MODEL-04     ‖ паралельно: так
      ✓ готово, коли: `manage.py seed_base` створює: службову категорію `__unmapped__` (`is_active=False, is_service=True`),
      `AttributeGroup` («Основні», «Габаритні розміри», «Інші»), ~25 `Unit` з aliases, `SiteSettings` (pk=1),
      `WorkingHours` (ПН–ПТ 09:00–20:00, СБ–НД 10:00–17:00);
      команда **ідемпотентна** (другий запуск → 0 змін)

## 1.2 Синхронізація — ПРІОРИТЕТ №1 (трек SYNC)

- [ ] **SYNC-03 · `normalize_model_name()` — порт скрипта Артура** ⭐ **передумова синку** — 0.5 д
      ← MODEL-00 (тільки за іменами полів)   ‖ **паралельно: так — це чиста функція, її можна писати ще до міграції**
      ✓ готово, коли: `catalog/services/naming.py::normalize_model_name()` і `pytest tests/catalog/test_naming.py` (≥25 кейсів) — зелено:
      · NBSP → пробіл · усі тире (`‐‑‒–—―−`) → `-` · апострофи (`'‘\`´`) → `'` · `×` → `x`
      · `unicodedata.normalize("NFKC")` → `.lower()`
      · **кирилична `І/і/Ї/ї` → латинські `I/i`** — тест `"Sіemens HZ66D910"` (кирилична `і`!) == `"siemens hz66d910"`
      · викид усього, крім `[a-z0-9._\-/]`; прибирання пробілів
      · генерація кандидатів: обріз по `(`, `[`, `,`, `/`, ` | `, ` - `
      ⚠️ **Це передумова SYNC-08.** Основний ключ зіставлення прайсу зі SpecSheet — **артикул** (він є в обох таблицях),
      але Артурів скрипт зшивав по назві, і в даних лишились рядки з битим/порожнім артикулом → потрібен фолбек

- [ ] **SYNC-02 · Парсер прайсу: числа, пастки, валідація рядка** — 1.5 д
      ← MODEL-00               ‖ **паралельно: так (чисті функції)**
      ✓ готово, коли: `pytest tests/sync/test_parsing.py` — зелено:
      · `_to_decimal_uk("27 445,50 ₴")` (NBSP!) == `Decimal("27445.50")`; `"6 600,00"` == `6600.00`; `"61.5"` == `61.5`
      · `_norm_sku(2400042.0)` == `"2400042"` (Sheets віддає число при `UNFORMATTED_VALUE` — **без цього артикули не зійдуться жодного разу**)
      · `_norm_category_id("с50549829")` (**кирилична `с`**) == `"50549829"`
      · `_is_placeholder("Уточнюється")` == True (і в Категорія/Виробник/Країна/Фото → порожньо; у Ціна/Артикул → ERROR)
      · **матриця з 7 рядків (SYNC §4.2)**: рядок-секція (`Аксесуари до техніки`) → пропуск **мовчки**;
        рядок без артикула (`Запчастина для духовки TEKA 83340602`) → `WARN ROW_NO_SKU`, **прогін не PARTIAL**;
        ціна `> 10_000_000` (з'їхала кома) → `ROW_INVALID`
      · `WARN AMBIGUOUS_NUMBER`, якщо після коми рівно 3 цифри (`6,600`)

- [ ] **SYNC-01 · Google Sheets клієнт + жорстка звірка повноти** — 1 д
      ← INFRA-01               ‖ паралельно: так
      ✓ готово, коли: `integrations/sheets/client.py` (`google-api-python-client`, `static_discovery=True`, `UNFORMATTED_VALUE`);
      `pytest tests/sync/test_reader.py`:
      · `test_incomplete_read_fails_hard` — прочитано 10k з 15k → **`SyncRun.FAILED`, каталог недоторканий** (не PARTIAL!)
      · `test_retry_on_429` (6 спроб, backoff+jitter)
      · `test_missing_column_fails_loudly`
      · **`offset = data_start_row - 1`** — звірка `len(rows) + offset == rowCount` сходиться на реальній геометрії (заголовки в рядку 3, дані з 5)
      · порожній хвіст сітки входить у `len(rows)` і відсіюється в парсері **без WARN**
      ⛔A: реальні таблиці — потрібен шарінг (ACCESS-01 №1,2). **До того — працюємо на фікстурах-снапшотах зі скрінів**

- [ ] **SYNC-04 · Дедуп SKU + два guard'и + сирий знімок у R2** — 1 д
      ← SYNC-02, MODEL-04      ‖ паралельно: ні (той самий модуль, що SYNC-05)
      ✓ готово, коли: `pytest tests/sync/test_guards.py` — зелено:
      · `test_duplicate_sku_in_one_sheet` — 2 рядки з тим самим sku → **1 оффер, 1 WARN, прогін НЕ FAILED** (виграє **перший**)
      · `test_guard_min_rows_aborts` (< 50% живих офферів)
      · `test_guard_mass_deactivation_aborts` (60% зникло)
      · `test_guard_median_price_jump_aborts` (усі ціни ×2)
      · **`test_guard_qty_column_zeroed_aborts`** — «Артур зсунув колонку К-сть»: рядків стільки ж, guard #1 мовчить, guard #2 ловить
      · `test_force_true_bypasses_guard_and_records_user`
      · `SyncRun.guard_report` містить цифри, не текст; `raw_snapshot_key` вказує на gzip-CSV у R2

- [ ] **SYNC-05 · Upsert офферів (пісочниця джерела) + savepoint-fallback** — 1 д
      ← SYNC-04                ‖ паралельно: ні
      ✓ готово, коли: `pytest tests/sync/test_upsert.py`:
      · `test_batch_with_bad_row_survives` — **499 з 500 товарів записались** (savepoint на батч → порядкова вставка)
      · `test_updated_at_changes_on_price_update` — `updated_at` **у `update_fields`** (інакше sitemap і ISR брешуть)
      · `test_idempotent_rerun` — повторний прогін тих самих даних → 0 змін по суті
      · деактивація зниклих — **тільки `filter(price_source=src)`**

- [ ] **SYNC-06 · `project_offers()` — проєкція SupplierOffer → Product** ⭐ **ядро** — 1.5 д
      ← SYNC-05, MODEL-06      ‖ паралельно: ні
      ✓ готово, коли: `pytest tests/sync/test_multisource.py` + `test_locks.py` — зелено, **усі 10**:
      · `test_low_priority_source_does_not_steal_sku`
      · `test_alpha_sync_does_not_deactivate_complex_products`
      · `test_nisa_sync_still_sees_shared_sku`
      · `test_winner_switches_when_complex_offer_disappears`
      · `test_deactivate_only_when_no_active_offers`
      · **`test_price_locked_product_stays_in_stock`** ← головний регресійний тест: у v1 `price_locked` товар **зникав з продажу** на першому ж синку
      · `test_price_locked_price_not_changed` / `test_price_locked_availability_IS_updated`
      · `test_availability_locked_keeps_manual_on_order` / `test_availability_locked_still_gets_last_seen_run`
      · `test_price_history_written_by_trigger_on_mass_update` — `qs.update()` теж логується

- [ ] **SYNC-07 · Категорії, бренди, країни** — 1 д
      ← SYNC-06                ‖ паралельно: так (після SYNC-06)
      ✓ готово, коли: `pytest tests/sync/test_categories.py`:
      · `test_unknown_category_goes_to_unmapped_inactive` — товар у `__unmapped__` створюється **`is_active=False`**
      · **`test_products_remapped_after_category_created`** — контент-менеджер створює категорію → товари **самі** туди переїжджають (`post_save` → `remap_unmapped_products`)
      · `test_source_category_ext_id_always_written`
      · `resolve_brand()` — пошук по `name` (ci) і `aliases` (GIN); не знайдено → `needs_review=True` + WARN

- [ ] **SYNC-08 · SpecSheet: трійки → EAV** — 1.5 д
      ← SYNC-03, SYNC-06, MODEL-07   ‖ паралельно: так
      ✓ готово, коли:
      · трійки читаються **позиційно з колонки J** (`spec_triplet_start_col=10`), заголовок у J звіряється → інакше **FAILED, голосно**
      · зіставлення: **основний ключ — артикул** (колонка G), **фолбек — `name_normalized`** (SYNC-03), розбіжності — в лог
      · `Attribute`/`AttributeOption`/`Unit` резолвляться по `code` **або `aliases`** (GIN); не знайдено → створюються з
        **`is_filterable=False, filter_widget=NONE, needs_review=True`** ← тест `test_new_attribute_is_not_filterable`
      · `ProductAttributeValue.bulk_create(update_conflicts=True)` **з дедупом PAV у межах товару** (та сама пастка «ON CONFLICT двічі»)
      ⛔A: реальна таблиця характеристик (ACCESS-01 №2)

- [ ] **SYNC-09 · Габарити упаковки + вага + гарантія (`Attribute.role`)** ⭐ **передумова калькулятора НП** — 1 д
      ← SYNC-08                ‖ паралельно: ні (той самий модуль)
      ✓ готово, коли: `pytest tests/delivery/test_dims.py` — зелено:
      · `"171,5 x 56 x 35"` → `height=171.5, width=56, depth=35` (**порядок ВхШхГ**, читається з назви характеристики `(ВхШхГ)`, а не з позиції)
      · роздільник: `x` (лат.) / **`х` (кирилична!)** / `×`; десятковий: **і кома, і крапка** (`171,5` поруч з `61.5`)
      · `Вага в упаковці (кг): 61.5` → `package_weight_kg=61.5`
      · **`test_product_dims_are_NOT_used_for_np`** — `Висота (мм): 1635` і `Вага (кг): 58` у `package_*` **не потрапляють ніколи**
        (це різні числа: виріб 58 кг / упаковка 61.5 кг — плутанина = недоплата на кожній посилці)
      · заповнення йде через `Attribute.role = PACKAGE_DIMS / PACKAGE_WEIGHT / WARRANTY_MONTHS` — **без хардкоду назв** у коді
      · `WHERE package_dims_source <> 'manual'` — ручне введення не затирається
      · невідповідний формат → **не падаємо**: WARN + `dims_source="category"`

- [ ] **SYNC-10 · Фото: завантаження, SSRF, ETag, деривативи** — 1.5 д
      ← SYNC-06                ‖ **паралельно: так** (окрема черга, окремі файли)
      ✓ готово, коли: `pytest tests/sync/test_images.py`:
      · `test_ssrf_private_ip_blocked` (резолв DNS, заборона private/loopback/link-local)
      · `test_etag_304_skips_download` · `test_oversize_rejected` (10 МБ, і по `Content-Length`, і по стріму)
      · **`test_stale_sheet_image_deleted_manual_kept`** — `source="sheet"` без `last_seen_run` видаляється, `source="manual"` **не чіпається ніколи**
      · **`test_two_manual_images_no_integrity_error`** (частковий unique по `source_url_hash != ''`)
      · деривативи 300/700/1600 webp, **імена — тільки латиниця** (`{url_hash[:16]}_md.webp`) ← вимога Hotline
      · `set_main_image()` — двокрокова, у `ProductAdmin.save_formset` теж

- [ ] **SYNC-11 · `rebuild_product_denorm` (обидві мови)** — 1.5 д
      ← SYNC-08, MODEL-03      ‖ паралельно: ні
      ✓ готово, коли:
      · `specs_json_uk` / `specs_json_ru` — структура `[{g,gs,code,n,u,v,vn,s}]`; **`n` БЕЗ одиниці, `u` окремо** (рендер клеїть `u` до `v`)
      · `filter_tokens` — **без мови** (тільки slug'и): `brand:gorenje`, `obiem:200-299`, `avail:in_stock`, `cond:2`, `installment:1`
      · **`test_filter_tokens_identical_for_uk_and_ru`** — перемикання мови **не змінює вибірку**
      · `search_vector_uk` (конфіг `uk` + стемер у Python **симетрично**), `search_vector_ru` (`ru_complex`); ваги A/B/C/D
      · **опис (`description`) у `filter_tokens` не потрапляє ніколи** — тест
      · `denorm_dirty` знімається; `denorm_at` виставляється

- [ ] **SYNC-12 · `recalc_prices` + `UsdRateChange`** — 0.5 д
      ← MODEL-06, SYNC-06      ‖ паралельно: так
      ✓ готово, коли: `test_recalc_preserves_markup` (15% **не зникає** після зміни курсу);
      `test_rounding_change_recalcs_uah_products_too` (`scope="all"`);
      `test_old_price_never_below_price`;
      recalc бере **той самий advisory-lock**, що й sync; `UsdRateChange` пишеться **тільки при реальній зміні** (diff у `SiteSettings.save()`)

- [ ] **SYNC-13 · Відкат прогону** — 0.5 д
      ← SYNC-06                ‖ паралельно: так
      ✓ готово, коли: `test_rollback_restores_prices_and_availability`;
      **`test_rollback_skips_products_touched_after_run`** — товари, змінені після прогону, **не чіпаються**;
      `PriceHistory` отримує `reason='rollback'`

- [ ] **SYNC-14 · Heal-задачі, reaper, watchdog, purge** — 1 д
      ← SYNC-06, SYNC-11       ‖ паралельно: так
      ✓ готово, коли: `pytest tests/sync/test_healing.py`:
      · **`test_lost_on_commit_task_is_healed`** — симулюємо смерть процесу між COMMIT і `.delay()` → через `heal_denorm` товар оживає за 5 хв
      · `test_reap_stale_run_marks_failed` · `test_heal_usd_prices_finds_undercalculated`
      · у `django-celery-beat` реально є 12 періодичних задач (SYNC §8) з **`timezone == "Europe/Kyiv"`** ← тест у CI
      · Redis broker (db0) — **`maxmemory-policy noeviction`** (перевірити `redis-cli config get maxmemory-policy`); cache (db1) — `allkeys-lru`

- [ ] **SYNC-15 · Dry-run + ПЕРШИЙ БОЙОВИЙ ПРОГІН** 🎯 — 1 д
      ← усі SYNC-*, ACCESS-01  ‖ паралельно: ні
      ✓ готово, коли:
      · `manage.py sync_prices --source=complex-main --dry-run` віддає повний diff: скільки створиться/оновиться/деактивується, `guard_report`, список невідомих категорій, топ-20 змін ціни
      · **бойовий прогін на реальній таблиці Артура: `SyncRun.status=SUCCESS`, у БД реальні товари з реальними цінами, `rows_failed == 0`**
      · `SyncRun.rows_section` і `rows_no_sku` — цифри, які збігаються з тим, що видно оком у таблиці
      ⛔A: шарінг таблиць (ACCESS-01 №1,2). **Це найважливіша віха етапу 1**

- [ ] **SYNC-16 · Добити тести синку до повного переліку SYNC.md §11** — 2 д
      ← SYNC-15                ‖ паралельно: так
      ✓ готово, коли: **усі 40 тестів з SYNC.md §11 існують і зелені**; покриття `sync/` ≥ 85%

## 1.3 Довідники Нової Пошти (трек NP) — ⭐ передумова checkout

- [ ] **NP-01 · Клієнт НП + circuit breaker** — 0.5 д
      ← MODEL-01               ‖ **паралельно: так**
      ✓ готово, коли: `integrations/novaposhta/client.py`;
      **`test_http_200_with_success_false_raises`** — НП **завжди** віддає HTTP 200, `raise_for_status()` не рятує; єдина правда — поле `success`;
      `test_errors_can_be_empty_on_failure` — на `errors[0]` покладатись не можна, логуємо `errorCodes`;
      breaker: 5 помилок/60 с → 60 с не ходимо; таймаути веб `(2s, 3s)` / celery `(5s, 60s)`

- [ ] **NP-02 · Нічний імпорт довідників `sync_np_refs()`** ⭐ **передумова checkout** — 1.5 д
      ← NP-01, MODEL-05        ‖ паралельно: так
      ✓ готово, коли: **у локальній БД реально лежать ~25 областей, ~11 100 міст, ~26 800 населених пунктів, ~53 000 відділень**
      (`Limit=5000` працює — «ліміт 150 для getSettlements» це застарілий міф; `Page` **1-based**);
      · `getWarehouses` проєктується з 54 полів до ~15 (інакше 178 МБ сміття)
      · **guard: НП віддала < 80% від поточного → `ABORTED`, довідник недоторканий** ← без нього частковий збій НП знищує дропдаун і **зупиняє продажі**
      · зниклі → `is_active=False`, **ніколи не DELETE** (на них посилаються старі замовлення)
      · smoke-assert після запису: у Ужгороді й Києві є активні відділення
      · збережено **обидва** ref: `NPSettlement.ref` (SettlementRef) **і** `delivery_city_ref` (CityRef — канонічний!)
      · beat `0 3 * * *`, пінг Healthchecks
      ⛔A: API-ключ НП (ACCESS-01 №4)

- [ ] **NP-03 · API довідників для checkout** — 1 д
      ← NP-02, API-01          ‖ паралельно: так
      ✓ готово, коли: `GET /api/delivery/settlements?q=Ужг` віддає результат **< 20 мс** з власної БД (trgm по `present`);
      `GET /api/delivery/warehouses?city_ref=…&category=Postomat` — **поштомати відфільтровані за габаритами товару**
      (`ReceivingLimitationsOnDimensions` 40×30×60 — холодильник туди не влізе; це найчастіший баг у чекаутах);
      Branch / Postomat / DropOff **згруповані**, не в одному плоскому списку (в Ужгороді поштоматів 148 проти 20 відділень)

- [ ] **NP-04 · `effective_dims()` — єдина функція** — 0.5 д
      ← SYNC-09                ‖ паралельно: ні (спільний модуль з SYNC-09)
      ✓ готово, коли: `delivery/services/dims.py::effective_dims(product) -> (h, w, d, weight, source)`;
      ланцюг: **характеристики → `Product.package_*` (ручний override) → `Category.default_package_*` → `SiteSettings`**;
      **та сама функція** викликається з `/delivery/quote`, з `OrderItem` і з генерації ТТН — тест `test_single_source_of_dims`;
      `OrderItem` зберігає **ефективні** значення + `dims_source`

## 1.4 API (трек API)

- [ ] **API-01 · Ninja skeleton + OpenAPI→TS** — 1 д
      ← MODEL-02, INFRA-01     ‖ паралельно: так
      ✓ готово, коли: `/api/openapi.json` валідний; `npm run gen:api` генерує TS-типи у `frontend/lib/api/types.ts`;
      `/healthz` (liveness) і `/readyz` (DB+Redis) віддають 200; throttling per-IP налаштований

- [ ] **API-02 · Каталог: лістинг + фасети з ОДНОГО джерела** — 2 д
      ← SYNC-11, API-01        ‖ паралельно: ні
      ✓ готово, коли:
      · `GET /api/catalog/products?lang=ru&category=5609730&brand=bosch&brand=gorenje&obiem=200-299`
      · **мультивибір усередині групи = `&&` (OR), між групами = AND** ← тест `test_multiselect_brand_returns_union_not_empty`
        (у v1 `@>` давав **нуль результатів** на двох брендах — фасети не працювали б взагалі)
      · фасети — **один** запит `SELECT t, count(*) FROM product, unnest(filter_tokens) t … GROUP BY t` з drop-self
      · **`test_facet_count_matches_result_count`** — «Bosch (12)» → клік → рівно 12 товарів (не 9)
      · підписи фасетів — з `Attribute.name_{lang}` (Redis, TTL 1 год); `lang` — **частина ключа кожного кешу**
      · `EXPLAIN ANALYZE` лістингу з 3 фільтрами — **GIN-скан, не Seq Scan**

- [ ] **API-03 · Пошук (uk + ru)** — 1.5 д
      ← API-02, MODEL-01       ‖ паралельно: так
      ✓ готово, коли: `«холодильники»` знаходить `«Холодильник Gorenje NRK6202»` (стемер застосований **симетрично**: і при побудові вектора, і при парсингу запиту);
      `«халадильник»` (опечатка) знаходить через `pg_trgm` (`similarity > 0.3`) при 0 результатів FTS;
      RU-пошук працює на `search_vector_ru`;
      метрика «частка запитів з 0 результатів» логується (поріг 15% → перехід на hunspell на етапі 4)

- [ ] **API-04 · Картка / bulk / cart preview** — 1 д
      ← API-02                 ‖ паралельно: так
      ✓ готово, коли: `GET /api/catalog/products/{id}` — **1 запит до БД**, нуль JOIN (усе зі `specs_json_{lang}`);
      `GET /api/catalog/products/bulk?ids=` — толерантний до неіснуючих id (для compare/wishlist);
      **`POST /api/cart/preview`** → per-item актуальні `price`/`availability`/`is_active` + cart-level `subtotal`,
      **`installment_allowed` (AND по всіх позиціях — рахується на СЕРВЕРІ)**, `changed_items`, `unavailable_items`

- [ ] **API-05 · Замовлення** — 1.5 д
      ← API-04, NP-03, MODEL-05  ‖ паралельно: ні
      ✓ готово, коли:
      · `POST /api/orders` з `idempotency_key`; **сервер перераховує total з БД**; `total != expected_total` → **409 з `changed_items`**
      · `Order.number` ← `nextval('order_number_seq')` ← тест `test_concurrent_checkout_no_duplicate_number` (два одночасні checkout'и)
      · `Order.public_token` (uuid4); публічний роут — `/order/{public_token}`, **ніколи не `/order/{number}`** ← тест `test_order_lookup_by_number_returns_404` (закриття IDOR)
      · `validate_installment` викликається з **трьох** місць (API, `Order.clean()`, перед створенням Payment)
      · `OrderItem` зберігає снапшот (`sku`, `name`, `price`) + **ефективні** габарити

- [ ] **API-06 · CMS + siteconfig** — 0.5 д
      ← MODEL-05               ‖ паралельно: так
      ✓ готово, коли: `/api/cms/{banners,menu,pages,news}`, `/api/siteconfig` (контакти, графік, курс — без секретів)

## 1.5 Адмінка (трек ADMIN)

- [ ] **ADMIN-01 · unfold + OTP + axes + захист** — 1 д
      ← MODEL-02               ‖ паралельно: так
      ✓ готово, коли: адмінка на `ADMIN_URL` з env (**не `/admin/`**); TOTP **обов'язковий** для всіх staff
      (вхід без OTP → редірект на налаштування); axes лочить після 5 невдалих; `SESSION_COOKIE_AGE=8h`, `Secure/HttpOnly/SameSite=Lax`;
      тест `test_admin_requires_otp`

- [ ] **ADMIN-02 · Категорії (з міні-емблемами)** — 1 д
      ← ADMIN-01, MODEL-02     ‖ паралельно: так
      ✓ готово, коли: дерево з `path`/`depth`; **завантаження міні-емблеми** (`icon`, SVG/PNG, валідатор);
      перейменування батька **каскадно перераховує `path` нащадків** ← тест;
      `hotline_enabled_default`; дія «Увімкнути Hotline на категорію + нащадків» (асинхронна, з аудитом)

- [ ] **ADMIN-03 · Товари** — 2 д
      ← ADMIN-01, MODEL-03     ‖ паралельно: ні
      ✓ готово, коли:
      · **таби `UA` / `RU`** на кожному перекладному полі + бейдж статусу перекладу + кнопка «Перекласти»
      · **TipTap-редактор опису** із завантаженням зображень (опис — rich HTML з вбудованими фото, INPUTS §2); санітизація на збереженні; `img[src]` — **тільки з нашого R2-домену**
      · inline-галерея; **`save_formset` перевизначено** → зміна головного фото не падає з IntegrityError (partial unique `uniq_main_image` **не може бути DEFERRABLE**)
      · фільтри: «без характеристик», «без фото», «`dims_source != spec`», «hotline_enabled + порожній mpn», `needs_review`
      · `price_locked` / `availability_locked` — видимі тумблери з підказкою, що саме вони блокують

- [ ] **ADMIN-04 · Характеристики, бренди, країни + злиття дублів** — 1.5 д
      ← ADMIN-01               ‖ паралельно: так
      ✓ готово, коли:
      · **`has_delete_permission = False`** на `Brand` / `Country` / `AttributeOption` ← тест
        (просте видалення дубля обнуляло б бренд у сотень товарів і вибивало їх з фасета)
      · дія «Злити з…» → `merge_brands(src, dst)`: перенос товарів, злиття `aliases`, `denorm_dirty=True`, інвалідація кешу фасетів ← тест `test_merge_preserves_products`
      · черга `needs_review=True` — окремий розділ
      · `Attribute.range_buckets` редагується; `manage.py suggest_buckets --attr=obiem` пропонує межі по перцентилях
      · `Attribute.role` — рівно **одна** характеристика на роль (unique constraint) ← тест

- [ ] **ADMIN-05 · Синхронізація: панель керування** — 1.5 д
      ← SYNC-06, ADMIN-01      ‖ паралельно: ні
      ✓ готово, коли: у `SyncRunAdmin` реально працюють **кнопки**:
      · «Оновити зараз» · «Застосувати попри попередження» (`force=True`, обов'язково з `started_by`) ·
        **«Відкотити прогін»** · «Зняти лок»
      · банери: 🔴 «У прайсі 3 невідомі ID категорій: 50549829, … — створіть їх» ·
        🟡 «У прайсі курс 44,75, в адмінці 41,65» **+ кнопка «Підтягнути з таблиці»** (запускає нормальний `recalc_prices` від імені юзера) ·
        🔴 «Останній успішний синк — 9 год тому»
      · `guard_report` показується цифрами; лог прогону фільтрується по `level`

- [ ] **ADMIN-06 · SiteSettings (курс USD) + Замовлення + Beat** — 1.5 д
      ← ADMIN-01, MODEL-05     ‖ паралельно: так
      ✓ готово, коли: зміна `usd_rate` → **алерт у Telegram** + `UsdRateChange` + асинхронний `recalc_prices`;
      зміна `price_rounding` → recalc **усіх** товарів (`scope="all"`, не тільки USD);
      **зміна телефону/SEO НЕ запускає recalc** ← тест `test_settings_save_without_rate_change_is_noop`;
      `OrderAdmin`: статуси, історія, друк, фільтр по телефону; розклад beat редагується (`DatabaseScheduler`)

## 1.6 i18n і модуль перекладу (трек I18N) — RU в MVP

- [ ] **I18N-01 · Локалі, маршрути, fallback** — 0.5 д
      ← MODEL-03               ‖ паралельно: так
      ✓ готово, коли: `LANGUAGES = [("uk"), ("ru")]`, дефолт `uk`, `MODELTRANSLATION_FALLBACK_LANGUAGES = ("uk",)`;
      middleware мапить URL-локаль `ua` → Django `uk`; **порожній `*_ru` ніколи не показується як порожнеча** ← тест

- [ ] **I18N-02 · `TranslationEntry` + збір цілей + Translation Memory** — 1 д
      ← MODEL-05               ‖ паралельно: так
      ✓ готово, коли: `collect_translation_targets()` створює/оновлює записи;
      **`test_same_source_hash_is_translated_once`** — та сама характеристика у 5 000 товарів → **1 звернення до API**;
      зміна UA → запис стає `STALE`, а сайт **далі віддає старий `published_text`** ← тест
      (два поля `target_text` / `published_text` — і є відповідь на «переклад не йде одразу в прод»)

- [ ] **I18N-03 · Claude-клієнт: промпт, глосарій, prompt cache, Batch API** — 1.5 д
      ← I18N-02, ACCESS-01 №5  ‖ паралельно: ні
      ✓ готово, коли:
      · system-блок (правила + глосарій) **≥ 4 096 токенів** ← інакше кеш на Opus 4.8 **мовчки не спрацює**
        (`cache_creation_input_tokens = 0`, **без жодної помилки**). Тест: `cache_read_input_tokens > 0` на другому запиті
      · Batch API (−50%); порядок: словник → категорії → назви → описи → SEO
      · `cost_usd` пишеться на кожен запис; бюджетний ліміт `SiteSettings.ai_monthly_budget_usd` реально зупиняє чергу
      ⛔A: Anthropic API key

- [ ] **I18N-04 · Сегментний переклад HTML через DOM + валідатори** — 1.5 д
      ← I18N-03                ‖ паралельно: ні
      ✓ готово, коли: **розмітка й `<img>` фізично не потрапляють у модель** (обхід DOM, інлайнові теги кодуються як `<0>…</0>`);
      `test_img_src_unchanged_after_translation`; `test_tag_count_preserved`;
      валідація не пройшла → **`FAILED`, у чергу на схвалення такий запис НЕ потрапляє**

- [ ] **I18N-05 · Назви товарів: бренд і модель не перекладаються** — 0.5 д
      ← I18N-03                ‖ паралельно: так
      ✓ готово, коли: `split_name("Варильна поверхня газова Bosch PNK6B2P40R", "Bosch")` → у модель їде **тільки** `"Варильна поверхня газова"`;
      пост-валідатор: `Counter(latin_tokens(src)) == Counter(latin_tokens(dst))` ← інакше `FAILED` + 1 ретрай на Opus

- [ ] **I18N-06 · Черга схвалення в адмінці** — 1.5 д
      ← I18N-03, ADMIN-01      ‖ паралельно: ні
      ✓ готово, коли: окремий розділ (не змішаний з товарами); дві колонки `UK | RU`;
      сортування **за пріоритетом впливу** (`attribute_name` → `unit` → `attribute_value` → `category_name` → `product_name` → `seo` → `description`);
      значок ⚠ біля рядків з `model_note`;
      bulk-дії: «Схвалити й опублікувати» (**блокується, якщо `validation_errors != []`**), «Відхилити з причиною» (причина йде в наступний промпт), «Не перекладати»;
      `approve()` → `published_text` → `write_back_to_model()` → `*_ru` → `denorm_dirty=True` → `revalidateTag('catalog:ru')`;
      дашборд: pending / stale / failed / **витрати за місяць**

- [ ] **I18N-07 · Первинний переклад СЛОВНИКА** 🎯 — 1 д (наш) + ~7 год модератора
      ← I18N-06, SYNC-08       ‖ паралельно: ні
      ✓ готово, коли: **~4 900 записів словника** (`Attribute.name` ~600 + `AttributeOption.value` ~6 000 (з них ~35% `skip`) + `Unit` ~25 + `Category.name` ~300) перекладені і **схвалені**;
      `specs_json_ru` реально інша від `specs_json_uk` на випадковій вибірці 20 товарів;
      **`12 міс` у RU-версії рендериться як `12 мес`** ← найпоказовіший тест того, що `Unit` — таблиця, а не рядок;
      факт витрат зафіксовано (очікуємо **< $1** на словник)

## 1.7 Фронтенд (трек FE)

- [ ] **FE-01 · Дизайн-система в код** ⭐ **передумова ВСЬОГО фронту** — 1 д
      ← Q10 (акцентний колір)  ‖ паралельно: так (не залежить від бекенду взагалі)
      ✓ готово, коли:
      · `frontend/app/globals.css` — усі токени з DESIGN_SYSTEM §2.1 (світла + `.dark`)
      · Inter Variable **self-hosted** через `next/font/local` (**без CDN**), `tabular-nums` на цінах
      · `shadcn/ui` init; Button/Input/Sheet/Dialog/Combobox/Checkbox/Collapsible встановлені
      · **скрипт перевірки контрасту в CI: 46/46 пар проходять WCAG** (`--border` свідомо 1.27:1 — декоративний; обводка контролів — окремий токен `--input`, 3.42:1)
      · Storybook або `/dev/tokens` — сторінка з усіма токенами і компонентами
      ⛔Q10: підтвердити помаранчевий `#C2410C`. **Перефарбувати після — це переверстка**

- [ ] **FE-02 · Каркас Next.js** — 1.5 д
      ← FE-01, API-01          ‖ паралельно: ні
      ✓ готово, коли: `app/[locale]/…` — **сегмент локалі обов'язковий**; `/` → 302 на `/ua/` за `Accept-Language`;
      `generateStaticParams` віддає **обидві** локалі; `locale` пробрасується в кожен fetch (`?lang=`);
      `export const revalidate = 600` **на всіх** ISR-сторінках + on-demand `revalidateTag` (теги **з мовою**: `catalog:ru`, `product:{id}:ru`);
      типи API — з OpenAPI (`npm run gen:api`), не руками

- [ ] **FE-03 · Хедер + мегаменю + бургер + футер** — 2 д
      ← FE-02                  ‖ паралельно: так
      ✓ готово, коли: мегаменю з **міні-емблемами** зліва (20px, `currentColor` — не кольорові PNG, щоб працювало в темній темі), 2–3 колонки підкатегорій справа;
      клавіатура: `↑↓` по кореневих, `→` у підкатегорії, `Esc`, фокус повертається на тригер;
      бургер (мобайл): Каталог / Мої бажання / Порівняння / Інформація / Покупцям (**без «Особистого кабінету»**),
      **Час роботи ПН–ПТ 09:00–20:00, СБ–НД 10:00–17:00**, телефони як `tel:`, перемикач `UA | RU` + теми

- [ ] **FE-04 · Головна** — 1 д
      ← FE-03, API-06          ‖ паралельно: так
      ✓ готово, коли: слайдер, промо-блок, плитки категорій, добірки; LCP-зображення з `priority`; Lighthouse LCP < 2.5 с

- [ ] **FE-05 · Каталог: сітка + фасети** — 3 д
      ← FE-03, API-02          ‖ паралельно: ні
      ✓ готово, коли:
      · сітка 2/3/4/5 колонок (< 640 / 640–1023 / 1024–1439 / ≥ 1440); **дві колонки на мобільному, не одна**
      · `ProductCard`: бейдж знижки, статус (крапка + текст — **ніколи не колір самотою**), ціна `price-lg` з `tnum`, **зарезервована висота ціни** (сітка не «стрибає»)
      · фасети: desktop — sticky sidebar 280px; mobile — кнопка «Фільтри (3)» → `Sheet` на весь екран + «Показати 124 товари»
      · лічильник 0 → `disabled`, **не ховати** (інакше фільтр «мигає»)
      · стан у URL (`?brand=lg&color=black`) — для SEO і шеру; **однакові slug'и в UA і RU** (токени без мови)
      · перерахунок: сітка `opacity-60`, **не спіннер на весь екран**

- [ ] **FE-06 · Пошук + автокомпліт** — 1 д
      ← FE-03, API-03          ‖ паралельно: так

- [ ] **FE-07 · Картка товару** — 2 д
      ← FE-05, API-04          ‖ паралельно: ні
      ✓ готово, коли:
      · галерея: desktop — вертикальні мініатюри зліва + лайтбокс (**не hover-лупа** — ламається на тач); mobile — свайп + лічильник `3/8`
      · **`SpecsTable`: дві колонки `Характеристики | Опис` на ПК, стек на мобільному (Характеристики першими), обидві згорнуті з градієнт-фейдом і «Усі характеристики ▾»**
      · **🔴 одиниця клеїться до ЗНАЧЕННЯ:** `Висота` → `284 мм`. **Ніколи `Висота (мм)` → `284`** ← е2е-тест на реальному товарі
      · опис — rich HTML з вбудованими фото (`prose`), обидва блоки **повністю в DOM** (згортання — CSS/JS), щоб краулер бачив текст
      · `Код товару: {sku}`, статус наявності повним бейджем

- [ ] **FE-08 · Кошик** — 1 д
      ← FE-05, API-04          ‖ паралельно: так
      ✓ готово, коли: `zustand persist` → localStorage; при відкритті — `POST /cart/preview`;
      **`test_price_changed_shows_banner`** — ціна змінилась синком → банер «Ціни оновились», а не тихий обман

- [ ] **FE-09 · Checkout** — 2 д
      ← FE-08, API-05, NP-03   ‖ паралельно: ні
      ✓ готово, коли: комбобокс міста (дебаунс 300 мс, мін. 2 символи, дані з **нашої** БД);
      комбобокс відділення/поштомата — **Branch / Postomat / DropOff згруповані**;
      способи доставки (відділення / поштомат / кур'єр / самовивіз Ужгород / кур'єр по Ужгороду) і оплати;
      **`test_checkout_never_blocked_by_np_outage`** — НП лежить → «Вартість доставки повідомить менеджер», кнопка «Оформити» **лишається активною**;
      409 з бекенду → «Ціни оновились, підтвердіть»

- [ ] **FE-10 · Статичні сторінки, новини, `/order/{token}`** — 1 д
      ← FE-03, API-06          ‖ паралельно: так

- [ ] **FE-11 · Мобільна версія — наскрізний прохід** — 1 д
      ← FE-05, FE-07, FE-09    ‖ паралельно: ні
      ✓ готово, коли: усі екрани пройдені на 360px і 390px; тач-таргети ≥ 44px;
      body-scroll-lock у Sheet'ах; **горизонтального скролу немає ніде** (перевірити на таблиці характеристик і порівнянні)

- [ ] **FE-12 · Перемикач мови + hreflang** — 0.5 д
      ← FE-02, I18N-07         ‖ паралельно: так
      ✓ готово, коли: перемикач зберігає поточний шлях; у `<head>` — `<link rel="alternate" hreflang="uk-UA">` / `"ru-UA"` / `"x-default"` (→ UA), **взаємні** (якщо `ua` посилається на `ru`, `ru` мусить посилатись на `ua` — інакше Google ігнорує всю групу);
      `<link rel="canonical">` — **на свою ж мову**, не на UA; `<html lang>` = поточна мова

## 1.8 Реліз MVP (трек REL)

- [ ] **REL-01 · Прод-інфраструктура** — 2 д
      ← усе вище              ‖ паралельно: ні
      ✓ готово, коли: VPS + Docker Compose + Caddy (HTTPS, rate-limit на `/…/login/`);
      GitHub Actions → GHCR, **теги = git SHA** (не `latest`); секрети через `docker compose secrets` (**не env** — env світиться в `docker inspect` і в breadcrumbs Sentry);
      Redis: broker db0 `noeviction` + `appendonly yes`, cache db1 `allkeys-lru`;
      `lock_timeout` перед `migrate`; rolling-деплой на 2 репліках backend
      ⛔A: VPS + домен

- [ ] **REL-02 · Бекапи і моніторинг** — 1.5 д
      ← REL-01                ‖ паралельно: так
      ✓ готово, коли: pgBackRest (base щоночі + WAL кожні 5 хв → **окремий R2-бакет з окремим токеном**; app-токен **не має** прав на бакет бекапів);
      **restore-drill щотижня реально піднімає БД з бекапу і рахує товари/замовлення** ← неперевірений бекап = відсутній бекап;
      Healthchecks.io dead-man's switch на `sync_prices` / `sync_np_refs` (grace = 2× інтервал) — **мовчазна смерть beat породжує алерт**;
      Sentry (`send_default_pii=False` + скраб `token`/`sign`/`phone`/`email`); Telegram-алерти; versioning на бакеті медіа

- [ ] **REL-03 · Наповнення контентом** — 1 д (переважно замовник)
      ← ADMIN-02, ADMIN-03    ‖ паралельно: так
      ✓ готово, коли: 59 категорій з міні-емблемами; статичні сторінки (оплата-доставка, гарантія, повернення, про нас, контакти, покупцям); банери; `PickupPoint` (Ужгород, координати)

- [ ] **REL-04 · UAT + чеклист приймання MVP** 🎯 — 1 д
      ← усе                   ‖ паралельно: ні
      ✓ готово, коли **усі 10 пунктів зелені**:
      1. Синк 4×/добу автоматично; каталог оновлюється; `SyncRun.SUCCESS`
      2. Помилковий прогін відкочується кнопкою в адмінці
      3. Фасети рахують правильно (лічильник == результат)
      4. Пошук українською знаходить за словоформою
      5. RU-версія: назви, характеристики, одиниці — російською; фолбек ніде не показує порожнечу
      6. Checkout проходить до кінця, ТТН можна вибити (реальні refs НП)
      7. Мобільна версія — жодного горизонтального скролу
      8. Курс USD змінюється в адмінці → ціни перераховуються, **націнка не зникає**
      9. Бекап відновлюється (restore-drill зелений)
      10. Beat помер → алерт у Telegram прийшов

---

# ЕТАП 2 — Калькулятор НП, LiqPay, порівняння і бажання

## 2.1 Калькулятор доставки

- [ ] **NPC-01 · `/delivery/quote` + кеш + breaker** — 1.5 д
      ← NP-04 (⭐ `effective_dims`), NP-01   ‖ паралельно: так
      ✓ готово, коли:
      · **`OptionsSeat` шлеться ЗАВЖДИ**, навіть коли `dims_source="default"` ← інакше НП рахує тільки за фактичною вагою і магазин **недоплачує в 5.5×**
      · `test_volumetric_weight` — Ужгород→Київ, 2 кг: **без габаритів `97.5`, з `50×50×60` — `539.5`** (це не баг, це коефіцієнт 250 кг/м³)
      · `ServiceType` **відповідає обраній точці**: `Branch` → `WarehouseWarehouse`, `Postomat` → `WarehousePostomat` (107.5, не 97.5!), кур'єр → `WarehouseDoors`
      · Redis-кеш TTL 24 год; ключ **включає `cost_declared`** (оголошена вартість = ціна товару, а вона змінюється 4×/добу) **і тиждень тарифів**
      · `DeliveryQuote` у Postgres — **тільки аналітика, семплювання 10%** (публічний ендпоінт → INSERT у БД = вектор забивання диска)
      · breaker відкритий → `200 {ok: false, reason: "np_unavailable"}`, **не 5xx і не таймаут**

- [ ] **NPC-02 · UI DeliveryCalculator у картці** — 1 д
      ← NPC-01, FE-07         ‖ паралельно: ні
      ✓ готово, коли: показує **вагу і габарити, які пішли в розрахунок** (`61.5 кг · 171,5×56×35 см`) — це знімає половину дзвінків у підтримку;
      ціна доставки — **не акцентним кольором** (акцент не розмивати: це не ціна товару);
      помилка → «Вартість уточнить менеджер», **покупку не блокує**

## 2.2 LiqPay

- [ ] **PAY-01 · Клієнт LiqPay (свій, ~80 рядків)** — 1 д
      ← ACCESS-01 №9          ‖ паралельно: так
      ✓ готово, коли: `pytest tests/payments/test_signature.py`:
      · `sha1` від **бінарного digest** (`.digest()`, **не** `.hexdigest()`)
      · `private_key + data + private_key`, де `data` — **base64-рядок**, а не JSON
      · порівняння — **`hmac.compare_digest`** (constant-time)
      🔴 **Офіційний SDK `liqpay/sdk-python` використовувати ЗАБОРОНЕНО**: це Python 2 і там `requests.post(..., verify=False)` — **вимкнена перевірка TLS**

- [ ] **PAY-02 · `apply_payment_status()` — ЄДИНА точка переходу станів** — 1.5 д
      ← PAY-01, MODEL-00 №9   ‖ паралельно: ні
      ✓ готово, коли: `Payment.reference` (UUID) — **`order_id` для LiqPay, НЕ `Order.pk`** ← інакше повторна спроба оплати поверне **старий** платіж;
      мапа `LIQPAY_STATUS_MAP` — один словник; невідомий статус → `PENDING` + WARN (**не «оплачено» і не «провалено»**);
      **`sandbox` у проді → ALERT і товар НЕ віддаємо** (означає витік sandbox-ключа або `sandbox:1` у бойовому конфізі)

- [ ] **PAY-03 · Callback + 4 запобіжники** — 1 д
      ← PAY-02                ‖ паралельно: ні
      ✓ готово, коли: `pytest tests/payments/test_callback.py` — усі 4:
      1. **ідемпотентність** (`sha256(data)`) — той самий callback двічі → один ефект
      2. **звірка суми** (`amount` і `currency`) → розбіжність = **гроші НЕ проводимо** + алерт (без цього будь-хто, підібравши `order_id`, проведе замовлення на 1 грн)
      3. **out-of-order** (`end_date`): старіший callback не перетирає новіший
      4. **фінальність**: `paid` → тільки `reversed`; `pending` після `paid` **ігнорується**
      ⚠️ дедуп лише по `sha256(data)` **недостатній**: у тілі є `end_date`, тому кожен ретрай має інше тіло → (3) і (4) — **основний**, а не додатковий захист

- [ ] **PAY-04 · Звірка (`action=status` + реєстр)** — 1 д
      ← PAY-02                ‖ паралельно: так
      ✓ готово, коли: beat кожні 5 хв; **та сама `apply_payment_status()`**, що й у callback (ніякого дубльованого мапінгу);
      старші 48 год → `EXPIRED` + алерт; щоденний `action=reports` звіряє суми **і `receiver_commission`** (єдине джерело правди по юніт-економіці розстрочки)

- [ ] **PAY-05 · Sandbox + офлайн-тести** — 1 д
      ← PAY-03                ‖ паралельно: так
      ✓ готово, коли: **`manage.py liqpay_simulate_callback --order <ref> --status success`** формує **валідно підписаний** callback і б'є у наш в'ю
      — це єдиний спосіб тестувати ідемпотентність / out-of-order / amount-mismatch **без інтернету**;
      тестові картки проходять; `server_url` через `cloudflared tunnel` локально

- [ ] **PAY-06 · Фіскалізація (ПРРО)** — 1.5 д
      ← PAY-03, ⛔Q2, ⛔Q6, ⛔Q7  ‖ паралельно: ні
      ✓ готово, коли: **`sum(rro_info.items[].cost) == amount`** до копійки ← саме тому **округлення мусить бути по-позиційне** (Q2 — це блокер);
      якщо накладений платіж є (Q6) → **LiqPay ПРРО не підходить**, ставимо Checkbox на всі типи оплат і `rro_info` в LiqPay **не передаємо** (щоб не було подвійних чеків);
      `Order.fiscal_receipt_id/url/fiscalized_at`; таск ідемпотентний
      🔴 Штраф — 100% суми першої непробитої операції, 150% за кожну наступну

- [ ] **PAY-07 · Фронт оплати** — 1 д
      ← PAY-03, FE-09         ‖ паралельно: так
      ✓ готово, коли: прихована POST-форма → редірект на LiqPay; сторінка `/order/{public_token}` показує статус;
      ⚠️ **`result_url` — це НЕ підтвердження оплати**, лише UX. Довіряємо **тільки** вебхуку

## 2.3 Порівняння і бажання

- [ ] **CMP-01 · Порівняння** — 1.5 д
      ← API-04, FE-05         ‖ паралельно: так
      ✓ готово, коли: `CompareTable` — липкий перший стовпець + липка шапка; **тумблер «Тільки відмінності»** (головна цінність порівняння побутової техніки);
      ліміт 4 товари, localStorage; мобільний — **та сама таблиця з горизонтальним скролом**, не «карточний» варіант (він губить сенс)

- [ ] **CMP-02 · Список бажань** — 0.5 д
      ← CMP-01               ‖ паралельно: так

---

# ЕТАП 3 — Супутні товари, варіанти, оплата частинами

- [ ] **VAR-01 · `VariantGroup` / `VariantItem` — модель + адмінка** — 1 д
      ← MODEL-00 №12         ‖ паралельно: так
      ✓ готово, коли: вісь варіанта = **будь-яка характеристика** (`Attribute` FK), а не хардкод «діагональ»;
      `VariantItem.label` — **перекладний** (`Слонова кістка` / `Слоновая кость`); `swatch_hex` / `swatch_image` живуть на `AttributeOption`

- [ ] **VAR-02 · VariantSwitcher (кнопки + кружечки)** — 1.5 д
      ← VAR-01, FE-07        ‖ паралельно: ні
      ✓ готово, коли: **A. кнопки** (`50" (127 см)`, `55"`, …) — відсутній варіант **не ховається**, а `disabled + line-through` (людина має бачити, що 85" існує, але немає);
      **B. кружечки кольорів** — поруч зі свотчем **назва кольору текстом** (WCAG 1.4.1: колір не можна кодувати самим кольором);
      `role="radiogroup"`, керування стрілками; зміна варіанта = навігація на URL іншого товару (свій артикул, своя ціна)

- [ ] **REL-05 · Супутні товари** — 1.5 д
      ← MODEL-05             ‖ паралельно: так
      ✓ готово, коли: `RelatedGroup` + `RelatedGroupItem`; **взаємність — властивість структури**: менеджер збирає ОДНУ групу з 3 товарів → усі троє бачать двох інших. **Асиметрія неможлива в принципі** ← тест;
      види: комплект / аксесуари / разом купують

- [ ] **INS-01 · Оплата частинами: тумблер + бейдж** — 1.5 д
      ← PAY-03, ⛔Q5, ⛔Q8    ‖ паралельно: ні
      ✓ готово, коли:
      · `paytypes` передається **ЗАВЖДИ явно** ← інакше LiqPay покаже те, що ввімкнено в кабінеті, і **по-товарний тумблер перестане працювати взагалі**
      · `cart_allows_installments()` рахується **на сервері**: всі позиції підтримують **І** `300 ≤ total ≤ 300 000` **І** UAH
      · бейдж на картці = **мінімум** по всіх товарах (найконсервативніше)
      · 🔴 **у документації для замовника прямо написано:** у LiqPay Checkout **немає параметра «максимум N платежів»** — кількість (2–25) обирає **покупець**. Бейдж «6 платежів» — **маркетинг, не обмеження**. Покупець обере 12 → магазин заплатить **13,7%** замість 6,5%

---

# ЕТАП 4 — Hotline, інші постачальники, ШІ, SEO

## 4.1 Hotline

- [ ] **HOT-01 · Рубрикатор Hotline** — 1 д
      ← MODEL-00 №2          ‖ паралельно: так
      ✓ готово, коли: `sync_hotline_tree` тягне `hotline_tree_uk.csv`, **декодує cp1251** (не UTF-8!), парсить **відступи** (кількість провідних `;` = глибина, це **не CSV**);
      у БД — **1223 вузли, 4 рівні** (17 → 108 → 663 → 435);
      `Category.hotline_category` — **FK з автокомплітом**, не рядок (рядок ламається від першої описки)

- [ ] **HOT-02 · Генератор XML** — 2 д
      ← HOT-01, ⛔Q8, ⛔Q14   ‖ паралельно: ні
      ✓ готово, коли:
      · стрімінг курсором (не тримаємо 10k товарів у пам'яті); **atomic write + rename** (бот не має спіймати напівзаписаний файл)
      · **`<code>` віддаємо ТІЛЬКИ якщо `mpn != ""`; порожній `mpn` — це НЕ причина скіпу** ← у v1 це робило фід **порожнім на 100%**
      · `<code>` — **тільки MPN виробника**. Внутрішній артикул (`2400042`) туди класти **прямо заборонено** — він іде в `<id>`
      · `<vendor>` — рівно один; **країну класти ЗАБОРОНЕНО** (для неї `<param name="Країна виготовлення">`)
      · **одиниця клеїться до значення**: `<param name="Висота">1635 мм</param>`
      · ціна **без роздільників розрядів**: `6 600,00` → `6600`
      · `<guarantee type="manufacturer">12</guarantee>` з характеристики; **немає характеристики → тег ОПУСКАЄМО** (дефолт 12 не вигадуємо — це обіцянка покупцеві)
      · `<description>` **не віддаємо взагалі** (на сайті Hotline не показується, HTML заборонений)
      · `<rate>`/`<priceRUSD>` **не віддаємо** (інакше Hotline перерахує **своїм** курсом і ціна розійдеться з нашою → скарга і зняття з майданчика)
      · `<delivery carrier>` — enum **тільки `SLF | ME | NP | UP`**; **перевізника «Delivery» в enum НЕМАЄ**
      · шляхи зображень — **тільки латиниця** (кирилиця = товар без фото)
      · валідація фіда проти специфікації — тест на 2 реальних товарах

- [ ] **HOT-03 · Роздача фіда + свіжість** — 0.5 д
      ← HOT-02, REL-01       ‖ паралельно: ні
      ✓ готово, коли: `https://{domain}/feeds/hotline.xml` — **статичний файл з named volume**, віддає Caddy
      ← **named volume, а не шар контейнера**: інакше будь-який `compose up -d` **знищує файл** → бот отримує 404 → **товари знімаються з майданчика**;
      **жодних редіректів, Basic Auth, JS-челенджу** (Hotline не публікує UA/IP робота → allowlist будувати не можна);
      `heal_hotline_feed` щогодини: `is_current` старший 24 год **або файл зник** → регенерація + алерт;
      зовнішній probe: 200, `Content-Length > 0`, **0 редіректів**
      🔴 Фід публікується **7 днів**. Не оновився — публікація припиняється

- [ ] **HOT-04 · Масове вмикання на категорію** — 0.5 д
      ← HOT-02, ADMIN-02     ‖ паралельно: так
      ✓ готово, коли: дія в `CategoryAdmin` (асинхронна, `path__startswith` → всі нащадки);
      **`Category.hotline_enabled_default` успадковується НОВИМ товаром при створенні** (в INSERT-частині проєкції, **але не в UPDATE** — щоб не затирати ручне вимкнення)
      ← без цього категорія «протікає» на **кожному** синку, і вимога ТЗ не виконується навіть після ручного вмикання

## 4.2 Інші постачальники

- [ ] **SUP-01 · Другий прайс зі статусом «Під замовлення»** — 1.5 д
      ← SYNC-06              ‖ паралельно: так
      ✓ готово, коли: другий `PriceSource(priority=200, default_availability=on_order, default_lead_days=14)` реально підключений;
      **`test_qty_none_uses_source_default`** — прайс **без колонки К-сть** → `ON_ORDER`, **не `OUT_OF_STOCK`** (у v1 це ламало саме той сценарій, який ТЗ просить закласти);
      `test_on_order_source_without_lead_days_fails_fast` — людське повідомлення у формі адмінки, а не «порушено constraint» на 20-й хвилині прогону;
      усі 5 тестів `test_multisource.py` лишаються зеленими

## 4.3 ШІ-збір характеристик

- [ ] **AI-01 · `aispecs`: Claude + web search** — 2 д
      ← MODEL-05, I18N-03    ‖ паралельно: так
      ✓ готово, коли: `SpecHarvestJob` → `proposed_specs` з `confidence` і **посиланнями на джерела**;
      **ШІ пропонує також `mpn`** (для Hotline `<code>`) **і `proposed_dims`** (для калькулятора НП) — два поля, яких немає в прайсі і які найдорожче обходяться порожніми;
      бюджетний ліміт реально зупиняє чергу

- [ ] **AI-02 · Адмінка ШІ: diff і схвалення** — 1 д
      ← AI-01, ADMIN-03      ‖ паралельно: ні
      ✓ готово, коли: **`test_ai_never_writes_to_catalog_automatically`** — статус завжди `NEEDS_REVIEW`;
      адмін бачить diff «пропоновано / поточне»; «Застосувати» → `ProductAttributeValue(source=ai)` + `denorm_dirty=True`
      🔴 ШІ **правдоподібно галюцинує** характеристики побутової техніки. Помилкова діагональ у фільтрі = повернення товару; помилкові габарити = недоплата за доставку

## 4.4 Оплата частинами — пряма інтеграція з банком

- [ ] **INS-02 · Прямий API «Оплата частинами» ПриватБанку (повз LiqPay Checkout)** — 3 д
      ← INS-01, ⛔Q5, ⛔A: договір з ПБ + `storeId`   ‖ паралельно: так (окремий провайдер, не чіпає LiqPay-гілку)
      ✓ готово, коли:
      · **покупець фізично НЕ МОЖЕ обрати більше ніж `installments_max_payments`** ← тест на живій угоді.
        Це **єдиний сенс цієї задачі**: у LiqPay Checkout параметра «максимум N платежів» **немає**, тому бейдж «6 платежів» (INS-01) — маркетинг.
        Тут він стає **технічним обмеженням**: `storeId` + `merchantType` + `partsCount` шлються прямо в ПБ
      · комісія магазину за угоду **звіряється з тарифною сіткою ПБ** (2 платежі → 2,3% … 24 → 26,3%) і пишеться в `Payment.merchant_commission`
        ← без цього юніт-економіка розстрочки лишається сліпою зоною (пор. PAY-04 `receiver_commission`)
      · провайдер реалізує **той самий `PaymentProvider`-Protocol** (ADR-015), стани йдуть через **ту саму `apply_payment_status()`** (PAY-02) — нуль дубльованого мапінгу
      · `SiteSettings.installment_provider ∈ {liqpay, privatbank_direct}` — перемикається без деплою; **відкат на LiqPay в одну зміну поля**
      🔴 Це задача **про гроші, а не про фічу.** Робити її має сенс **тільки якщо Артур увімкнув `paypart`** (Q5).
      Якщо лишається `moment_part` (наш дефолт — комісія магазину 0%) — **задача не потрібна взагалі**, і 3 дні економляться.

## 4.5 SEO

- [ ] **SEO-01 · Sitemap + robots** — 1 д
      ← FE-12                ‖ паралельно: так
      ✓ готово, коли: **два набори URL** (ua + ru) + `<xhtml:link rel="alternate" hreflang>` на **кожному** `<url>`;
      **взаємність перевірена тестом** (якщо `ua` → `ru`, то `ru` → `ua`, інакше Google **ігнорує всю групу**);
      `x-default` → UA; `lastmod` з `updated_at` ← саме тому `updated_at` мусить бути в `update_fields` синку (інакше sitemap бреше)

- [ ] **SEO-02 · Мета-теги** — 0.5 д
      ← FE-12                ‖ паралельно: так
      ✓ готово, коли: `canonical` — **на свою ж мову**, не на UA; невірний slug → **301** на канонічний (старий сайт віддавав 200 → нескінченний дубль-контент) ← тест

- [ ] **SEO-03 · Мікророзмітка** — 1 д
      ← SEO-02               ‖ паралельно: так
      ✓ готово, коли: JSON-LD `Product` + `Offer` (`price`, `availability`, `priceCurrency`) + `BreadcrumbList` + `Organization`;
      Google Rich Results Test — 0 помилок

- [ ] **SEO-04 · 301-редіректи зі старого домену** — 1 д
      ← ⛔Q13                ‖ паралельно: так
      ✓ готово, коли: `RedirectRule(old_path, new_path, code)` + мапа `legacy_id → product_id`; топ-100 старих URL віддають 301
      ⛔Q13: чи є SEO-вага в старому домені. **Архітектуру не міняє — чекати відповіді не треба, робити теж**

---

# Паралелізація: як розкидати по агентах

| Хвиля | Можна вести **одночасно** | Хто на кого чекає |
|---|---|---|
| **W0** | `ACCESS-01` ‖ `ACCESS-02` ‖ `INFRA-01` ‖ `MODEL-00` | — |
| **W1** | `MODEL-01` → `MODEL-02` | послідовно, це критичний шлях |
| **W2** | `MODEL-03` ‖ `MODEL-04` ‖ `MODEL-05` ‖ `MODEL-06` ‖ `MODEL-07` ‖ **`SYNC-02`** ‖ **`SYNC-03`** ‖ `SYNC-01` ‖ `NP-01` ‖ **`FE-01`** ‖ `ADMIN-01` | `SYNC-02/03` і `FE-01` **не залежать від БД взагалі** — віддавати одразу |
| **W3** | `SYNC-04→05→06` (ланцюг) ‖ `NP-02` ‖ `API-01` ‖ `ADMIN-02` ‖ `ADMIN-04` ‖ `I18N-01/02` ‖ `FE-02` | `SYNC-06` — вузьке місце етапу 1 |
| **W4** | `SYNC-07` ‖ `SYNC-08→09` ‖ `SYNC-10` ‖ `SYNC-12` ‖ `SYNC-13` ‖ `ADMIN-05` ‖ `NP-03` ‖ `FE-03/04` ‖ `I18N-03` | |
| **W5** | `SYNC-11` → `API-02` → `FE-05` (ланцюг каталогу) ‖ `SYNC-14` ‖ `API-03/04/06` ‖ `ADMIN-03/06` ‖ `I18N-04/05/06` ‖ `NP-04` | |
| **W6** | **`SYNC-15`** 🎯 ‖ `API-05` ‖ `FE-06/07/08/10` ‖ `I18N-07` ‖ `SYNC-16` | |
| **W7** | `FE-09` → `FE-11` ‖ `FE-12` ‖ `REL-01/02/03` | |
| **W8** | `REL-04` 🎯 (UAT) | усе |

**Критичний шлях етапу 1:**
`INFRA-01 → MODEL-00 → MODEL-01 → MODEL-02 → MODEL-04 → SYNC-04 → SYNC-05 → SYNC-06 → SYNC-08 → SYNC-11 → API-02 → FE-05 → FE-07 → FE-09 → FE-11 → REL-01 → REL-04`
≈ **24 робочі дні послідовної роботи.** Решта (адмінка, i18n, НП, тести) кладеться на нього паралельно.

**Що НЕ можна паралелити:**
· `MODEL-02` — усі чекають на першу міграцію
· `SYNC-04 → 05 → 06` — один модуль, той самий файл
· `FE-01` перед будь-яким FE — інакше переверстка
· `MODEL-00` перед `MODEL-02` — інакше 14 розбіжностей поїдуть у міграцію №1

**Чотири передумови, які легко проґавити (винесено окремо, щоб не загубились):**
1. ⭐ **`SYNC-03` (normalize_model_name — порт скрипта Артура)** — передумова `SYNC-08`. Це чиста функція, її можна писати **найпершою**, ще до міграцій.
2. ⭐ **`SYNC-09` (габарити й вага з характеристик)** — передумова `NPC-01`. Без неї калькулятор НП **систематично бреше вниз у 5.5×**, і магазин попадає на гроші при **кожному** замовленні.
3. ⭐ **`NP-02` (нічний імпорт довідників НП у локальну БД)** — передумова `FE-09`. Без нього checkout **не існує**, а не «працює повільніше».
4. ⭐ **`FE-01` (дизайн-система в код)** — передумова **всього** фронтенду. Заблокована відкритим питанням **Q10**. Перефарбувати після — це переверстка.
