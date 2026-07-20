"""Checkout — створення замовлення (ADR-014, ADR-017, ADR-021).

Це «остання миля», якої проєкту бракувало: моделі, адмінка, платежі, доставка й уся
фронтова форма були готові, а `POST /orders` не існував, тож жодне замовлення фізично
не потрапляло в БД.

Інваріанти, які тримає САМЕ цей модуль (а не клієнт і не модель):

  1. ⚡ СУМА РАХУЄТЬСЯ НА СЕРВЕРІ, з тієї самої функції, що й кошик —
     `catalog.services.cards.bulk_products()`. Не «схожою логікою», а буквально нею:
     два джерела цін розійшлись би рівно так само, як колись розійшлись синк і
     `recalc_usd_prices` (ADR-005). Клієнтський `expected_total` — не вхідні дані для
     розрахунку, а ТІЛЬКИ предмет звірки.

  2. ⚡ Розбіжність суми → 409, а не тиха згода. Синк міняє ціни 4×/добу, тож між
     переглядом кошика і сабмітом ціна цілком могла змінитись. Мовчки продати за старою
     ціною = збиток; мовчки за новою = обман покупця. Тому — явне перепідтвердження.

  3. ⚡ Ідемпотентність по `idempotency_key`. Подвійний клік, ретрай мережі або
     повторний сабміт форми повертають ТЕ САМЕ замовлення, а не друге. Гонку ловимо
     через `IntegrityError` на unique-полі, а не перевіркою «а чи є вже» — між SELECT
     і INSERT встигає вклинитись паралельний запит.

  4. ⚡ `OrderItem` зберігає ЕФЕКТИВНІ габарити (`effective_dims`, ADR-021) — те, що
     реально піде в Нову Пошту. Не сирі поля товару: інакше при відсутніх габаритах
     у ТТН поїде NULL, НП порахує лише за фактичною вагою і магазин недоплатить
     у ~5.5 раза (INTEGRATIONS §1.7).

  5. ⚡ Оплата частинами — лише якщо ВСІ позиції її підтримують, і перевіряється це по
     снапшоту на момент замовлення, а не по сьогоднішньому стану каталогу.

⚠️ СПОВІЩЕННЯ ШЛЮТЬСЯ ПІСЛЯ КОМІТУ, і тільки через чергу. Всередині `atomic` їх слати
   не можна двічі: (а) HTTP до Telegram/SMTP тримав би відкриту транзакцію, (б) при
   відкаті покупець уже отримав би лист про замовлення, якого немає. `on_commit` +
   Celery — той самий патерн, що і в решті проєкту.
"""

from __future__ import annotations

import logging
import re
import uuid
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction

from catalog.services.cards import bulk_products
from catalog.services.lang import normalize_lang
from delivery.services.dims import effective_dims
from orders.models import Order, OrderItem, OrderStatusHistory

log = logging.getLogger(__name__)

__all__ = [
    "CheckoutError",
    "PriceChanged",
    "UnavailableItems",
    "create_order",
    "normalize_phone",
]

#: Стільки позицій приймаємо за один checkout. Більше — це вже не роздріб, а помилка
#: або спроба навантажити сервер розрахунком габаритів на кожну одиницю.
MAX_ITEMS = 50

#: Скільки одиниць одного товару. Овербукінгу немає (ADR §5 «свідомо прийняті ризики»),
#: тому кількість не резервується — але й «300 холодильників» приймати безглуздо.
MAX_QTY_PER_ITEM = 99


class CheckoutError(RuntimeError):
    """Замовлення створити не можна (бізнес-правило)."""


class PriceChanged(CheckoutError):
    """Ціни змінились між переглядом кошика і сабмітом → 409.

    Несе рівно те, що потрібно фронту для повідомлення «ціни оновились»:
    які позиції змінились і скільки коштує кошик НАСПРАВДІ.
    """

    def __init__(self, changed_items: list[int], actual_total: Decimal) -> None:
        self.changed_items = changed_items
        self.actual_total = actual_total
        super().__init__("Ціни змінились")


class UnavailableItems(CheckoutError):
    """Товару вже немає / він деактивований — оформити не можна."""

    def __init__(self, unavailable_items: list[int]) -> None:
        self.unavailable_items = unavailable_items
        super().__init__("Частина товарів недоступна")


# ---------------------------------------------------------------------------
# Нормалізація телефону
# ---------------------------------------------------------------------------
_DIGITS_RE = re.compile(r"\D+")


