"""
Доставка — адмінка.

Усе тут READONLY, і це принципово: довідники НП — не наші дані. Їх наповнює нічний cron
(`delivery.tasks.sync_np_refs`, 03:00), а не людина. Ручна правка ref-а = ТТН, який не
вибивається, при формально валідному замовленні.

Що з цього екрана реально потрібно менеджеру:
  • «Чому покупець не бачить свого відділення?» → пошук по відділеннях + колонка «Активне»
    (зникло з відповіді НП → is_active=False; відділення НП закриваються регулярно);
  • «Довідники взагалі оновлюються?» → колонка «Синхронізовано» (last_seen_run → SyncRun).

🔴 SettlementRef ≠ CityRef (INTEGRATIONS §1.4). У NPSettlement показані ОБИДВА: `ref` — це
   SettlementRef (по ньому мапиться пошук), `delivery_city_ref` — CityRef, КАНОН для API НП.
   Плутанина між ними — класична причина «замовлення є, ТТН немає».
"""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest
from django.utils import timezone
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateTimeFilter
from unfold.decorators import display

from delivery.models import DeliveryQuote, NPArea, NPCity, NPSettlement, NPWarehouse
from sync.models import SyncRun


class ReadOnlyAdmin(ModelAdmin):
    """Довідник наповнює cron. Дивитись — так, правити — ні."""

    def get_readonly_fields(self, request: HttpRequest, obj=None) -> tuple:
        return tuple(f.name for f in self.model._meta.fields)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class LastSyncMixin:
    """
    `last_seen_run` — це UUID прогону, а не дата. Людині потрібна дата.

    FK на SyncRun тут немає свідомо (довідники пишуться масовими upsert-ами), тому
    резолвимо руками і кешуємо: started_at прогону НЕЗМІННИЙ, тож кеш не протухає,
    а різних прогонів у вибірці — одиниці (нічний cron), не сотні.
    """

    _run_started: dict = {}

    @display(description="Синхронізовано", ordering="last_seen_run")
    def last_sync(self, obj) -> str:
        run_id = obj.last_seen_run
        if not run_id:
            return "—"
        if run_id not in self._run_started:
            self._run_started[run_id] = (
                SyncRun.objects.filter(pk=run_id).values_list("started_at", flat=True).first()
            )
        started = self._run_started[run_id]
        if started is None:
            return str(run_id)[:8]
        return timezone.localtime(started).strftime("%d.%m.%Y %H:%M")


@admin.register(NPArea)
class NPAreaAdmin(ReadOnlyAdmin):
    list_display = ("name", "ref", "center_city_ref")
    search_fields = ("name", "ref")
    ordering = ("name",)


@admin.register(NPCity)
class NPCityAdmin(LastSyncMixin, ReadOnlyAdmin):
    list_display = ("name", "area", "settlement_type", "ref", "is_active", "last_sync")
    list_filter = ("area", "is_active")
    search_fields = ("name", "ref")
    search_help_text = "Назва міста або CityRef"
    list_select_related = ("area",)
    ordering = ("name",)


@admin.register(NPSettlement)
class NPSettlementAdmin(LastSyncMixin, ReadOnlyAdmin):
    list_display = (
        "present",
        "area",
        "settlement_type_code",
        "warehouses_count",
        "delivery_city_ref",
        "is_active",
        "last_sync",
    )
    list_filter = ("area", "is_active", "settlement_type_code")
    search_fields = ("present", "name", "ref", "delivery_city_ref")
    search_help_text = "Назва, SettlementRef або CityRef (це РІЗНІ ref-и — див. шапку модуля)"
    list_select_related = ("area",)
    ordering = ("-warehouses_count", "name")


@admin.register(NPWarehouse)
class NPWarehouseAdmin(LastSyncMixin, ReadOnlyAdmin):
    list_display = (
        "description",
        "category_badge",
        "city",
        "number",
        "limits",
        "status",
        "is_active",
        "last_sync",
    )
    list_filter = (
        ("category", ChoicesDropdownFilter),
        "is_active",
        "status",
    )
    list_filter_submit = True
    search_fields = ("description", "short_address", "number", "city_ref", "ref")
    search_help_text = "Адреса, номер відділення або CityRef"
    list_select_related = ("city",)
    ordering = ("city_ref", "category", "number")

    @display(
        description="Тип",
        ordering="category",
        label={
            NPWarehouse.Category.BRANCH: "info",
            NPWarehouse.Category.POSTOMAT: "warning",
            NPWarehouse.Category.DROPOFF: "default",
        },
    )
    def category_badge(self, obj: NPWarehouse) -> tuple[str, str]:
        return obj.category, obj.get_category_display()

    @display(description="Обмеження (Ш×В×Д, вага)")
    def limits(self, obj: NPWarehouse) -> str:
        """NULL = НП обмеження не віддала → відділення НЕ відсікаємо (див. fits_package)."""
        dims = [obj.max_width_cm, obj.max_height_cm, obj.max_length_cm]
        if not any(dims):
            return "без обмежень"
        shown = "×".join(f"{d:.0f}" if d is not None else "?" for d in dims)
        weight = f", до {obj.place_max_weight_kg:.0f} кг" if obj.place_max_weight_kg else ""
        return f"{shown} см{weight}"


@admin.register(DeliveryQuote)
class DeliveryQuoteAdmin(ReadOnlyAdmin):
    """Кеш розрахунків — ТІЛЬКИ аналітика (гарячий кеш живе в Redis, семплювання 10%)."""

    list_display = (
        "created_at",
        "city_ref",
        "service_type",
        "weight_kg",
        "volume_m3",
        "cost_declared",
        "cost_delivery",
        "cost_redelivery",
        "dims_source_badge",
    )
    list_filter = (
        ("service_type", ChoicesDropdownFilter),
        ("dims_source", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    list_filter_submit = True
    search_fields = ("city_ref", "cache_key")
    date_hierarchy = "created_at"

    @display(
        description="Джерело габаритів",
        ordering="dims_source",
        label={
            "spec": "success",
            "product": "info",
            "category": "warning",
            "default": "danger",  # >20% "default" у категорії = системна недоплата за доставку
        },
    )
    def dims_source_badge(self, obj: DeliveryQuote) -> tuple[str, str]:
        return obj.dims_source, obj.get_dims_source_display() or "—"
