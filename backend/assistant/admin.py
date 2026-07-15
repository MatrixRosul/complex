"""
ШІ-асистент — адмінка. Дашборд витрат і матеріал «що люди насправді питають».

Усе тут readonly, і це принципово:
  * сесія і повідомлення — ЖУРНАЛ. Правити журнал = ламати аудит витрат (Sum(cost_usd)
    за місяць — це наш hard-cap) і псувати єдине джерело правди про те, що саме бачив
    користувач. Тому ні add, ні change; лишається delete (видалити чужу приватну розмову
    на прохання — легітимна операція, «підправити» її — ні).
  * ip_hash показуємо як є: це sha256(IP + SECRET_KEY)[:32], розкрити з нього нічого не
    можна, а зіставити дві сесії одного відвідувача — можна.

Найцінніша колонка тут — не гроші, а текст питань. Він показує, яких характеристик у
таблиці Артура бракує найбільше: якщо люди питають те, чого асистент не знає, значить це
і треба збирати першим.
"""

from __future__ import annotations

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateTimeFilter
from unfold.decorators import display

from assistant.models import ChatMessage, ChatRole, ChatSession

__all__ = ["ChatMessageAdmin", "ChatMessageInline", "ChatSessionAdmin"]

ROLE_LABELS: dict[str, str] = {
    ChatRole.USER: "info",
    ChatRole.ASSISTANT: "success",
}


def _money(value) -> str:
    if not value:
        return "—"
    return f"${value:.6f}".rstrip("0").rstrip(".")


class ChatMessageInline(TabularInline):
    """Уся розмова однією стрічкою — читається зверху вниз, як у чаті."""

    model = ChatMessage
    extra = 0
    can_delete = False
    show_change_link = True
    ordering = ("created_at", "id")
    fields = ("created_at", "role", "content", "tool_names", "tokens", "cost_display")
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    @display(description="Інструменти")
    def tool_names(self, obj: ChatMessage) -> str:
        calls = obj.tool_calls or []
        names = [str(c.get("name", "?")) for c in calls if isinstance(c, dict)]
        return ", ".join(names) if names else "—"

    @display(description="Токени")
    def tokens(self, obj: ChatMessage) -> str:
        if not (obj.input_tokens or obj.output_tokens):
            return "—"
        return (
            f"{obj.input_tokens} → {obj.output_tokens} "
            f"(кеш: {obj.cache_read_tokens}/{obj.cache_write_tokens})"
        )

    @display(description="Вартість")
    def cost_display(self, obj: ChatMessage) -> str:
        return _money(obj.cost_usd)


@admin.register(ChatSession)
class ChatSessionAdmin(ModelAdmin):
    list_display = (
        "id",
        "public_token",
        "locale",
        "first_question",
        "message_count",
        "cost_display",
        "ip_hash_short",
        "created_at",
    )
    list_display_links = ("id", "public_token")
    list_fullwidth = True
    list_per_page = 50
    list_filter = (
        ("locale", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    list_filter_submit = True
    search_fields = ("public_token", "messages__content")
    search_help_text = "Токен сесії або текст будь-якого повідомлення в ній"
    ordering = ("-created_at", "-id")
    date_hierarchy = "created_at"

    inlines = (ChatMessageInline,)

    readonly_fields = (
        "public_token",
        "locale",
        "ip_hash",
        "message_count",
        "cost_usd",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            "Сесія",
            {
                "description": (
                    "Журнал розмови. Редагування вимкнено: на Sum(cost_usd) за місяць "
                    "тримається місячний ліміт витрат — правити його не можна."
                ),
                "fields": (("public_token", "locale"), ("message_count", "cost_usd")),
            },
        ),
        (
            "Технічне",
            {
                "classes": ("collapse",),
                "fields": ("ip_hash", ("created_at", "updated_at")),
            },
        ),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[ChatSession]:
        return super().get_queryset(request).prefetch_related("messages")

    def has_add_permission(self, request: HttpRequest) -> bool:
        # Сесії створює чат, а не людина.
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    @display(description="Перше питання", ordering="id")
    def first_question(self, obj: ChatSession) -> str:
        first = next((m for m in obj.messages.all() if m.role == ChatRole.USER), None)
        if first is None:
            return "—"
        text = first.content or ""
        short = text if len(text) <= 80 else text[:80] + "…"
        return format_html('<span title="{}">{}</span>', text[:500], short)

    @display(description="Вартість", ordering="cost_usd")
    def cost_display(self, obj: ChatSession) -> str:
        return _money(obj.cost_usd)

    @display(description="Відбиток IP", ordering="ip_hash")
    def ip_hash_short(self, obj: ChatSession) -> str:
        return obj.ip_hash[:10] or "—"


@admin.register(ChatMessage)
class ChatMessageAdmin(ModelAdmin):
    """Плоский зріз усіх повідомлень: пошук по темах і покілька-денний облік витрат."""

    list_display = ("id", "created_at", "session", "role_badge", "content_preview", "cost_display")
    list_display_links = ("id",)
    list_fullwidth = True
    list_per_page = 100
    list_filter = (("role", ChoicesDropdownFilter), ("created_at", RangeDateTimeFilter))
    list_filter_submit = True
    search_fields = ("content", "session__public_token")
    search_help_text = "Текст повідомлення або токен сесії"
    ordering = ("-created_at", "-id")
    date_hierarchy = "created_at"

    readonly_fields = (
        "session",
        "role",
        "content",
        "tool_calls",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "cost_usd",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        ("Повідомлення", {"fields": (("session", "role"), "content", "tool_calls")}),
        (
            "Витрати",
            {
                "classes": ("collapse",),
                "fields": (
                    ("input_tokens", "output_tokens"),
                    ("cache_read_tokens", "cache_write_tokens"),
                    ("cost_usd", "created_at"),
                ),
            },
        ),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[ChatMessage]:
        return super().get_queryset(request).select_related("session")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    @display(description="Роль", ordering="role", label=ROLE_LABELS)
    def role_badge(self, obj: ChatMessage) -> tuple[str, str]:
        return obj.role, obj.get_role_display()

    @display(description="Текст", ordering="content")
    def content_preview(self, obj: ChatMessage) -> str:
        text = obj.content or ""
        short = text if len(text) <= 110 else text[:110] + "…"
        return format_html('<span title="{}">{}</span>', text[:800], short)

    @display(description="Вартість", ordering="cost_usd")
    def cost_display(self, obj: ChatMessage) -> str:
        return _money(obj.cost_usd)