def normalize_phone(raw: str) -> str:
    """Будь-який український запис → `+380XXXXXXXXX`.

    Модель свідомо не робить цього сама (див. коментар до `Order.phone`): нормалізація —
    рішення шару, який знає, що дані прийшли від людини. Менеджер має бачити один
    формат, а не `067…`, `+38 (067) …` і `38067…` вперемішку — інакше пошук по телефону
    в адмінці не знаходить замовлення, яке точно є.
    """
    digits = _DIGITS_RE.sub("", raw or "")

    if len(digits) == 9:  # 671234567
        digits = "380" + digits
    elif len(digits) == 10 and digits.startswith("0"):  # 0671234567
        digits = "38" + digits
    elif len(digits) == 11 and digits.startswith("80"):  # 80671234567
        digits = "3" + digits

    if len(digits) != 12 or not digits.startswith("380"):
        raise CheckoutError("Некоректний номер телефону")

    return "+" + digits


# ---------------------------------------------------------------------------
# Створення замовлення
# ---------------------------------------------------------------------------
def create_order(payload: Any, *, lang: str = "uk", meta: dict[str, Any] | None = None) -> Order:
    """Створити замовлення з payload'а checkout-форми.

    `payload` — pydantic-схема `orders.schemas.CreateOrderIn` (звертаємось по атрибутах).
    `meta` — необов'язковий контекст запиту: `ip`, `user_agent`, `utm`.

    Кидає `PriceChanged` (409), `UnavailableItems` (409) або `CheckoutError` (400).
    """
    meta = meta or {}
    lang = normalize_lang(lang)

    # --- ідемпотентність: швидкий шлях -------------------------------------
    # Гонку це не закриває (закриває IntegrityError нижче), але економить весь
    # розрахунок на найчастішому випадку — повторному сабміті тієї самої форми.
    existing = Order.objects.filter(idempotency_key=payload.idempotency_key).first()
    if existing is not None:
        return existing

    if not payload.items:
        raise CheckoutError("Кошик порожній")
    if len(payload.items) > MAX_ITEMS:
        raise CheckoutError(f"Максимум {MAX_ITEMS} позицій у замовленні")
    for item in payload.items:
        if item.qty < 1 or item.qty > MAX_QTY_PER_ITEM:
            raise CheckoutError(f"Некоректна кількість (1–{MAX_QTY_PER_ITEM})")

    phone = normalize_phone(payload.phone)
    email = (payload.email or "").strip()

    # Той самий інваріант, що й CheckConstraint `order_online_needs_email` — але
    # з людською помилкою до запису, а не IntegrityError після створення платежу.
    if not email and payload.payment_method in (
        Order.PaymentMethod.ONLINE,
        Order.PaymentMethod.INSTALLMENT,
    ):
        raise CheckoutError("Email обов'язковий для онлайн-оплати та оплати частинами")

    # --- ЦІНИ: єдине джерело, те саме, що в кошику --------------------------
    preview = bulk_products([(i.id, i.qty) for i in payload.items], lang)

    if preview["unavailable_items"]:
        raise UnavailableItems(sorted(preview["unavailable_items"]))

    rows: list[dict[str, Any]] = preview["items"]
    if not rows:
        raise CheckoutError("Кошик порожній")

    subtotal: Decimal = preview["subtotal"]

    # --- звірка з тим, що бачив покупець ------------------------------------
    expected = _to_decimal(payload.expected_total)
    if expected is None:
        raise CheckoutError("Некоректна очікувана сума")

    if expected != subtotal:
        # ⚠️ Які саме позиції подорожчали, сервер сказати НЕ МОЖЕ: контракт
        #    (types.ts::CartPreviewRequestItem) — це `{id, qty}` без цін, тобто нам
        #    невідомо, що покупець бачив на екрані. Тому повертаємо весь кошик, а
        #    фронт просто перезавантажує його з /cart/preview і показує нову суму.
        #    Вигадувати тут «змінені позиції» означало б підсвітити випадкові рядки.
        raise PriceChanged([row["id"] for row in rows], subtotal)

    if (
        payload.payment_method == Order.PaymentMethod.INSTALLMENT
        and not preview["installment_allowed"]
    ):
        raise CheckoutError("Оплата частинами доступна лише якщо ВСІ товари її підтримують")

    # --- запис ---------------------------------------------------------------
    payment_status = (
        Order.PaymentStatus.PENDING
        if payload.payment_method in (Order.PaymentMethod.ONLINE, Order.PaymentMethod.INSTALLMENT)
        else Order.PaymentStatus.NOT_REQUIRED
    )

    products = {row["id"]: row for row in rows}
    product_objs = _load_products(list(products))

    try:
        with transaction.atomic():
            order = Order.objects.create(
                number=Order.generate_number(),
                idempotency_key=payload.idempotency_key,
                status=Order.Status.NEW,
                last_name=payload.last_name.strip(),
                first_name=payload.first_name.strip(),
                phone=phone,
                email=email,
                comment=(payload.comment or "").strip(),
                delivery_method=payload.delivery_method,
                np_city_ref=(payload.np_city_ref or "").strip(),
                np_city_name=(payload.np_city_name or "").strip(),
                np_settlement_ref=(payload.np_settlement_ref or "").strip(),
                np_warehouse_ref=(payload.np_warehouse_ref or "").strip(),
                np_warehouse_name=(payload.np_warehouse_name or "").strip(),
                np_service_type=_service_type(payload.delivery_method),
                delivery_address=(payload.delivery_address or "").strip(),
                payment_method=payload.payment_method,
                payment_status=payment_status,
                subtotal=subtotal,
                discount=Decimal("0"),
                total=subtotal,
                utm=meta.get("utm") or {},
                ip=meta.get("ip"),
                user_agent=(meta.get("user_agent") or "")[:400],
            )

            OrderItem.objects.bulk_create(
                [_build_item(order, row, product_objs.get(row["id"])) for row in rows]
            )

            OrderStatusHistory.objects.create(
                order=order,
                from_status="",
                to_status=Order.Status.NEW,
                comment="Створено покупцем на сайті",
            )

            # ⚠️ ПІСЛЯ коміту, не всередині. Всередині — це HTTP до Telegram/SMTP з
            #    відкритою транзакцією і лист про замовлення, якого не існує при відкаті.
            transaction.on_commit(lambda: _schedule_notifications(order.pk))

    except IntegrityError:
        # Паралельний запит з тим самим idempotency_key виграв гонку — віддаємо його
        # замовлення. Це не помилка, це рівно та поведінка, заради якої ключ і існує.
        duplicate = Order.objects.filter(idempotency_key=payload.idempotency_key).first()
        if duplicate is not None:
            log.info("Checkout: гонка по idempotency_key, віддаю наявне %s", duplicate.number)
            return duplicate
        raise

    log.info("Замовлення %s створено на суму %s", order.number, order.total)
    return order


