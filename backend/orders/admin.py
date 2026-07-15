"""
Замовлення — адмінка.

🔴 ГОЛОВНИЙ ПРИНЦИП ЦЬОГО ЕКРАНА: замовлення — це ЮРИДИЧНИЙ СЛІД, а не робочий документ.
   Тому в адмінці НЕ РЕДАГУЄТЬСЯ нічого, що описує «чек»:
     * позиції (OrderItem) — снапшот товару на момент оформлення (див. orders/models.py):
       ціна, назва, sku зафіксовані НАЗАВЖДИ. Правка тут переписала б історію продажів;
     * суми (subtotal / discount / total) — рахує checkout-сервіс із актуальних Product.price;
     * number / public_token / idempotency_key — ідентифікатори, згенеровані сервером.

   Менеджер тут керує рівно двома речами: СТАТУСОМ (з обов'язковим записом в історію),
   ТТН і примітками. Усе решта — read-only.

⚠️ Створення замовлення з адмінки ВИМКНЕНО (has_add_permission=False): Order.idempotency_key
   не має дефолту, а номер береться з sequence — коректно зібрати замовлення вміє тільки
   checkout-сервіс. Форма «додати» тут або впала б, або створила б замовлення без інваріантів.

⚠️ Публічна сторінка замовлення адресується public_token, а НЕ номером (IDOR, orders/models.py).
   Тому в картці показуємо готове посилання з токеном — щоб менеджер міг надіслати його
   покупцю, не збираючи URL руками.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateTimeFilter
from unfold.decorators import display

from orders.models import Order, OrderItem, OrderStatusHistory

# --- кольори бейджів ---------------------------------------------------------
# Логіка кольору: сірий/синій — у процесі, помаранчевий — потребує дії, зелений — закрито,
# червоний — гроші не отримані / замовлення втрачене.
STATUS_LABELS: dict[str, str] = {
    Order.Status.NEW: "primary",
    Order.Status.CONFIRMED: "info",
    Order.Status.PACKED: "warning",
    Order.Status.SHIPPED: "warning",
    Order.Status.DELIVERED: "success",
    Order.Status.DONE: "success",
    Order.Status.CANCELLED: "danger",
    Order.Status.RETURNED: "danger",
}

PAYMENT_STATUS_LABELS: dict[str, str] = {
    Order.PaymentStatus.NOT_REQUIRED: "default",
    Order.PaymentStatus.PENDING: "warning",
    Order.PaymentStatus.PAID: "success",
    Order.PaymentStatus.FAILED: "danger",
    Order.PaymentStatus.REFUNDED: "info",
}


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------
class OrderItemInline(TabularInline):
    """🔴 READONLY. Це снапшот, а не кошик — див. шапку модуля."""

    model = OrderItem
    extra = 0
    can_delete = False
    show_change_link = False
    verbose_name = "Позиція (снапшот)"
    verbose_name_plural = "Позиції замовлення (снапшот — не редагується)"

    fields = (
        "sku",
        "name",
        "price",
        "quantity",
        "line_total",
        "installment_available",
        "weight_kg",
        "volume_m3",
        "dims_source",
    )
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class OrderStatusHistoryInline(TabularInline):
    """Append-only журнал переходів. Пишеться дією/збереженням, руками — ніколи."""

    model = OrderStatusHistory
    extra = 0
    can_delete = False
    verbose_name = "Зміна статусу"
    verbose_name_plural = "Історія статусів"

    fields = ("created_at", "from_status", "to_status", "changed_by", "comment")
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        return super().get_queryset(request).select_related("changed_by")


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------
@admin.register(Order)
class OrderAdmin(ModelAdmin):
    list_display = (
        "number",
        "created_at",
        "customer",
        "phone",
        "total_display",
        "status_badge",
        "delivery_display",
        "payment_method_display",
        "payment_status_badge",
    )
    list_display_links = ("number", "customer")
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("payment_status", ChoicesDropdownFilter),
        ("delivery_method", ChoicesDropdownFilter),
        ("payment_method", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    list_filter_submit = True
    search_fields = ("number", "phone", "email", "last_name", "first_name", "ttn")
    search_help_text = "Номер, телефон, email, прізвище або ТТН"
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_per_page = 50

    inlines = (OrderItemInline, OrderStatusHistoryInline)

    actions = (
        "set_confirmed",
        "set_packed",
        "set_shipped",
        "set_delivered",
        "set_done",
        "set_cancelled",
    )

    # 🔴 Юридичний слід: усе, що формує чек, — readonly.
    readonly_fields = (
        "number",
        "public_token",
        "public_link",
        "idempotency_key",
        "subtotal",
        "discount",
        "total",
        "paid_at",
        "created_at",
        "updated_at",
        "ip",
        "user_agent",
        "utm",
    )

    fieldsets = (
        (
            "Замовлення",
            {
                "fields": (
                    "number",
                    "status",
                    ("created_at", "updated_at"),
                    "public_link",
                    "public_token",
                    "idempotency_key",
                )
            },
        ),
        (
            "Покупець",
            {"fields": (("last_name", "first_name"), ("phone", "email"), "comment")},
        ),
        (
            "Доставка",
            {
                "fields": (
                    "delivery_method",
                    "pickup_point",
                    ("np_area_name", "np_city_name"),
                    ("np_area_ref", "np_city_ref", "np_settlement_ref"),
                    ("np_warehouse_ref", "np_warehouse_name", "np_service_type"),
                    "delivery_address",
                    "delivery_cost_estimate",
                    "ttn",
                )
            },
        ),
        (
            "Оплата",
            {"fields": ("payment_method", "payment_status", "paid_at")},
        ),
        (
            "Суми (рахує сервер — не редагується)",
            {"fields": (("subtotal", "discount", "total"),)},
        ),
        (
            "Службове",
            {
                "classes": ("collapse",),
                "fields": ("manager_note", "utm", "ip", "user_agent"),
            },
        ),
    )

    # -- права ---------------------------------------------------------------

    def has_add_permission(self, request: HttpRequest) -> bool:
        # Замовлення створює checkout-сервіс (sequence + idempotency_key). Див. шапку.
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        # Фінансовий слід. Payment.order = PROTECT — оплачене замовлення не видалиться і в БД.
        return request.user.is_superuser

    # -- колонки -------------------------------------------------------------

    @display(description="Покупець", ordering="last_name")
    def customer(self, obj: Order) -> str:
        return f"{obj.last_name} {obj.first_name}".strip()

    @display(description="Сума", ordering="total")
    def total_display(self, obj: Order) -> str:
        return f"{obj.total:.2f} ₴"

    @display(description="Статус", ordering="status", label=STATUS_LABELS)
    def status_badge(self, obj: Order) -> tuple[str, str]:
        return obj.status, obj.get_status_display()

    @display(description="Оплата", ordering="payment_status", label=PAYMENT_STATUS_LABELS)
    def payment_status_badge(self, obj: Order) -> tuple[str, str]:
        return obj.payment_status, obj.get_payment_status_display()

    @display(description="Доставка", ordering="delivery_method")
    def delivery_display(self, obj: Order) -> str:
        if obj.ttn:
            return f"{obj.get_delivery_method_display()} · ТТН {obj.ttn}"
        return obj.get_delivery_method_display()

    @display(description="Спосіб оплати", ordering="payment_method")
    def payment_method_display(self, obj: Order) -> str:
        return obj.get_payment_method_display()

    @display(description="Сторінка замовлення (для покупця)")
    def public_link(self, obj: Order) -> str:
        """Роут іде по public_token, а не по номеру (IDOR — див. orders/models.py)."""
        if not obj.pk:
            return "—"
        url = f"{settings.SITE_URL.rstrip('/')}/uk/order/{obj.public_token}"
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', url, url)

    # -- історія статусів ----------------------------------------------------

    def save_model(self, request: HttpRequest, obj: Order, form, change: bool) -> None:
        """Зміна статусу з форми теж МУСИТЬ лишити слід в OrderStatusHistory."""
        old_status = None
        if change and "status" in form.changed_data:
            old_status = Order.objects.filter(pk=obj.pk).values_list("status", flat=True).first()

        super().save_model(request, obj, form, change)

        if old_status is not None and old_status != obj.status:
            OrderStatusHistory.objects.create(
                order=obj,
                from_status=old_status,
                to_status=obj.status,
                changed_by=request.user,
                comment="Змінено вручну в адмінці",
            )

    def _transition(self, request: HttpRequest, queryset: QuerySet, new_status: str) -> None:
        changed = 0
        for order in queryset:
            if order.status == new_status:
                continue
            old = order.status
            order.status = new_status
            order.save(update_fields=["status", "updated_at"])
            OrderStatusHistory.objects.create(
                order=order,
                from_status=old,
                to_status=new_status,
                changed_by=request.user,
                comment="Масова дія в адмінці",
            )
            changed += 1

        label = Order.Status(new_status).label
        self.message_user(
            request,
            f"Переведено в «{label}»: {changed}. Пропущено (вже в цьому статусі): "
            f"{queryset.count() - changed}.",
        )

    @admin.action(description="Статус → Підтверджений")
    def set_confirmed(self, request: HttpRequest, queryset: QuerySet) -> None:
        self._transition(request, queryset, Order.Status.CONFIRMED)

    @admin.action(description="Статус → Скомплектований")
    def set_packed(self, request: HttpRequest, queryset: QuerySet) -> None:
        self._transition(request, queryset, Order.Status.PACKED)

    @admin.action(description="Статус → Відправлений")
    def set_shipped(self, request: HttpRequest, queryset: QuerySet) -> None:
        self._transition(request, queryset, Order.Status.SHIPPED)

    @admin.action(description="Статус → Доставлений")
    def set_delivered(self, request: HttpRequest, queryset: QuerySet) -> None:
        self._transition(request, queryset, Order.Status.DELIVERED)

    @admin.action(description="Статус → Завершений")
    def set_done(self, request: HttpRequest, queryset: QuerySet) -> None:
        self._transition(request, queryset, Order.Status.DONE)

    @admin.action(description="Статус → Скасований")
    def set_cancelled(self, request: HttpRequest, queryset: QuerySet) -> None:
        self._transition(request, queryset, Order.Status.CANCELLED)


# ---------------------------------------------------------------------------
# OrderStatusHistory — окремий журнал (для аудиту «хто що переводив за тиждень»)
# ---------------------------------------------------------------------------
@admin.register(OrderStatusHistory)
class OrderStatusHistoryAdmin(ModelAdmin):
    list_display = ("created_at", "order", "from_status", "to_status", "changed_by", "comment")
    list_filter = (("created_at", RangeDateTimeFilter), "to_status")
    search_fields = ("order__number", "order__phone")
    date_hierarchy = "created_at"
    list_select_related = ("order", "changed_by")

    def get_readonly_fields(self, request: HttpRequest, obj=None) -> tuple:
        return tuple(f.name for f in self.model._meta.fields)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False
