"""Сповіщення про замовлення — Telegram (адмінам) і email (покупцеві)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core import mail

from orders.models import Order
from orders.services.checkout import create_order
from orders.tasks import _admin_message, _customer_message, notify_customer, notify_new_order

pytestmark = pytest.mark.django_db


@pytest.fixture
def order(product_factory, payload_factory) -> Order:
    product = product_factory(price="15000.00")
    return create_order(
        payload_factory(
            items=[{"id": product.pk, "qty": 2}],
            expected_total="30000.00",
            comment="Подзвоніть після 18:00",
        )
    )


# ---------------------------------------------------------------------------
# Повідомлення адмінам
# ---------------------------------------------------------------------------
def test_admin_message_has_everything_manager_needs(order: Order) -> None:
    """Менеджер має зрозуміти замовлення з екрана телефону, не відкриваючи адмінку."""
    text = _admin_message(order)

    assert order.number in text
    assert "30000.00" in text
    assert "+380671234567" in text
    assert "Шевченко" in text
    assert "Тарас" in text
    assert "Відділення №1" in text
    assert "Ужгород" in text
    assert "Подзвоніть після 18:00" in text
    assert "Товар SKU-1" in text
    assert "Відкрити в адмінці" in text


def test_admin_message_escapes_html(product_factory, payload_factory) -> None:
    """🔴 Неекранований `<` ламає ВСЕ повідомлення: Telegram відповідає 400.

    Тобто товар із «<» у назві не «виглядав би трохи криво» — сповіщення про
    замовлення не прийшло б узагалі.
    """
    product = product_factory(price="100.00")
    product.name = 'Пральна <b>машина</b> & "Bosch"'
    product.save(update_fields=["name"])

    order = create_order(
        payload_factory(
            items=[{"id": product.pk, "qty": 1}],
            expected_total="100.00",
            comment="<script>alert(1)</script>",
        )
    )

    text = _admin_message(order)

    assert "&lt;b&gt;" in text
    assert "&lt;script&gt;" in text
    assert "&amp;" in text
    # Розмітка, яку ставимо МИ, лишається живою.
    assert "<b>Нове замовлення" in text


def test_admin_message_without_optional_fields(product_factory, payload_factory) -> None:
    """Без email і коментаря повідомлення не має розсипатись порожніми рядками."""
    product = product_factory(price="100.00")
    order = create_order(
        payload_factory(
            items=[{"id": product.pk, "qty": 1}],
            expected_total="100.00",
            email="",
            comment="",
            payment_method="cod",
        )
    )

    text = _admin_message(order)
    assert "✉️" not in text
    assert "💬" not in text


def test_notify_skipped_when_telegram_not_configured(order: Order, settings) -> None:
    """У dev токена немає — це штатний стан, а не привід ретраїти."""
    settings.TELEGRAM_BOT_TOKEN = ""
    assert notify_new_order(order.pk) is False


def test_notify_missing_order_does_not_retry() -> None:
    """Замовлення видалили, поки задача чекала в черзі — ретраїти нічого."""
    assert notify_new_order(999_999) is False


# ---------------------------------------------------------------------------
# Лист покупцеві
# ---------------------------------------------------------------------------
def test_customer_email_sent(order: Order) -> None:
    mail.outbox.clear()

    assert notify_customer(order.pk) is True
    assert len(mail.outbox) == 1

    message = mail.outbox[0]
    assert message.to == ["taras@example.com"]
    assert order.number in message.subject
    assert "зв'яжеться" in message.body
    assert str(order.public_token) in message.body


def test_customer_email_lists_items(order: Order) -> None:
    body = _customer_message(order)
    assert "Товар SKU-1" in body
    assert "30000.00" in body


def test_no_email_no_letter(product_factory, payload_factory) -> None:
    """Email опційний для накладеного платежу — це легальний стан, не збій."""
    product = product_factory(price="100.00")
    order = create_order(
        payload_factory(
            items=[{"id": product.pk, "qty": 1}],
            expected_total="100.00",
            email="",
            payment_method="cod",
        )
    )
    mail.outbox.clear()

    assert notify_customer(order.pk) is False
    assert len(mail.outbox) == 0


def test_customer_email_total_matches_order(order: Order) -> None:
    """Сума в листі — з замовлення, а не перерахована ще раз іншою логікою."""
    assert order.total == Decimal("30000.00")
    assert str(order.total) in _customer_message(order)
