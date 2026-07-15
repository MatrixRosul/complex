# Complex

Інтернет-магазин побутової техніки. Пишемо з нуля.

Дві речі, які визначають усю архітектуру і які варто знати до того, як щось відкривати:

1. **Каталог живе не в адмінці, а в Google Sheets.** Прайс редагують люди, синк ходить 4×/добу.
   Тому «серце» системи — не CRUD, а синхронізація з запобіжниками, знімками й відкотом.
2. **Особистого кабінету немає.** Кошик і порівняння — на клієнті, замовлення — гостьове.

---

## Стек

| Шар | Технологія |
|---|---|
| API | Django 5 + Django Ninja, Python 3.12, `uv` |
| БД | PostgreSQL (локально 14, у проді 16) |
| Черги | Celery 5 + Redis |
| Адмінка | Django Admin + django-unfold + django-otp + django-axes |
| i18n | django-modeltranslation — **uk (дефолт) + ru, обидві живі з MVP** |
| Фронт | Next.js App Router + TypeScript + Tailwind + shadcn/ui |
| Файли | Cloudflare R2 (S3-сумісний), окремий бакет під бекапи |
| Деплой | Docker Compose + Caddy на VPS, GitHub Actions → GHCR |
| Бекапи | pgBackRest (base + WAL) → окремий бакет з окремим токеном |

---

## Локальний запуск

### 0. Що має бути на машині

```bash
python3.12 --version     # 3.12.x   (системний python3 — 3.9, він НЕ підходить)
uv --version
psql --version           # 14.x
redis-cli ping           # PONG
node --version           # 22.x  (потрібен лише для фронту)
```

PostgreSQL і Redis мають бути підняті локально (без докера).
Порожня база: `createdb nisa`.

> **Чому база зветься `nisa`, а проєкт — Complex?** Назву проєкту змінено 14.07.2026
> (NISA → Complex). Ім'я БД (`nisa`, тестова — `test_nisa`) і станзу pgBackREST свідомо
> лишено старими: rename зламав би наявні локальні оточення й бекап-репозиторій.
> Усе всередині БД уже перейменовано міграціями `core/0003`, `catalog/0004`:
> `complex_price_uah()`, `complex_log_price()`, TS-конфіг `ru_complex`.

### 1. Підняти бекенд

```bash
make setup       # uv sync + backend/.env з прикладу (+ npm ci, якщо фронт уже є)
make migrate     # міграції; тут же створюються розширення PG і функція complex_price_uah()
make seed        # демо-дані (seed_demo)
make superuser   # адмін для входу в адмінку
make dev         # http://localhost:8000
```

Адмінка — за шляхом із `ADMIN_URL` у `backend/.env` (локально `admin/`, у проді — довгий
непередбачуваний префікс: Django Admin на публічному домені — головна поверхня атаки).

### 2. Черги (за потреби)

Найпростіше — `CELERY_TASK_ALWAYS_EAGER=True` у `backend/.env`: задачі виконуються
синхронно, воркер не потрібен. Коли треба по-справжньому — двома терміналами:

```bash
make worker
make beat
```

### 3. Фронтенд

```bash
make dev-front   # http://localhost:3000
```

> Каталогу `frontend/` ще немає — він з'являється на треку **FE** (див. `docs/ROADMAP.md`).
> Усе, що його стосується (Dockerfile, CI-джоб, сервіс у compose), уже написано і чекає.

### Решта команд

`make` без аргументів покаже повний список.

---

## Структура репо

```
backend/            Django: config/ + застосунки
  config/           settings (base/local/prod), urls, celery, api (Ninja)
  core/             TimeStampedModel, SEO-міксини, advisory-locks, uk-стемер
  catalog/          Product, Category, EAV-атрибути, денорм, фасети, пошук
  sync/             Google Sheets → SupplierOffer → проєкція в Product
  orders/           гостьовий checkout
  payments/         LiqPay + ідемпотентні вебхуки
  delivery/         довідники Нової Пошти + калькулятор
  feeds/            генератор Hotline XML
  cms/  siteconfig/ банери, новини, статичні сторінки, налаштування

frontend/           Next.js (з'явиться на треку FE)

infra/              ПРОД. Тут не працюють локально.
  docker-compose.yml  весь стек: postgres, redis, web×2, worker×3, beat, frontend, caddy, backup
  Dockerfile.backend  multi-stage, uv, non-root
  Dockerfile.frontend Next.js standalone
  Dockerfile.caddy    Caddy + модуль rate-limit (у ванільному його немає)
  Caddyfile           TLS, reverse proxy, статика, фід Hotline
  backup/             pgBackRest: бекапи, шифрування, restore-drill
  images.env          дайджести образів = механізм відкату
  scripts/            entrypoint бекенду, пінінг образів, ініціалізація секретів

docs/               ← читати ДО того, як писати код
.github/workflows/  ci.yml (lint, mypy, міграції на чистій БД, збірка фронту)
                    deploy.yml (GHCR → VPS, ручний запуск)
```

---

## Документація

Порядок читання для нового розробника:

| Документ | Про що |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | **Головний.** Компоненти, потоки даних, 28 ADR з обґрунтуванням і списком відкинутих варіантів |
| [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) | Повна модель даних — усі поля, індекси, обмеження |
| [`docs/SYNC.md`](docs/SYNC.md) | Синхронізація з Google Sheets: guard'и, знімки, відкат, heal-задачі |
| [`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md) | Нова Пошта, LiqPay, Hotline, Claude API |
| [`docs/reference/INPUTS.md`](docs/reference/INPUTS.md) | Підтверджені вхідні дані від замовника і пастки в них |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Треки й порядок робіт |
| [`docs/OPEN_QUESTIONS.md`](docs/OPEN_QUESTIONS.md) | Що ще не вирішено |

---

## Три речі, на яких легко обпектися

**PostgreSQL 14 локально, 16 у проді.** Тому нічого з PG15+ використовувати не можна.
Найгостріше — `UniqueConstraint(nulls_distinct=False)` з Django 5: він вимагає PG15,
і `migrate` у розробника просто впаде. Замість нього — два часткові unique-індекси
(ADR-016). CI ганяється на 16 і цієї помилки **не спіймає** — спіймає розробник.

**Фід Hotline мусить віддаватись по постійному URL з HTTP 200 — без редіректів, без
авторизації, без челенджів.** Hotline не публікує ні User-Agent, ні IP свого бота, тому
allowlist побудувати неможливо: шлях `/feeds/hotline.xml` має бути «тупим». Файл живе в
named volume (не в шарі контейнера — інакше `compose up -d` його зносить). Якщо бот не
забере фід 7 днів поспіль, **весь каталог знімається з майданчика**. Деплой перевіряє це
автоматично.

**Ціна рахується однією SQL-функцією `complex_price_uah()`, і тільки нею.** Python-двійник
існує лише для preview в адмінці й покритий parity-тестом. Причина в ADR-005: колись було
дві реалізації, і зміна курсу стирала націнку на всьому каталозі — тихо, без помилок.

---

## Прод

Розгортання, секрети, бекапи й відкат — в `infra/`. Коротко:

```bash
make secrets-init   # створити infra/secrets/* (на VPS)
make prod-config    # перевірити compose і Caddyfile, нічого не піднімаючи
make prod-up
make restore-drill  # довести, що бекап РЕАЛЬНО відновлюється
```

Деплой — `.github/workflows/deploy.yml`, **вручну** (`Run workflow`). Образи пінуються по
`sha256` в `infra/images.env`, тому відкат — це `git revert` одного файлу.
