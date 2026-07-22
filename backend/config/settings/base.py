"""
Complex — базові налаштування.

Спільне для всіх оточень. Конкретика — у local.py / prod.py.
Усе, що залежить від оточення, читається з env через django-environ (див. .env.example).

⚠️ БАЗА: PostgreSQL 14 (не 16!).  Наслідки, які треба тримати в голові:
   • UniqueConstraint(nulls_distinct=False) — потребує PG15, НЕ використовувати.
     Замість нього — часткові унікальні обмеження (як у SYNC.md §7).
   • GeneratedField — на PG14 лише STORED і лише з IMMUTABLE-виразів; ані ціна
     (complex_price_uah з зовнішнім курсом), ані search_vector з нашим стемером під це не підходять.
   • MERGE — PG15. Живемо на INSERT ... ON CONFLICT.
   Детальні обхідні шляхи — у docstring кожного models.py.
"""

from __future__ import annotations

from pathlib import Path

import environ
from celery.schedules import crontab
from django.urls import reverse_lazy

# ---------------------------------------------------------------------------
# Шляхи та env
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # …/backend

env = environ.Env()
env.read_env(BASE_DIR / ".env")  # у проді змінні приходять з compose/секретів, файл опційний

# ---------------------------------------------------------------------------
# Основне
# ---------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-key-change-me")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

SITE_URL = env("SITE_URL", default="http://localhost:3000")
ADMIN_URL = env("ADMIN_URL", default="admin/").lstrip("/")  # НЕ /admin/ у проді (ARCHITECTURE §5)

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
ROOT_URLCONF = "config.urls"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Застосунки
#   ⚠️ ПОРЯДОК КРИТИЧНИЙ:
#     modeltranslation — ПЕРЕД django.contrib.admin (патчить ModelAdmin)
#     unfold           — ПЕРЕД django.contrib.admin (підміняє шаблони адмінки)
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    # i18n-переклад моделей — строго перед admin
    "modeltranslation",
    # Unfold — строго перед django.contrib.admin
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.inlines",
    "unfold.contrib.import_export",
    "unfold.contrib.simple_history",
    # Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "django.contrib.sitemaps",
    # Третя сторона
    "django_celery_beat",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "django_otp.plugins.otp_static",
    "axes",
    "corsheaders",
    "storages",
    # Наші застосунки
    "core",
    "catalog",
    "sync",
    "orders",
    "delivery",
    "payments",
    "cms",
    "feeds",
    "translation",
    "assistant",
    "aispecs",
]

# ---------------------------------------------------------------------------
# Middleware
#   django_otp.middleware.OTPMiddleware — ПІСЛЯ AuthenticationMiddleware
#   axes.middleware.AxesMiddleware      — ОСТАННІЙ (вимога django-axes)
# ---------------------------------------------------------------------------
MIDDLEWARE = [
    # ⚠️ CorsMiddleware — ЯКОМОГА ВИЩЕ і строго ПЕРЕД будь-яким middleware, що вміє САМЕ
    #    згенерувати відповідь (CommonMiddleware з APPEND_SLASH, LocaleMiddleware з редіректом,
    #    SecurityMiddleware з SSL-редіректом). Інакше preflight-OPTIONS отримає редірект/405
    #    БЕЗ заголовків Access-Control-Allow-* — і браузер заблокує запит, хоча curl бачить 200.
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    # Одразу ПІСЛЯ LocaleMiddleware: пінить адмінку на uk, перекриваючи вибір за
    # Accept-Language. Порядок критичний — до Locale не спрацює. Див. docstring middleware.
    "core.middleware.ForceAdminLanguageMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",
]

