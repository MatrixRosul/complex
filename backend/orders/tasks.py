"""Замовлення — Celery-задачі (сповіщення).

Дві незалежні задачі на одну подію, і це навмисно:

  * `notify_new_order`  → АДМІНАМ у Telegram. Менеджер і так у телефоні (ADR-019),
    тому саме Telegram, а не пошта: лист про замовлення читають увечері, замовлення
    треба підтвердити за хвилини.
  * `notify_customer`   → ПОКУПЦЕВІ на email: «замовлення прийнято, ми зв'яжемось».

⚠️ ЧОМУ ДВІ ЗАДАЧІ, А НЕ ОДНА. Якщо SMTP лежить, менеджер усе одно має отримати
   замовлення; якщо Telegram лежить — покупець усе одно має отримати підтвердження.
   Одна задача зробила б із двох незалежних збоїв один спільний.

⚠️ ЖОДНА З НИХ НЕ ВПЛИВАЄ НА ЗАМОВЛЕННЯ. Воно вже закомічене (`on_commit` у
   `services/checkout.py`). Провал сповіщення — це рядок у лозі й ретрай, а не втрачений
   продаж.

🔴 SMS ПОКУПЦЕВІ ПОКИ НЕМАЄ. У проєкті не підключено жодного SMS-провайдера (ні в
   залежностях, ні в settings, ні в .env.example) — це окреме рішення й окремий договір.
   Тому підтвердження йде на email, а якщо покупець його не лишив (email опційний для
   накладеного платежу) — не йде взагалі, і єдиним каналом лишається дзвінок менеджера.
   Див. `notify_customer`.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.urls import NoReverseMatch, reverse

from core.alerts import escape, send_telegram, telegram_configured
from orders.models import Order

log = logging.getLogger(__name__)

__all__ = ["notify_customer", "notify_new_order"]


# ---------------------------------------------------------------------------
# Адмінам у Telegram
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    retry_backoff=True,
    queue="default",
    acks_late=False,  # дубль сповіщення = менеджер дзвонить покупцеві двічі
)
def notify_new_order(self, order_id: int) -> bool:
    """Повідомити адмінів про нове замовлення."""
    order = _load(order_id)
    if order is None:
        return False

    if not telegram_configured():
        log.info("Telegram не налаштований — замовлення %s без сповіщення", order.number)
        return False

    if send_telegram(_admin_message(order)):
        return True

    log.warning(
        "Замовлення %s: Telegram не відповів, спроба %s", order.number, self.request.retries + 1
    )
    raise self.retry(exc=RuntimeError("Telegram sendMessage failed"))


def _admin_message(order: Order) -> str:
    """Те, що менеджер має побачити, не відкриваючи комп'ютер.

    Порядок полів — за спаданням терміновості: спершу «кому дзвонити і на скільки»,
    і тільки потім деталі. Телефон окремим рядком у <code>, щоб тапом копіювався.
    """
    lines = [
        f"🛒 <b>Нове замовлення {escape(order.number)}</b>",
        "",
        f"💰 <b>{escape(order.total)} грн</b> — {escape(order.get_payment_method_display())}",
        f"👤 {escape(order.last_name)} {escape(order.first_name)}",
        f"📞 <code>{escape(order.phone)}</code>",
    ]

    if order.email:
        lines.append(f"✉️ {escape(order.email)}")

    lines += ["", f"🚚 {escape(order.get_delivery_method_display())}"]

    destination = _destination(order)
    if destination:
        lines.append(f"   {escape(destination)}")

    if order.payment_status != Order.PaymentStatus.NOT_REQUIRED:
        lines.append(f"💳 Оплата: {escape(order.get_payment_status_display())}")

    lines += ["", "<b>Позиції:</b>"]
    for item in order.items.all():
        lines.append(
            f"• {escape(item.name)}\n"
            f"  {item.quantity} × {escape(item.price)} = <b>{escape(item.line_total)} грн</b>"
        )

    if order.comment:
        lines += ["", f"💬 <i>{escape(order.comment)}</i>"]

    admin_link = _admin_url(order)
    if admin_link:
        lines += ["", f'<a href="{escape(admin_link)}">Відкрити в адмінці</a>']

    return "\n".join(lines)


def _destination(order: Order) -> str:
    """Куди везти — одним рядком, без порожніх «, ,»."""
    if order.delivery_method == Order.DeliveryMethod.PICKUP:
        return str(order.pickup_point) if order.pickup_point_id else "Самовивіз"

    parts = [order.np_city_name, order.np_warehouse_name or order.delivery_address]
    return ", ".join(part for part in parts if part)


def _admin_url(order: Order) -> str:
    """Пряме посилання на замовлення в адмінці.

    🔴 БАЗА — `PAYMENTS_PUBLIC_URL` (публічний URL БЕКЕНДА), а НЕ `SITE_URL`. Це той
       самий вибір, що й у `payments/providers/liqpay.py` для `server_url`, і причина та
       сама: `SITE_URL` — адреса вітрини на Next.js, а адмінка живе в Django. У проді це
       різні хости (фронт окремо, `complex-api` на Heroku окремо), тож посилання на
       `{SITE_URL}/admin/…` вело б у нікуди — на фронт, де такого роуту немає.
       Збігаються вони лише локально, тому помилку не було б видно в dev.

    ⚠️ `ADMIN_URL` у проді НЕ `/admin/` (ARCHITECTURE §5), тому шлях беремо з `reverse`,
       а не склеюємо руками. Якщо адмінка не змонтована (наприклад, у тестах) —
       повертаємо порожньо, а не падаємо посеред сповіщення.
    """
    base = (
        getattr(settings, "PAYMENTS_PUBLIC_URL", "") or getattr(settings, "SITE_URL", "") or ""
    ).rstrip("/")
    if not base:
        return ""
    try:
        path = reverse("admin:orders_order_change", args=[order.pk])
    except NoReverseMatch:
        return ""
    return f"{base}{path}"


# ---------------------------------------------------------------------------
# Покупцеві на пошту
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
    queue="default",
    acks_late=False,  # дубль листа «ми отримали ваше замовлення» гірший за його відсутність
)
def notify_customer(self, order_id: int) -> bool:
    """«Ми отримали ваше замовлення, невдовзі зв'яжемось».

    Мовчки нічого не робимо, якщо email порожній: він опційний для накладеного платежу
    (`Order.email.blank=True`), і це легальний стан, а не збій.
    """
    order = _load(order_id)
    if order is None:
        return False

    if not order.email:
        log.info("Замовлення %s без email — підтвердження не шлемо", order.number)
        return False

    sender = getattr(settings, "DEFAULT_FROM_EMAIL", "") or "noreply@complex.ua"

    try:
        sent = send_mail(
            subject=f"Замовлення {order.number} прийнято",
            message=_customer_message(order),
            from_email=sender,
            recipient_list=[order.email],
            fail_silently=False,
        )
    except Exception as exc:
        log.warning("Лист покупцеві по %s не пішов: %s", order.number, exc)
        raise self.retry(exc=exc) from exc

    return bool(sent)


def _customer_message(order: Order) -> str:
    """Плоский текст — свідомо.

    HTML-лист вимагає шаблону, інлайн-стилів і тесту в десятку клієнтів. Тут завдання
    рівно одне: підтвердити, що замовлення прийнято, і назвати номер. Це чесніше
    зробити текстом зараз, ніж красиво «колись».
    """
    lines = [
        f"Вітаємо, {order.first_name}!",
        "",
        f"Ми отримали ваше замовлення № {order.number} на суму {order.total} грн.",
        "Наш менеджер зв'яжеться з вами найближчим часом для підтвердження.",
        "",
        "Склад замовлення:",
    ]

    for item in order.items.all():
        lines.append(f"  • {item.name} — {item.quantity} × {item.price} = {item.line_total} грн")

    lines += [
        "",
        f"Разом: {order.total} грн",
        f"Доставка: {order.get_delivery_method_display()}",
        f"Оплата: {order.get_payment_method_display()}",
    ]

    base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    if base:
        lines += ["", f"Статус замовлення: {base}/ua/order/{order.public_token}"]

    lines += ["", "Дякуємо за покупку!"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Спільне
# ---------------------------------------------------------------------------
def _load(order_id: int) -> Order | None:
    """Замовлення з позиціями одним запитом.

    None замість винятку: задача могла дійти до воркера після того, як замовлення
    видалили. Це не привід для ретраїв — ретраїти нічого.
    """
    order = (
        Order.objects.filter(pk=order_id)
        .prefetch_related("items")
        .select_related("pickup_point")
        .first()
    )
    if order is None:
        log.warning("Замовлення %s не знайдено — сповіщення пропускаю", order_id)
    return order
