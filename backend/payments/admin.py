"""
Платежі — адмінка.

🔴 ГРОШІ. Тут readonly ВСЕ, крім `status` — і навіть це виняток «на випадок ручного розбору»
   (провайдер завис, гроші прийшли на рахунок, менеджер закриває платіж вручну).
   `amount`, `reference`, `provider_invoice_id`, сирі payload'и — НЕ редагуються ніколи:
   це фінансовий слід, з яким звіряють виписку банку.

⚠️ Зміна статусу з адмінки НЕ проводить гроші і НЕ смикає провайдера. Вона лише виправляє
   НАШ запис. Реальні переходи робить `apply_payment_status()` з вебхука і reconcile —
   з чотирма запобіжниками (ідемпотентність, звірка суми, out-of-order, фінальність стану).

PaymentWebhookEvent — сирі вебхуки, readonly повністю. Це матеріал для розбору інцидентів:
`bad_signature` (можлива атака), `amount_mismatch` (🔴 гроші НЕ проводимо), `sandbox_in_prod`
(витік ключа). Видаляти їх руками не можна — є ретеншн 90 днів.
"""

from __future__ import annotations

import json

from django.contrib import admin
from django.http import HttpRequest
from django.urls import reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateTimeFilter
from unfold.decorators import display

from payments.models import Payment, PaymentWebhookEvent

STATUS_LABELS: dict[str, str] = {
    Payment.Status.CREATED: "default",
    Payment.Status.PENDING: "warning",
    Payment.Status.HELD: "info",
    Payment.Status.PAID: "success",
    Payment.Status.FAILED: "danger",
    Payment.Status.REVERSED: "danger",
    Payment.Status.REFUNDED: "info",
    Payment.Status.EXPIRED: "default",
}

RESULT_LABELS: dict[str, str] = {
    PaymentWebhookEvent.Result.APPLIED: "success",
    PaymentWebhookEvent.Result.DUPLICATE: "default",
    PaymentWebhookEvent.Result.STALE: "default",
    PaymentWebhookEvent.Result.FINAL_STATE: "default",
    PaymentWebhookEvent.Result.AMOUNT_MISMATCH: "danger",
    PaymentWebhookEvent.Result.BAD_SIGNATURE: "danger",
    PaymentWebhookEvent.Result.UNKNOWN_PAYMENT: "warning",
    PaymentWebhookEvent.Result.SANDBOX_IN_PROD: "danger",
    PaymentWebhookEvent.Result.ERROR: "danger",
}


def pretty_json(value, title: str = "payload") -> str:
    """Згорнутий JSON-в'ювер: payload'и провайдера довгі, і в розгорнутому вигляді нечитабельні."""
    if not value:
        return "—"
    dumped = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return format_html(
        '<details><summary style="cursor:pointer">Показати {} ({} символів)</summary>'
        '<pre style="white-space:pre-wrap;font-size:12px;margin-top:6px;max-height:420px;'
        'overflow:auto">{}</pre></details>',
        title,
        len(dumped),
        dumped,
    )