# ---------------------------------------------------------------------------
# CORS
#   Фронт (Next.js) живе на ІНШОМУ origin: localhost:3001 у деві, окремий домен у проді.
#   SSR-запити йдуть сервер-до-сервера і CORS не потребують — а от кошик, «обране» і
#   порівняння тягнуть POST /products/bulk ПРЯМО З БРАУЗЕРА. Без цих заголовків браузер
#   блокує відповідь ще до того, як її побачить JS: сторінка показує «Щось пішло не так»,
#   хоча curl тим самим URL віддає 200. Саме тому баг не ловився голим curl.
#
#   Список origin'ів — БІЛИЙ, не «*»: API публічне на читання, але дозволяти будь-якому
#   сайту слати запити від імені відвідувача ми не хочемо.
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
    ],
)
# Кабінету/сесій у покупця немає — cookie через CORS не ходять, тому й credentials не треба.
CORS_ALLOW_CREDENTIALS = False
# Тільки API. Адмінка й медіа не мають бути доступні крос-origin JS-ом.
CORS_URLS_REGEX = r"^/api/.*$"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# База даних — PostgreSQL 14
# ---------------------------------------------------------------------------
# ⚠️ Ім'я БД `nisa` (і тестової `test_nisa`) — ІСТОРИЧНЕ, від старої назви проєкту.
#    Свідомо НЕ перейменовуємо: це зламало б усі наявні локальні оточення, DATABASE_URL,
#    станзу pgBackREST і бекап-репозиторій. Об'єкти ВСЕРЕДИНІ БД (complex_price_uah(),
#    complex_log_price(), ru_complex) перейменовано міграціями core/0003 і catalog/0004.
DATABASES = {
    "default": env.db_url(
        "DATABASE_URL",
        default="postgres://maxrosul@localhost:5432/nisa",
    ),
}
DATABASES["default"]["ATOMIC_REQUESTS"] = False
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

# ---------------------------------------------------------------------------
# Redis: db0 — broker (noeviction!), db1 — кеш (allkeys-lru).  SYNC.md §8
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379")


def _redis_url(url: str) -> str:
    """`rediss://` (TLS) → додати `ssl_cert_reqs=none`.

    🔴 БЕЗ ЦЬОГО ПРОД ЛЕЖИТЬ. Heroku Redis віддає TLS-ендпоінт із САМОПІДПИСАНИМ
    сертифікатом, а redis-py за замовчуванням вимагає перевірки ланцюжка й падає з
    `SSL: CERTIFICATE_VERIFY_FAILED`. Оскільки сесії — `cached_db`, тобто ходять у кеш,
    ЛЕЖИТЬ УСЯ АДМІНКА: будь-яка сторінка під логіном віддає 500 (симптом виглядав як
    «падає сторінка /admin/sync/usdratechange», хоча та сторінка ні до чого).

    Перевірено на прод-дино: без параметра — CERTIFICATE_VERIFY_FAILED, з ним — ping OK.
    Значення саме `none` малими: redis-py приймає лише none/optional/required.
    На локальному `redis://` (без TLS) функція нічого не робить.
    """
    if not url.startswith("rediss://") or "ssl_cert_reqs=" in url:
        return url
    return f"{url}{'&' if '?' in url else '?'}ssl_cert_reqs=none"


CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": _redis_url(env("REDIS_CACHE_URL", default=f"{REDIS_URL}/1")),
        "KEY_PREFIX": "complex",
    },
}

SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
SESSION_COOKIE_AGE = 60 * 60 * 8  # 8 год (ARCHITECTURE §5)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# ---------------------------------------------------------------------------
# Автентифікація
#   AxesStandaloneBackend — ПЕРШИЙ у списку (інакше лок не спрацює)
# ---------------------------------------------------------------------------
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- django-axes: лок після 5 невдалих спроб ---
AXES_ENABLED = env.bool("AXES_ENABLED", default=True)
AXES_FAILURE_LIMIT = env.int("AXES_FAILURE_LIMIT", default=5)
AXES_COOLOFF_TIME = 1  # година
AXES_LOCKOUT_PARAMETERS = ["ip_address", ["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_TEMPLATE = None
AXES_BEHIND_REVERSE_PROXY = env.bool("AXES_BEHIND_REVERSE_PROXY", default=False)
AXES_IPWARE_PROXY_COUNT = 1 if AXES_BEHIND_REVERSE_PROXY else None
AXES_IPWARE_META_PRECEDENCE_ORDER = ["HTTP_X_FORWARDED_FOR", "REMOTE_ADDR"]

# --- django-ninja throttling: скільки проксі-хопів перед нами ---
# 🔴 БЕЗ ЦЬОГО per-IP тротлінг (AnonRateThrottle на /assistant/chat) НЕ ЗАХИЩАЄ:
# за замовчуванням NINJA_NUM_PROXIES=None, і ninja бере як «особистість» увесь заголовок
# X-Forwarded-For — а його шле сам клієнт. Досить крутити XFF у кожному запиті, і ліміт
# 20/хв обходиться повністю (класична діра публічного LLM-ендпоінта = відкритий гаманець).
#   1 хоп (за Caddy у проді) → ninja бере ОСТАННІЙ запис XFF, тобто IP, який побачив Caddy,
#     ігноруючи будь-що, що клієнт дописав раніше в ланцюг → підміну знешкоджено.
#   0 (дев, без проксі)      → XFF ігнорується взагалі, ключ = REMOTE_ADDR.
# Дзеркалимо той самий прапорець, що й axes вище — щоб два лічильники IP не розійшлися.
# ⚠️ Якщо перед Caddy стане ще й Cloudflare-proxy (2 хопи) — підняти обидва (тут і axes).
NINJA_NUM_PROXIES = 1 if AXES_BEHIND_REVERSE_PROXY else 0

# --- django-otp: TOTP обов'язковий для всього staff ---
OTP_TOTP_ISSUER = "Complex"
# Обов'язковість OTP вмикається через AdminSite (OTPAdminSite) у core/admin.py —
# щоб не ламати `manage.py` та Ninja API.

# ---------------------------------------------------------------------------
# i18n / l10n.  uk — дефолт, ru — обов'язково вже в MVP.
#
# ⚠️ ДВІ РІЗНІ ДВОМОВНОСТІ, які легко сплутати:
#   · САЙТ — uk + ru, вимога замовника (INPUTS §1). Працює через modeltranslation: це про
#     ДАНІ (name_uk / name_ru), і LANGUAGES/LocaleMiddleware нижче потрібні саме йому.
#   · АДМІНКА — ТІЛЬКИ УКРАЇНСЬКА, свідомо (рішення 17.07.2026). gettext у проєкті не
#     використовується: всі підписи — український хардкод. Тому LOCALE_PATHS тут НЕМАЄ —
#     раніше він вказував на backend/locale/, якої не існує, і створював враження, що
#     переклад UI налаштований. Каркас Django admin українською дають вбудовані каталоги
#     самого Django. Див. UNFOLD["SHOW_LANGUAGES"] нижче — там повне обґрунтування.
#     Якщо колись знадобиться ru-інтерфейс — це не «увімкнути прапорець», а окрема робота:
#     обгорнути ~300 рядків у gettext, завести locale/, ПЛЮС перекласти сам Unfold.
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "uk"
LANGUAGES = [
    ("uk", "Українська"),
    ("ru", "Русский"),
]

USE_I18N = True
USE_L10N = True
USE_TZ = True
TIME_ZONE = "Europe/Kyiv"

# --- django-modeltranslation ---
MODELTRANSLATION_LANGUAGES = ("uk", "ru")
MODELTRANSLATION_DEFAULT_LANGUAGE = "uk"
MODELTRANSLATION_FALLBACK_LANGUAGES = ("uk",)
# RU показуємо тільки якщо переклад реально є; порожній рядок → фолбек на uk.
MODELTRANSLATION_AUTO_POPULATE = False
MODELTRANSLATION_ENABLE_FALLBACKS = True

# Product.search_vector — [tr] (search_vector_uk / search_vector_ru, ADR-003/ADR-009).
# SearchVectorField немає у SUPPORTED_FIELDS modeltranslation, тому вносимо його явно.
# Без цього рядка `django.setup()` падає з ImproperlyConfigured ще на реєстрації catalog.
MODELTRANSLATION_CUSTOM_FIELDS = ("SearchVectorField",)

# ---------------------------------------------------------------------------
# Статика та медіа (S3-сумісний Cloudflare R2 через django-storages)
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [d for d in [BASE_DIR / "static"] if d.exists()]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Параметри R2 читаються тут, а вмикаються у prod.py (або local.py при USE_R2=True).
AWS_ACCESS_KEY_ID = env("R2_ACCESS_KEY_ID", default="")
AWS_SECRET_ACCESS_KEY = env("R2_SECRET_ACCESS_KEY", default="")
AWS_STORAGE_BUCKET_NAME = env("R2_BUCKET", default="")
AWS_S3_ENDPOINT_URL = env("R2_ENDPOINT_URL", default="")
AWS_S3_CUSTOM_DOMAIN = env("R2_PUBLIC_DOMAIN", default="")  # cdn.complex.ua
AWS_S3_REGION_NAME = env("R2_REGION", default="auto")
AWS_S3_SIGNATURE_VERSION = "s3v4"
AWS_S3_ADDRESSING_STYLE = "virtual"
AWS_DEFAULT_ACL = None  # R2 не підтримує ACL
AWS_QUERYSTRING_AUTH = False
AWS_S3_FILE_OVERWRITE = False
AWS_S3_OBJECT_PARAMETERS = {"CacheControl": "public, max-age=31536000, immutable"}

# Окремий бакет для сирих знімків прогонів (SYNC.md §2, крок 7)
R2_SNAPSHOTS_BUCKET = env("R2_SNAPSHOTS_BUCKET", default="sync-snapshots")

# ---------------------------------------------------------------------------
# Celery.  Черги: sync (-c1), images (-c2), ai (-c1), default (-c4)
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=f"{REDIS_URL}/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=f"{REDIS_URL}/2")

# Той самий самопідписаний сертифікат Heroku Redis б'є і по Celery — але тут НЕ через
# query-параметр: kombu історично чекає на CERT_* і на 'none' може впасти. Явні
# налаштування Celery надійніші й не залежать від його версії.
if CELERY_BROKER_URL.startswith("rediss://"):
    import ssl as _ssl

    CELERY_BROKER_USE_SSL = {"ssl_cert_reqs": _ssl.CERT_NONE}
if CELERY_RESULT_BACKEND.startswith("rediss://"):
    import ssl as _ssl

    CELERY_REDIS_BACKEND_USE_SSL = {"ssl_cert_reqs": _ssl.CERT_NONE}

CELERY_TIMEZONE = TIME_ZONE  # Europe/Kyiv — інакше '0 8 * * *' поїде в UTC (SYNC.md §8)
CELERY_ENABLE_UTC = True
DJANGO_CELERY_BEAT_TZ_AWARE = True

CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_TIME_LIMIT = 900
CELERY_TASK_SOFT_TIME_LIMIT = 840
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_RESULT_EXTENDED = True
CELERY_TASK_TRACK_STARTED = True
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 3600}

# ⚠️ ІМЕНА ЗАДАЧ ТУТ — ЦЕ РЯДКИ, І НІХТО ЇХ НЕ ПЕРЕВІРЯЄ.
#    Задачі фото/денормалізації живуть у `sync.tasks`, а не в `catalog.tasks` (там лише
#    recalc_prices). Маршрут на неіснуючу `catalog.tasks.download_product_image` просто
#    ніколи не спрацьовував — черга бралась із декоратора (`queue="images"`), тому це
#    не було видно. Виправлено, щоб маршрут не брехав.
CELERY_TASK_ROUTES = {
    "sync.tasks.*": {"queue": "sync"},
    # Черга задана і в декораторі задачі (вона має пріоритет) — тут дублюємо явно.
    "sync.tasks.download_product_image": {"queue": "images"},
    "translation.tasks.*": {"queue": "ai"},
    # TODO: harvest_specs (AI-збагачення характеристик) ще не написана — маршрут з'явиться
    #       разом із задачею. Раніше тут висів "catalog.tasks.harvest_specs" — задачі з таким
    #       іменем не існує в жодному застосунку.
}

# Розклад (SYNC.md §8). Із django-celery-beat DatabaseScheduler ці записи
# синкаються в PeriodicTask при старті beat і далі редагуються в адмінці.
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

CELERY_BEAT_SCHEDULE = {
    # --- прайс: 4 рази на добу ---
    "sync-prices": {
        # фан-аут по всіх активних PriceSource (per-source advisory-lock усередині)
        "task": "sync.tasks.sync_all_prices",
        "schedule": crontab(minute=0, hour="8,12,16,20"),
        "options": {"queue": "sync", "expires": 600},
    },
    # --- довідники Нової Пошти ---
    "sync-np-refs": {
        "task": "delivery.tasks.sync_np_refs",
        "schedule": crontab(minute=0, hour=3),
        "options": {"queue": "default"},
    },
    # --- Hotline-фід ---
    "generate-hotline-feed": {
        "task": "feeds.tasks.generate_hotline_feed",
        "schedule": crontab(minute=0, hour="6,14,22"),
        "options": {"queue": "default"},
    },
    "heal-hotline-feed": {  # фід старший 24 год або файл зник → регенерація
        "task": "feeds.tasks.heal_hotline_feed",
        "schedule": crontab(minute=17),
        "options": {"queue": "default"},
    },
    # --- самозагоювання (SYNC.md §8, «heal / watchdog») ---
    #
    # ⚠️⚠️ ТУТ БУВ КОРІНЬ БАГА «ТОВАРИ БЕЗ ХАРАКТЕРИСТИК».
    #   Обидві heal-задачі були прописані як `catalog.tasks.*`, хоча живуть у `sync.tasks.*`.
    #   Задачі з такими іменами НЕ ІСНУЄ: beat справно публікував повідомлення, воркер так
    #   само справно відповідав «Received unregistered task of type catalog.tasks.heal_denorm»
    #   і викидав його. Тобто САМОЗАГОЮВАННЯ НЕ ПРАЦЮВАЛО ЖОДНОГО РАЗУ — мовчки, без падінь.
    #   Наслідок у базі: 278 товарів назавжди залишились із denorm_dirty=true й порожнім
    #   specs_json (порожня картка, порожнє порівняння, товар не знаходиться фільтрами).
    #   Ціна помилки — одна літера в неймспейсі; ціна невиявлення — половина каталогу.
    "heal-denorm": {  # гарантія проти втраченої on_commit-задачі
        "task": "sync.tasks.heal_denorm",
        "schedule": crontab(minute="*/5"),
        "options": {"queue": "default"},
    },
    "heal-images": {
        "task": "sync.tasks.heal_images",
        "schedule": crontab(minute=7),
        "options": {"queue": "default"},
    },
    # TODO: задачі `heal_usd_prices` не існує НІДЕ (ні в catalog.tasks, ні в sync.tasks).
    #       Запис лише засмічував брокер невідомим іменем. Найближчий родич —
    #       sync.tasks.recalc_usd_prices, але його семантика інша (перерахунок ПІСЛЯ зміни
    #       курсу, з аудитом UsdRateChange), і вішати його на щогодинний cron наосліп не можна.
    #       Повернути розклад, коли задача з'явиться.
    "reap-stale-runs": {  # SyncRun завис у RUNNING → FAILED + алерт
        "task": "sync.tasks.reap_stale_runs",
        "schedule": crontab(minute="*/10"),
        "options": {"queue": "default"},
    },
    # TODO: `core.tasks` не існує як модуль — watchdog «давно не було синку» ще не написаний.
    #       Запис публікував у брокер ім'я, яке воркер відкидав як unregistered.
    # --- платежі: вебхук міг не дійти (INTEGRATIONS §3) ---
    "reconcile-pending-payments": {
        "task": "payments.tasks.reconcile_pending_payments",
        "schedule": crontab(minute="*/5"),
        "options": {"queue": "default"},
    },
    # TODO: `catalog.tasks.refresh_category_counts` теж не існує. products_count зараз
    #       рахує API на льоту; коли з'явиться задача-агрегатор — повернути розклад.
    # --- прибирання ---
    "purge-sync-logs": {
        "task": "sync.tasks.purge_sync_logs",
        "schedule": crontab(minute=0, hour=4),
        "options": {"queue": "default"},
    },
    "purge-snapshots": {
        "task": "sync.tasks.purge_snapshots",
        "schedule": crontab(minute=10, hour=4),
        "options": {"queue": "default"},
    },
    "purge-delivery-quotes": {
        "task": "delivery.tasks.purge_delivery_quotes",
        "schedule": crontab(minute=20, hour=4),
        "options": {"queue": "default"},
    },
    "purge-webhook-events": {
        "task": "payments.tasks.purge_webhook_events",
        "schedule": crontab(minute=30, hour=4),
        "options": {"queue": "default"},
    },
    # TODO: `catalog.tasks.purge_price_history` і `catalog.tasks.purge_orphan_media` не
    #       написані (в catalog.tasks є лише recalc_prices). Обидва записи щотижня слали
    #       у брокер неіснуючі імена. Повернути розклад разом із самими задачами.
}

# ---------------------------------------------------------------------------
# Інтеграції (ключі — з env / docker secrets)
# ---------------------------------------------------------------------------
# Google Sheets: шлях до JSON сервісного акаунта (docker secret — файл, не env-рядок)
GOOGLE_SA_JSON_PATH = env("GOOGLE_SA_JSON_PATH", default="")
SHEETS_PRICE_SPREADSHEET_ID = env("SHEETS_PRICE_SPREADSHEET_ID", default="")
SHEETS_SPEC_SPREADSHEET_ID = env("SHEETS_SPEC_SPREADSHEET_ID", default="")

# Нова Пошта
NP_API_KEY = env("NP_API_KEY", default="")
NP_API_URL = env("NP_API_URL", default="https://api.novaposhta.ua/v2.0/json/")

# LiqPay
LIQPAY_PUBLIC_KEY = env("LIQPAY_PUBLIC_KEY", default="")
LIQPAY_PRIVATE_KEY = env("LIQPAY_PRIVATE_KEY", default="")
LIQPAY_SANDBOX = env.bool("LIQPAY_SANDBOX", default=True)

# Claude API (переклад uk→ru, збір характеристик)
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default="")
ANTHROPIC_TRANSLATION_MODEL = env("ANTHROPIC_TRANSLATION_MODEL", default="claude-sonnet-4-5")
ANTHROPIC_HARVEST_MODEL = env("ANTHROPIC_HARVEST_MODEL", default="claude-opus-4-5")
AI_MONTHLY_BUDGET_USD = env.float("AI_MONTHLY_BUDGET_USD", default=50.0)