# ---------------------------------------------------------------------------
# Допоміжне
# ---------------------------------------------------------------------------
def _to_decimal(raw: Any) -> Decimal | None:
    try:
        return Decimal(str(raw))
    except (ArithmeticError, ValueError, TypeError):
        return None


def _load_products(ids: list[int]) -> dict[int, Any]:
    """Моделі товарів — потрібні `effective_dims()`, якій мало словника з preview.

    Тягнемо `category` одразу: без неї фолбек габаритів на `Category.default_*`
    зробив би окремий запит на КОЖНУ позицію.
    """
    from catalog.models import Product

    return {p.pk: p for p in Product.objects.select_related("category").filter(pk__in=ids)}


def _build_item(order: Order, row: dict[str, Any], product: Any) -> OrderItem:
    """Позиція-снапшот з ефективними габаритами.

    `line_total` рахуємо явно: `bulk_create` не викликає `save()`, а CheckConstraint
    `oi_line_total_matches` відхилить рядок, де сума не збігається з price × quantity.
    """
    price: Decimal = row["price"]
    qty: int = row["qty"]

    dims = effective_dims(product) if product is not None else None

    return OrderItem(
        order=order,
        product_id=row["id"],
        sku=row["sku"],
        name=row["name"],
        price=price,
        quantity=qty,
        line_total=price * qty,
        image_url=row.get("main_image_url") or "",
        installment_available=bool(row.get("installment_available")),
        weight_kg=dims.weight_kg if dims else None,
        volume_m3=dims.volume_m3 if dims else None,
        dims_source=dims.source if dims else "",
    )


def _service_type(delivery_method: str) -> str:
    """ServiceType Нової Пошти під обраний спосіб доставки.

    ⚠️ Має відповідати РЕАЛЬНО обраній точці. Обрали поштомат, а послали
    `WarehouseWarehouse` — недорахували на кожному замовленні (INTEGRATIONS §1.7).
    """
    # dict[str, str], а не за enum-ключами: `delivery_method` приходить із JSON рядком,
    # і словник, ключований TextChoices, не знайшов би в ньому нічого.
    mapping: dict[str, str] = {
        str(Order.DeliveryMethod.NP_WAREHOUSE): "WarehouseWarehouse",
        str(Order.DeliveryMethod.NP_POSTOMAT): "WarehousePostomat",
        str(Order.DeliveryMethod.NP_COURIER): "WarehouseDoors",
    }
    return mapping.get(delivery_method, "")


def _schedule_notifications(order_id: int) -> None:
    """Поставити сповіщення в чергу. Ніколи не ламає замовлення.

    Замовлення вже закомічене. Якщо брокер недоступний — це привід для алерту в лог,
    а не для 500 покупцеві, який щойно оформив покупку.
    """
    try:
        from orders.tasks import notify_customer, notify_new_order

        notify_new_order.delay(order_id)
        notify_customer.delay(order_id)
    except Exception:
        log.exception("Не вдалось поставити сповіщення для замовлення %s", order_id)


def new_idempotency_key() -> uuid.UUID:
    """Ключ для викликів із бекенду (тести, адмінка, імпорт)."""
    return uuid.uuid4()
