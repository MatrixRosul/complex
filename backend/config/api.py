"""Точка збірки Django Ninja API.

Роутери застосунків підключаються тут. Фронтенд — Next.js, тож Django віддає лише JSON + адмінку.

⚠️ CSRF тут НЕ вмикаємо (в Ninja 1.x він і так застосовується лише до cookie-автентифікації,
   якої в нас немає). Це ПУБЛІЧНЕ API без сесій: особистого кабінету покупця немає в принципі,
   а POST /products/bulk — це «порахуй ціни за цим списком id», а не зміна стану. Коли з'явиться
   POST /orders, його захистить ідемпотентний ключ + rate-limit, а не CSRF: його теж викликає
   fetch з іншого походження (Next.js SSR), для якого cookie-токен недоступний.
"""

from __future__ import annotations

from ninja import NinjaAPI

api = NinjaAPI(
    title="Complex API",
    version="1.0.0",
    description="Публічне API інтернет-магазину Complex",
    docs_url="/docs",
    urls_namespace="api-v1",
)


@api.get("/healthz", tags=["service"], auth=None)
def healthz(request):
    """Ліфнес-проба для Caddy/Docker: процес живий і Django піднявся."""
    return {"status": "ok"}


# --- Роутери застосунків ---
# ⚠️ Обидва монтуються в КОРІНЬ /api/v1/, а не під власними префіксами: шляхи вже задані
#    контрактом (ТЗ + frontend/src/lib/api/http.ts) і містять власні префікси
#    (/catalog/…, /products/…, /cms/…). Додатковий префікс тут дав би /api/v1/catalog/catalog/….
api.add_router("/", "catalog.api.router")
api.add_router("/cms/", "cms.api.router")

# Ці два оголошують шляхи БЕЗ власного префікса (/areas, /quote, /create, /liqpay/callback),
# тому префікс задається тут — на відміну від catalog/cms вище.
api.add_router("/delivery/", "delivery.api.router")
api.add_router("/payments/", "payments.api.router")
api.add_router("/assistant/", "assistant.api.router")

# TODO: orders/api.py ще не написаний — без нього не можна оформити замовлення (checkout).
# api.add_router("/orders/", "orders.api.router")