# Hotline
HOTLINE_FIRM_ID = env("HOTLINE_FIRM_ID", default="")

# Next.js ISR-ревалідація
NEXT_REVALIDATE_URL = env("NEXT_REVALIDATE_URL", default="")
NEXT_REVALIDATE_SECRET = env("NEXT_REVALIDATE_SECRET", default="")

# Алерти / dead-man's switch
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID", default="")
HEALTHCHECKS_SYNC_URL = env("HEALTHCHECKS_SYNC_URL", default="")


# ---------------------------------------------------------------------------
# Unfold (адмінка).  Кольори — docs/research/DESIGN_SYSTEM.md:
#   акцент #15558F (light) / #4E9BE0 (dark), бренд-темний #0E3F68 = колір
#   вордмарку COMPLEX. Шкала 50–950 згенерована рівномірно навколо цих трьох
#   точок (hue 208°), тому 700/900/400 = саме вони, а не «схожий» відтінок.
# ---------------------------------------------------------------------------
def _nav(title: str, model: str, icon: str) -> dict:
    """Один пункт меню: посилання на список моделі (admin:<app>_<model>_changelist)."""
    return {"title": title, "icon": icon, "link": reverse_lazy(f"admin:{model}_changelist")}


def _admin_navigation() -> list[dict]:
    """
    Ручне меню адмінки — логічний порядок замість абеткового автосписку.

    Принцип: щоденне — зверху й розгорнуте; рідкісні словники та журнали — під
    згорнутими групами «Довідники» і «Журнали», щоб не займали висоту. Технічні
    застосунки (axes, otp, celery-beat) у меню взагалі не виводяться.
    """
    return [
        {
            "title": "Каталог",
            "items": [
                _nav("Товари", "catalog_product", "inventory_2"),
                _nav("Категорії", "catalog_category", "category"),
                _nav("Бренди", "catalog_brand", "local_offer"),
                _nav("Країни", "catalog_country", "public"),
            ],
        },
        {
            "title": "Замовлення і оплата",
            "items": [
                _nav("Замовлення", "orders_order", "shopping_bag"),
                _nav("Платежі", "payments_payment", "credit_card"),
                _nav("Вебхуки платежів", "payments_paymentwebhookevent", "webhook"),
            ],
        },
        {
            "title": "Синхронізація прайсу",
            "items": [
                _nav("Прогони синхронізації", "sync_syncrun", "sync"),
                _nav("Проблеми в даних", "sync_dataissue", "report_problem"),
                _nav("Джерела прайсів", "sync_pricesource", "request_quote"),
                _nav("Листи прайсу", "sync_pricesheet", "table_chart"),
                _nav("Таблиці характеристик", "sync_specsheet", "table_rows"),
            ],
        },
        {
            "title": "ШІ-характеристики",
            "items": [
                _nav("Черга на перегляд", "aispecs_specharvestjob", "auto_awesome"),
            ],
        },
        {
            "title": "Контент сайту",
            "items": [
                _nav("Статичні сторінки", "cms_staticpage", "description"),
                _nav("Новини", "cms_newspost", "article"),
                _nav("Банери", "cms_banner", "campaign"),
                _nav("Меню", "cms_menuitem", "menu"),
                _nav("Точки самовивозу", "cms_pickuppoint", "storefront"),
            ],
        },
        {
            "title": "Переклад",
            "items": [
                _nav("Черга перекладу", "translation_translationentry", "translate"),
                _nav("Глосарій", "translation_glossaryterm", "menu_book"),
            ],
        },
        # ⏸️ ФІДИ (HOTLINE) ПРИХОВАНІ З МЕНЮ — на прохання замовника, поки прайс-майданчик
        # не підключають. Це саме ХОВАННЯ НАВІГАЦІЇ, а не вимкнення: застосунок `feeds`
        # лишається в INSTALLED_APPS, генерація фіда за розкладом (celery beat) працює,
        # сторінки доступні за прямим URL. Щоб повернути в меню — розкоментувати блок.
        # {
        #     "title": "Фіди (Hotline)",
        #     "items": [
        #         _nav("Артефакти фідів", "feeds_feedartifact", "rss_feed"),
        #         _nav("Рубрикатор Hotline", "feeds_hotlinecategory", "account_tree"),
        #     ],
        # },
        {
            "title": "Налаштування",
            "items": [
                _nav("Налаштування магазину", "core_sitesettings", "settings"),
                _nav("Користувачі", "auth_user", "group"),
                _nav("Групи доступу", "auth_group", "admin_panel_settings"),
            ],
        },
        {
            "title": "Довідники",
            "collapsible": True,
            "items": [
                _nav("Характеристики", "catalog_attribute", "tune"),
                _nav("Варіанти характеристик", "catalog_attributeoption", "list"),
                _nav("Групи характеристик", "catalog_attributegroup", "folder"),
                _nav("Одиниці виміру", "catalog_unit", "straighten"),
                _nav("Групи варіантів", "catalog_variantgroup", "swap_horiz"),
                _nav("Групи супутніх товарів", "catalog_relatedgroup", "link"),
                _nav("Графік роботи", "core_workinghours", "schedule"),
                _nav("Області (НП)", "delivery_nparea", "map"),
                _nav("Міста (НП)", "delivery_npcity", "location_city"),
                _nav("Населені пункти (НП)", "delivery_npsettlement", "place"),
                _nav("Відділення (НП)", "delivery_npwarehouse", "local_shipping"),
                _nav("Розрахунки доставки", "delivery_deliveryquote", "calculate"),
            ],
        },
        {
            "title": "Журнали й аудит",
            "collapsible": True,
            "items": [
                _nav("Історія цін", "catalog_pricehistory", "history"),
                _nav("Історія статусів замовлень", "orders_orderstatushistory", "receipt_long"),
                _nav("Журнал синхронізації", "sync_synclogentry", "manage_search"),
                _nav("Зміни курсу USD", "sync_usdratechange", "currency_exchange"),
                _nav("Знімки цін (відкат)", "sync_productpricesnapshot", "restore"),
                _nav("Пропозиції постачальників", "sync_supplieroffer", "inventory"),
                _nav("Зображення редактора", "cms_editorimage", "image"),
                _nav("Сесії чату (ШІ)", "assistant_chatsession", "forum"),
                _nav("Повідомлення чату (ШІ)", "assistant_chatmessage", "chat"),
            ],
        },
    ]