@admin.register(Payment)
class PaymentAdmin(ModelAdmin):
    list_display = (
        "created_at",
        "order_link",
        "provider",
        "amount_display",
        "status_badge",
        "paytype",
        "commission_display",
        "review_flag",
        "paid_at",
    )
    list_display_links = ("created_at", "order_link")
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("provider", ChoicesDropdownFilter),
        ("paytype", ChoicesDropdownFilter),
        "needs_bank_review",
        ("created_at", RangeDateTimeFilter),
    )
    list_filter_submit = True
    search_fields = (
        "reference",
        "provider_invoice_id",
        "order__number",
        "order__phone",
        "sender_card_mask2",
    )
    search_help_text = "Reference, ID платежу у провайдера, номер замовлення або телефон"
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("order",)

    fieldsets = (
        (
            "Платіж",
            {
                "description": (
                    "Зміна статусу тут виправляє НАШ запис і не проводить гроші. "
                    "Реальні переходи робить вебхук/reconcile."
                ),
                "fields": (
                    "order",
                    "provider",
                    "status",
                    ("amount", "currency"),
                    "reference",
                    "provider_invoice_id",
                    "payment_url",
                ),
            },
        ),
        (
            "Як заплатили",
            {
                "fields": (
                    ("paytype", "installment_count", "is_moment_part"),
                    "receiver_commission",
                    ("sender_card_mask2", "sender_card_bank"),
                )
            },
        ),
        (
            "Стан",
            {
                "fields": (
                    ("paid_at", "expires_at"),
                    ("needs_bank_review", "last_end_date", "last_polled_at"),
                    ("err_code", "error_message"),
                )
            },
        ),
        (
            "Сирі дані провайдера (редаговані за allowlist)",
            {
                "classes": ("collapse",),
                "fields": ("raw_request_view", "raw_response_view"),
            },
        ),
        ("Службове", {"classes": ("collapse",), "fields": (("created_at", "updated_at"),)}),
    )

    def get_readonly_fields(self, request: HttpRequest, obj=None) -> tuple:
        # 🔴 Усе, крім status. Це фінансовий слід, з яким звіряють виписку банку.
        editable = {"status"}
        return (
            *(f.name for f in self.model._meta.fields if f.name not in editable),
            "raw_request_view",
            "raw_response_view",
        )

    def has_add_permission(self, request: HttpRequest) -> bool:
        # Платіж створює checkout / провайдер, а не людина.
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    # -- колонки -------------------------------------------------------------

    @display(description="Замовлення", ordering="order__number")
    def order_link(self, obj: Payment) -> str:
        url = reverse("admin:orders_order_change", args=(obj.order_id,))
        return format_html('<a href="{}">{}</a>', url, obj.order.number)

    @display(description="Сума", ordering="amount")
    def amount_display(self, obj: Payment) -> str:
        return f"{obj.amount:.2f} {obj.currency}"

    @display(description="Статус", ordering="status", label=STATUS_LABELS)
    def status_badge(self, obj: Payment) -> tuple[str, str]:
        return obj.status, obj.get_status_display()

    @display(description="Комісія магазину", ordering="receiver_commission")
    def commission_display(self, obj: Payment) -> str:
        """Єдине місце, де видно РЕАЛЬНУ ціну «оплати частинами» для магазину."""
        if obj.receiver_commission is None:
            return "—"
        pct = (obj.receiver_commission / obj.amount * 100) if obj.amount else 0
        return f"{obj.receiver_commission:.2f} ₴ ({pct:.1f}%)"

    @display(description="Перевірка банку", boolean=True, ordering="needs_bank_review")
    def review_flag(self, obj: Payment) -> bool:
        return obj.needs_bank_review

    @display(description="Запит до провайдера")
    def raw_request_view(self, obj: Payment) -> str:
        return pretty_json(obj.raw_request, "запит")

    @display(description="Відповідь провайдера")
    def raw_response_view(self, obj: Payment) -> str:
        return pretty_json(obj.raw_response, "відповідь")


@admin.register(PaymentWebhookEvent)
class PaymentWebhookEventAdmin(ModelAdmin):
    """Сирі вебхуки. Readonly повністю — це доказова база для розбору інцидентів."""

    list_display = (
        "created_at",
        "provider",
        "invoice_id",
        "status",
        "result_badge",
        "signature_flag",
        "processed",
        "payment",
    )
    list_filter = (
        ("result", ChoicesDropdownFilter),
        "processed",
        "signature_valid",
        ("created_at", RangeDateTimeFilter),
    )
    list_filter_submit = True
    search_fields = ("invoice_id", "body_hash", "status")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("payment",)

    fieldsets = (
        (
            "Подія",
            {
                "fields": (
                    ("provider", "invoice_id", "status"),
                    "payment",
                    ("signature_valid", "signature"),
                    "body_hash",
                )
            },
        ),
        (
            "Обробка",
            {"fields": (("processed", "processed_at", "result"), "error")},
        ),
        (
            "Сире тіло",
            {"classes": ("collapse",), "fields": ("payload_view", "raw_body", "headers_view")},
        ),
    )

    def get_readonly_fields(self, request: HttpRequest, obj=None) -> tuple:
        return (
            *(f.name for f in self.model._meta.fields),
            "payload_view",
            "headers_view",
        )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    @display(description="Результат", ordering="result", label=RESULT_LABELS)
    def result_badge(self, obj: PaymentWebhookEvent) -> tuple[str, str]:
        return obj.result, obj.get_result_display() or "—"

    @display(description="Підпис", boolean=True, ordering="signature_valid")
    def signature_flag(self, obj: PaymentWebhookEvent) -> bool:
        return obj.signature_valid

    @display(description="Розшифроване тіло")
    def payload_view(self, obj: PaymentWebhookEvent) -> str:
        return pretty_json(obj.payload, "payload")

    @display(description="Заголовки")
    def headers_view(self, obj: PaymentWebhookEvent) -> str:
        return pretty_json(obj.headers, "headers")
