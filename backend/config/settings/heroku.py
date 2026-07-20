"""ТИМЧАСОВИЙ хостинг на Heroku. НЕ прод.

Прод — це `infra/docker-compose.yml` + Caddy на VPS (`prod.py`), і він лишається таким.
Цей модуль існує лише тому, що на Heroku інша модель рантайму, і чотири речі з `prod.py`
там фізично не працюють:

1. **Статику нема кому віддавати.** У проді її роздає Caddy з named volume; на Heroku
   перед дино стоїть лише роутер, який у файли не вміє → whitenoise (роздає сам Django).
2. **TLS термінує роутер Heroku**, а не Caddy. `SECURE_PROXY_SSL_HEADER` з `prod.py`
   лишається чинним (роутер шле X-Forwarded-Proto), але http→https нема кому редіректити —
   тому це робить сам Django (у проді редірект робив Caddy, і там W008 гасився свідомо).
3. **Файлова система дино ЕФЕМЕРНА і в КОЖНОГО дино СВОЯ.** Наслідок не косметичний:
   `download_product_image` виконується на дино `worker`, а віддає фото дино `web` —
   різні файлові системи. Тому сховище тут ОБОВ'ЯЗКОВЕ і зовнішнє, і модуль ПАДАЄ
   НА СТАРТІ без нього, а не показує каталог без фото. Сховище — **Cloudinary, а не
   R2**: R2 не активується без прив'язаної до Cloudflare картки, а акаунт Cloudinary
   у замовника вже є. Прод (prod.py) лишається на R2 (див. нижче).
4. **Redis від Heroku має лише дві бази (0 і 1)** і самопідписаний TLS-сертифікат —
   `base.py` розкладав broker/кеш/результати по 0/1/2, і /2 тут не існує.

Фід Hotline (`/srv/feeds`) на Heroku теж ефемерний. Для тимчасового показу це не проблема:
на майданчик Hotline ми ще не подаємось, а сам фід перегенерується задачею.
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

from .prod import *
from .prod import INSTALLED_APPS, MIDDLEWARE, STORAGES, env

# ---------------------------------------------------------------------------
# Хости й HTTPS
# ---------------------------------------------------------------------------
# Роутер Heroku термінує TLS і ходить до дино по http — редіректить уже Django,
# а не Caddy. Тому знімаємо і глушник security.W008 з prod.py: тут перевірка доречна.
SECURE_SSL_REDIRECT = True
SILENCED_SYSTEM_CHECKS = []

# Роутер Heroku додає рівно один проксі-хоп перед дино.
AXES_IPWARE_PROXY_COUNT = 1

# ---------------------------------------------------------------------------
# Статика — whitenoise
# ---------------------------------------------------------------------------
# Строго ПІСЛЯ SecurityMiddleware і ПЕРЕД усім іншим (вимога whitenoise).
# CorsMiddleware лишається найпершим — причина в base.py, і вона тут не змінюється.
MIDDLEWARE = [
    *MIDDLEWARE[:2],  # corsheaders, SecurityMiddleware
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *MIDDLEWARE[2:],
]

STORAGES = {
    **STORAGES,
    "staticfiles": {
        # Compressed* — той самий Manifest, що й у проді (тобто зламане посилання так само
        # впаде на collectstatic, у білді), плюс gzip/brotli: Caddy тут немає, стискати нікому.
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# ---------------------------------------------------------------------------
# Медіа — Cloudinary (а НЕ R2, як у проді)
# ---------------------------------------------------------------------------
# ⚠️ ЄДИНЕ місце в проєкті, де медіа не на R2. Причина не технічна: R2 не активується
# без прив'язаної до Cloudflare картки, а акаунт Cloudinary у замовника вже є.
# prod.py (VPS) лишається на R2 — цей файл нічого там не змінює.
#
# Зовнішнє сховище тут ОБОВ'ЯЗКОВЕ, і мовчазний фолбек на файлову систему був би пасткою:
# `download_product_image` виконує дино worker, а віддає фото дино web — диски в них РІЗНІ
# й ефемерні. Виглядало б це як «синк не тягне фото», хоча синк відпрацював бездоганно.
# Тому падаємо на старті (як і prod.py з його обов'язковими змінними).
if not env("CLOUDINARY_URL", default=""):
    raise ImproperlyConfigured(
        "CLOUDINARY_URL не заданий. На Heroku диск дино ефемерний і в кожного дино свій, "
        "тому локальне сховище медіа тут не працює: фото, завантажені воркером, для web-дино "
        "не існують. Візьми CLOUDINARY_URL у дашборді Cloudinary (Product Environment "
        "Credentials) і задай через heroku config:set."
    )

# cloudinary_storage читає CLOUDINARY_URL з оточення сам; окремий конфіг-дікт не потрібен.
INSTALLED_APPS = [*INSTALLED_APPS, "cloudinary", "cloudinary_storage"]

STORAGES = {
    **STORAGES,
    # MediaCloudinaryStorage — для картинок. Решта (django-storages/S3 з prod.py) тут не діє.
    #
    # ⚠️ ПОБІЧНИЙ ЕФЕКТ, свідомо прийнятий: sync/services.py::store_raw_snapshot кладе
    #    у default_storage сирий знімок `.csv.gz`, а Cloudinary чекає картинку і його
    #    відкине. Прогін від цього НЕ падає — там `except` з warning'ом, бо форензика
    #    менш важлива за сам синк. Тобто на Heroku не буде сирих знімків, а відкат
    #    прогону лишається робочим: він живе в Postgres (sync_productpricesnapshot).
    "default": {"BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage"},
}

# ---------------------------------------------------------------------------
# Redis: Heroku Key-Value Store
# ---------------------------------------------------------------------------
# ⚠️ ДВІ ВІДМІННОСТІ ВІД base.py, обидві — вимушені і перевірені на живому аддоні.
#
# (а) БАЗ ЛИШЕ ДВІ. `SELECT 2` → "DB index is out of range". base.py розкладав
#     broker=/0, кеш=/1, результати=/2. Результати Celery у цьому проєкті НЕ ЧИТАЄ ніхто
#     (жодного AsyncResult/.get()), тому вони їдуть на /0 разом із broker'ом.
#
# (б) СЕРТИФІКАТ САМОПІДПИСАНИЙ. Дефолтна перевірка TLS падає з
#     "SSL: CERTIFICATE_VERIFY_FAILED" — Heroku не дає CA свого Key-Value Store.
#     `ssl_cert_reqs=none` вимикає ПЕРЕВІРКУ сертифіката (не саме шифрування: канал
#     лишається TLS). Це задокументований і єдиний робочий шлях для цього аддона.
#     ⚠️ Ціна: з'єднання беззахисне перед MITM усередині мережі Heroku. Для тимчасового
#     показу — прийнятно; у прод-VPS (prod.py) цього немає й бути не повинно.
REDIS_URL = env("REDIS_URL")
_REDIS_SSL = "?ssl_cert_reqs=none" if REDIS_URL.startswith("rediss://") else ""

CELERY_BROKER_URL = f"{REDIS_URL}/0{_REDIS_SSL}"

# Результати ВИМКНЕНО. base.py клав їх на /1, якої в цьому аддоні немає (див. (а)), але
# головне не це: result backend тримає ВЛАСНИЙ пул з'єднань, а ліміт тут — стіна (див. (в)).
# У проєкті результати не читає ніхто — жодного AsyncResult/.get(), тому втрачати нічого.
CELERY_RESULT_BACKEND = None
CELERY_TASK_IGNORE_RESULT = True

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": f"{REDIS_URL}/1{_REDIS_SSL}",
        "KEY_PREFIX": "complex",
        # ↓ див. «БЮДЖЕТ З'ЄДНАНЬ» нижче. OPTIONS Django віддає в ConnectionPool.from_url.
        "OPTIONS": {"max_connections": 3},
    },
}

# --- (в) БЮДЖЕТ З'ЄДНАНЬ -----------------------------------------------------
# ⚠️ План Mini має ЖОРСТКИЙ ліміт ~20 одночасних з'єднань, і ми в нього ВПЕРЛИСЯ:
#     rejected_connections: 36
# Проявляється це підступно — не помилкою «ліміт», а обривом на TLS-handshake:
#     redis.exceptions.ConnectionError: [SSL: UNEXPECTED_EOF_WHILE_READING]
# що читається як проблема з сертифікатом і відправляє шукати не туди. Redis просто
# закриває сокет, не встигнувши договорити TLS.
#
# Споживачі: web (2 воркери gunicorn × пул кешу) + celery (пул брокера × concurrency
# + result backend + кеш) + КОЖНЕ разове `heroku run`. Без стелі пули розростаються
# самі й з'їдають ліміт.
#
# ЗМІРЯНО, а не вгадано: воркер (concurrency=2, 4 черги, gossip/mingle, result backend)
# тримав 16 з ~20; після його зупинки лишалось 3. Тому стеля саме на ньому, а в Procfile —
# concurrency=1 і --without-gossip/--without-mingle/--without-heartbeat.
#
# У проді (prod.py) цього немає: там свій Redis без такої стелі, і черги розібрані
# на три окремі воркери.
CELERY_BROKER_POOL_LIMIT = 2
CELERY_REDIS_MAX_CONNECTIONS = 3
