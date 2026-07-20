"""ASGI-точка входу.

⚠️ СТАТИКА В DEV. Локально бекенд піднімають через `uvicorn config.asgi:application`
   (див. Makefile), а не `manage.py runserver`. Це важлива різниця: автоматичну віддачу
   `/static/` робить САМЕ `runserver` — його підміняє `django.contrib.staticfiles`.
   Чистий ASGI-застосунок статику не віддає взагалі, whitenoise у local-middleware
   немає (він тільки в `heroku.py`), і `collectstatic` при DEBUG теж не рятує.

   Наслідок був такий: адмінка відкривалась голим HTML, а в консолі — 404 на кожному
   файлі Unfold (`app.js`, `alpine.js`, `chart.js`, `simplebar.js`…) плюс
   «Refused to apply style … MIME type ('text/html')» — бо на запит .css/.js
   Django повертав HTML-сторінку 404.

   `ASGIStaticFilesHandler` повертає ту саму поведінку, що й у `runserver`, і рівно
   тоді, коли вона доречна — при DEBUG. У проді статику віддає whitenoise (Heroku)
   або Caddy (VPS), тому обгортка там не вмикається.
"""

from __future__ import annotations

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

application = get_asgi_application()

from django.conf import settings  # noqa: E402 — тільки після get_asgi_application()

if settings.DEBUG:
    from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler

    application = ASGIStaticFilesHandler(application)
