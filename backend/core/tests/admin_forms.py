"""Хелпери для тестів, які САБМІТЯТЬ форми адмінки як людина, а не смикають ModelAdmin.

Навіщо окремий модуль: «замовник керує сайтом з адмінки» перевіряється лише наскрізним
POST — через `admin_client`, реальні URL (`reverse("admin:app_model_add")`) і реальні файли
в `request.FILES`. Виклик `ModelAdmin.save_model()` напряму пропускає рівно те, що ламається
на практиці: readonly-поля, віджети, валідатори форми, formset списку.

Дві пастки, заради яких це винесено з тестів:

  1. **modeltranslation.** У формі НЕМАЄ поля `title` — є `title_uk` / `title_ru`. Тому дані
     для POST не можна писати руками зі списку полів моделі: `_form_data()` бере їх з
     реальної форми адмінки (`ModelAdmin.get_form()`), тобто автоматично враховує і мовні
     колонки, і readonly, і fieldsets.

  2. **`list_editable` — це FORMSET.** Галочка в списку категорій шле не `is_active=on`, а
     `form-0-is_active` + повний ManagementForm, причому кількість форм МУСИТЬ збігатися з
     кількістю рядків у changelist. Розбіжність = тиха 200-ка без збереження, тобто
     «натиснув Зберегти, нічого не змінилось» — найдорожчий клас багів адмінки.
"""

from __future__ import annotations

from typing import Any

from django.contrib.admin.sites import site
from django.db.models import Model
from django.forms import BooleanField
from django.forms.models import model_to_dict


def form_data(obj: Model, **overrides: Any) -> dict[str, Any]:
    """Дані для POST на change/add-форму адмінки цього об'єкта.

    Береться СПИСОК ПОЛІВ РЕАЛЬНОЇ ФОРМИ (з fieldsets і мовними колонками), а значення —
    з самого об'єкта. Файлові поля пропускаються: їх передають окремо, як `request.FILES`,
    інакше Django сприйме рядок як «файл не міняли» лише випадково.
    """
    model_admin = site._registry[type(obj)]
    form_class = model_admin.get_form(_admin_request())
    current = model_to_dict(obj)

    data: dict[str, Any] = {}
    for name, field in form_class.base_fields.items():
        if name in overrides:
            continue
        if hasattr(field.widget, "needs_multipart_form") and field.widget.needs_multipart_form:
            continue  # FileInput — файл їде окремим ключем у payload тесту
        value = current.get(name)
        if value is None or value == "":
            continue
        if isinstance(field, BooleanField):
            if value:
                data[name] = "on"
            continue
        if isinstance(value, list):
            data[name] = ",".join(str(v) for v in value)
            continue
        data[name] = str(value)

    for name, value in overrides.items():
        if value is None:
            data.pop(name, None)
        elif isinstance(value, bool):
            if value:
                data[name] = "on"
            else:
                data.pop(name, None)
        else:
            data[name] = value

    data["_save"] = ""
    return data


def changelist_data(model: type[Model], overrides: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Payload для POST у changelist з `list_editable` (галочки прямо в списку).

    ⚠️ Форми будуються по ВСІХ рядках у порядку `ModelAdmin.ordering` — саме їх покаже
       changelist. Якщо надіслати одну форму на десять рядків, Django відкине formset як
       невалідний і поверне 200 БЕЗ збереження: сторінка перемалюється, галочка «відскочить».
    """
    model_admin = site._registry[model]
    ordering = model_admin.ordering or ("pk",)
    objects = list(model.objects.order_by(*ordering))

    data: dict[str, Any] = {
        "form-TOTAL_FORMS": str(len(objects)),
        "form-INITIAL_FORMS": str(len(objects)),
        "form-MIN_NUM_FORMS": "0",
        "form-MAX_NUM_FORMS": str(len(objects)),
        "_save": "",
    }
    for index, obj in enumerate(objects):
        data[f"form-{index}-id"] = str(obj.pk)
        row = overrides.get(obj.pk, {})
        for field in model_admin.list_editable:
            value = row[field] if field in row else getattr(obj, field)
            if isinstance(value, bool):
                if value:
                    data[f"form-{index}-{field}"] = "on"
                continue
            data[f"form-{index}-{field}"] = "" if value is None else str(value)
    return data


def _admin_request() -> Any:
    """Справжній `HttpRequest` — unfold і modeltranslation читають з нього `method`, `GET`
    і права, тому дублер з двома атрибутами тут не годиться (падає AttributeError)."""
    from django.contrib.auth import get_user_model
    from django.test import RequestFactory

    request = RequestFactory().get("/")
    request.user = get_user_model()(is_superuser=True, is_staff=True, is_active=True)
    return request