UNFOLD = {
    "SITE_TITLE": "Complex",
    "SITE_HEADER": "Complex — панель керування",
    "SITE_SUBHEADER": "Побутова техніка",
    "SITE_SYMBOL": "kitchen",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    # 🔴 НЕ ВМИКАТИ. Це перемикач МОВИ ІНТЕРФЕЙСУ у випадайці користувача (він постить у
    #    set_language), а НЕ таби uk/ru на перекладних полях — ті дає TabbedTranslationAdmin
    #    із modeltranslation і від цього прапорця не залежать узагалі.
    #    Вмикати його шкідливо з двох причин (обидві перевірені 17.07.2026):
    #      1. Адмінка одномовна: gettext у проєкті не використовується, всі 161 підпис —
    #         український хардкод. Перемикання на ru давало мішанину — російський каркас
    #         Django + українське меню + англійські рядки самого Unfold (він робить 113
    #         викликів _(), але постачається БЕЗ locale-каталогів).
    #      2. Активна мова керує дескрипторами modeltranslation: у ru-режимі колонка «Назва»
    #         почала б мовчки показувати й редагувати name_ru замість name.
    "SHOW_LANGUAGES": False,
    "THEME": None,  # користувач сам обирає light/dark
    "BORDER_RADIUS": "8px",
    "COLORS": {
        "base": {  # нейтральний zinc — база дизайн-системи
            "50": "250 250 250",
            "100": "244 244 245",
            "200": "228 228 231",
            "300": "212 212 216",
            "400": "161 161 170",
            "500": "113 113 122",
            "600": "82 82 91",
            "700": "63 63 70",
            "800": "39 39 42",
            "900": "24 24 27",
            "950": "9 9 11",
        },
        "primary": {  # бренд-синій: 700 = #15558F (акцент), 900 = #0E3F68 (лого), 400 = #4E9BE0 (dark-тема)
            "50": "236 243 249",
            "100": "215 230 244",
            "200": "180 211 238",
            "300": "131 184 231",
            "400": "78 155 224",
            "500": "32 120 197",
            "600": "25 102 169",
            "700": "21 85 143",
            "800": "17 70 116",
            "900": "14 63 104",
            "950": "8 38 64",
        },
        "font": {
            "subtle-light": "107 114 128",
            "subtle-dark": "156 163 175",
            "default-light": "24 24 27",
            "default-dark": "250 250 250",
            "important-light": "17 24 39",
            "important-dark": "255 255 255",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        # Меню зібране ВРУЧНУ (нижче). Технічні застосунки (axes, otp, celery-beat)
        # свідомо не в списку — вони доступні за прямим URL, але не засмічують сайдбар.
        "show_all_applications": False,
        "navigation": _admin_navigation(),
    },
}

# ---------------------------------------------------------------------------
# Логування
# ---------------------------------------------------------------------------
LOG_LEVEL = env("DJANGO_LOG_LEVEL", default="INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {name} {process:d} {message}",
            "style": "{",
        },
        "simple": {"format": "{levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "django.db.backends": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "django.security.DisallowedHost": {"handlers": ["console"], "level": "ERROR"},
        "celery": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        # наші застосунки
        "sync": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "catalog": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "payments": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "delivery": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "feeds": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "translation": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "assistant": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}

# ---------------------------------------------------------------------------
# Sentry — опційний: немає DSN → не вмикається взагалі
# ---------------------------------------------------------------------------
SENTRY_DSN = env("SENTRY_DSN", default="")


def init_sentry(environment: str) -> None:
    """Вмикає Sentry, якщо є DSN. Викликається з local.py / prod.py."""
    if not SENTRY_DSN:
        return

    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    def scrub_pii(event, hint):
        """Скраб PII (ARCHITECTURE §5): телефон/пошта/адреса покупця не летять у Sentry."""
        pii_keys = {
            "phone",
            "email",
            "customer_phone",
            "customer_email",
            "customer_name",
            "recipient",
            "address",
            "np_recipient_phone",
            "card",
            "sender_card_mask2",
            "authorization",
            "cookie",
            "liqpay_private_key",
            "signature",
            "data",
        }

        def _walk(obj):
            if isinstance(obj, dict):
                return {
                    k: ("[scrubbed]" if k.lower() in pii_keys else _walk(v)) for k, v in obj.items()
                }
            if isinstance(obj, list):
                return [_walk(i) for i in obj]
            return obj

        return _walk(event)

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=environment,
        integrations=[
            DjangoIntegration(),
            CeleryIntegration(),
            LoggingIntegration(level=None, event_level=None),
        ],
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.05),
        send_default_pii=False,
        before_send=scrub_pii,
        release=env("GIT_SHA", default=""),
    )
