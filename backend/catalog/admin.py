"""Адмінка каталогу (django-unfold).

Це РОБОЧЕ МІСЦЕ контент-менеджера, а не CRUD-обгортка над таблицями. Три речі, які тут
зроблені інакше, ніж «за замовчуванням», і чому:

  1. ЦІНА НЕ РЕДАГУЄТЬСЯ. `price` — похідна (ADR-005), її рахує SQL-функція `complex_price_uah()`
     з base_price × націнка × курс × правило округлення. Менеджер править ДЖЕРЕЛА, а поле
     «Ціна, грн» показує результат. Інакше ручна правка `price` жила б рівно до наступного
     синку — і ніхто б не зрозумів, куди вона поділась.

  2. ХАРАКТЕРИСТИКИ ПОКАЗУЮТЬ ПРЕВ'Ю РЕНДЕРУ. В inline є колонка «Як буде на картці», яка
     склеює значення з одиницею рівно так, як це зробить фронт: «Висота» → «284 мм»
     (INPUTS §2). Одиниця клеїться до ЗНАЧЕННЯ, а не до назви — і менеджер бачить це одразу,
     а не після деплою.

  3. ВИДАЛЕННЯ БРЕНДІВ/КРАЇН/ВАРІАНТІВ ВИМКНЕНЕ. Замість нього — «Злити з…»
     (`services/merge.py`). Причина в моделях: Product.brand = SET_NULL (видалення дубля
     мовчки обнулило б бренд у сотень товарів), PAV.option = PROTECT (видалення просто впаде).

Габарити упаковки і гарантія — READONLY: це денормалізовані проєкції з характеристик
(Attribute.role = PACKAGE_DIMS / PACKAGE_WEIGHT / WARRANTY_MONTHS), а не самостійні поля.
Правити їх треба в характеристиках товару — про це написано прямо у formі.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from django import forms
from django.contrib import admin, messages
from django.db import transaction
from django.db.models import Count, Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString
from modeltranslation.admin import TabbedTranslationAdmin, TranslationTabularInline
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import (
    AutocompleteSelectFilter,
    BooleanRadioFilter,
    ChoicesDropdownFilter,
    DropdownFilter,
    RangeDateTimeFilter,
    RelatedDropdownFilter,
)
from unfold.decorators import action, display
from unfold.enums import ActionVariant
from unfold.forms import BaseDialogForm
from unfold.widgets import (
    UnfoldAdminSelectWidget,
    UnfoldAdminTextInputWidget,
    UnfoldBooleanSwitchWidget,
)

from catalog.enums import Availability, ProductSource
from catalog.models import (
    Attribute,
    AttributeGroup,
    AttributeOption,
    Brand,
    Category,
    Country,
    PriceHistory,
    Product,
    ProductAttributeValue,
    ProductImage,
    RelatedGroup,
    RelatedGroupItem,
    Unit,
    VariantGroup,
    VariantItem,
)
from catalog.services.cache import invalidate_collections_cache
from catalog.services.hotline import affected_products, set_hotline_for_category
from catalog.services.images import save_image_formset
from catalog.services.merge import (
    MergeError,
    merge_brands,
    merge_countries,
    merge_options,
    mergeable_targets,
)
from catalog.services.pricing import PriceError, compute_uah_price, price_reason, recalc_products
from cms.admin import WysiwygBodyMixin
from core.models import SiteSettings
from sync.tasks import rebuild_product_denorm

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Хелпери відображення
# ---------------------------------------------------------------------------
def _fmt_number(value: Decimal | None) -> str:
    """331.0000 → «331», 171.5000 → «171.5». Хвости нулів у картці нікому не потрібні."""
    if value is None:
        return ""
    q = value.normalize()
    if q == q.to_integral_value():
        q = q.quantize(Decimal(1))
    return f"{q:f}"


def _fmt_money(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}".replace(",", " ")


def _file_url(*candidates: Any) -> str:
    """Перший непорожній FieldFile → URL. `.url` на порожньому FieldFile кидає ValueError."""
    for f in candidates:
        if f:
            try:
                return f.url
            except ValueError:  # pragma: no cover
                continue
    return ""


def _thumb(url: str, size: int = 40, radius: int = 6) -> SafeString:
    if not url:
        return format_html(
            '<div style="width:{}px;height:{}px;border-radius:{}px;'
            'background:rgba(128,128,128,.12)"></div>',
            size,
            size,
            radius,
        )
    return format_html(
        '<img src="{}" style="width:{}px;height:{}px;object-fit:contain;border-radius:{}px;'
        'background:#fff" loading="lazy">',
        url,
        size,
        size,
        radius,
    )


AVAILABILITY_COLORS = {
    "В наявності": "success",
    "Під замовлення": "warning",
    "Немає в наявності": "danger",
}
CONDITION_COLORS = {
    "Новий": "info",
    "Відновлений": "warning",
    "Уцінений": "danger",
    "Б/в": "warning",
}


# ---------------------------------------------------------------------------
# Фільтри «стану даних» (SYNC.md §4.3, §12)
# ---------------------------------------------------------------------------
class RenamedBooleanFilter(BooleanRadioFilter):
    """BooleanRadioFilter з ВЛАСНИМ заголовком.

    `verbose_name` поля описує ДАНІ («Неповний товар (чернетка прайсу)»), а фільтр має
    відповідати на питання менеджера — «а де ті товари, яких не видно на сайті?». Змінювати
    заради цього verbose_name не можна: це міграція на рівному місці.
    """

    filter_title = ""

    def __init__(self, field, request, params, model, model_admin, field_path) -> None:  # type: ignore[no-untyped-def]
        super().__init__(field, request, params, model, model_admin, field_path)
        if self.filter_title:
            self.title = self.filter_title


class IncompleteFilter(RenamedBooleanFilter):
    filter_title = "Неповні — сховані з сайту"


class SurrogateSkuFilter(RenamedBooleanFilter):
    filter_title = "Без артикула (ключ AUTO-…)"


class CategoryBranchFilter(DropdownFilter):
    """Фільтр «Категорія» — РАЗОМ З УСІМА ПІДКАТЕГОРІЯМИ.

    🔴 Було: звичайний `("category", RelatedDropdownFilter)`, тобто точний збіг по FK.
    Товари ж висять на ЛИСТКАХ дерева, а не на кореневих вузлах, — тому вибір
    «Вбудована побутова техніка» давав «No results» попри 151 товар у гілці. Замовник
    справедливо прочитав це як «фільтр не працює».

    Тепер вибірка йде по матеріалізованому `path` (ADR-001 його для цього й тримає):
    один індексований запит замість рекурсії по дереву.

    ⚠️ `path__startswith` САМ ПО СОБІ БРЕХЛИВИЙ: шлях «87727179» — це префікс і для
    «877271790», тобто чужа категорія з довшим external_id потрапила б у вибірку.
    Тому умова з двох частин: або сам вузол, або нащадок ЧЕРЕЗ РОЗДІЛЮВАЧ (`path + "/"`).

    Список показує все дерево з відступами — інакше «Духові шафи» під двома різними
    батьками не відрізнити.
    """

    title = "Категорія (з підкатегоріями)"
    parameter_name = "category_branch"

    def lookups(self, request: HttpRequest, model_admin: Any) -> list[tuple[Any, str]]:
        """Дерево з відступами І З ЛІЧИЛЬНИКОМ — «Посудомийні машини (0)».

        🔴 Лічильник тут не прикраса, а відповідь на другу скаргу: «не по всіх
        категоріях працює, наприклад посудомийні машини». Фільтр працював —
        категорія «Посудомийні машини» (під «Велика побутова техніка») просто
        ПОРОЖНЯ, бо в прайсі окремостоячих посудомийок немає, усі 14 лежать у
        «Вбудовані посудомийні машини». Порожній результат був чесним, але
        неможливо було відрізнити його від поломки. Тепер видно ДО кліку.

        Рахуємо ОДНИМ `GROUP BY` + роллап по `path` у Python (70 вузлів —
        мікросекунди), а не запитом на категорію: інакше це 70 запитів на кожне
        відкриття списку товарів.
        """
        categories = list(Category.objects.filter(is_service=False).order_by("path"))
        direct: dict[int, int] = dict(
            Product.objects.values_list("category_id").annotate(n=Count("id"))
        )

        lookups: list[tuple[Any, str]] = []
        for cat in categories:
            total = direct.get(cat.pk, 0) + sum(
                n
                for other in categories
                if other.pk != cat.pk and other.path.startswith(f"{cat.path}/")
                for n in (direct.get(other.pk, 0),)
            )
            lookups.append((str(cat.pk), f"{'— ' * cat.depth}{cat.name} ({total})"))
        return lookups

    def queryset(self, request: HttpRequest, queryset: QuerySet) -> QuerySet:
        value = self.value()
        if not value:
            return queryset
        category = Category.objects.filter(pk=value).only("path").first()
        if category is None:
            return queryset.none()
        return queryset.filter(
            Q(category__path=category.path) | Q(category__path__startswith=f"{category.path}/")
        )


# ---------------------------------------------------------------------------
# Діалоги (підтвердження з показом наслідків)
# ---------------------------------------------------------------------------
class HotlineCascadeForm(BaseDialogForm):
    """Підтвердження каскаду Hotline. Показує, СКІЛЬКИ товарів буде зачеплено (вимога ТЗ)."""

    mode = forms.ChoiceField(
        label="Дія",
        choices=[("on", "Увімкнути Hotline"), ("off", "Вимкнути Hotline")],
        initial="on",
        widget=UnfoldAdminSelectWidget,
    )
    include_descendants = forms.BooleanField(
        label="Разом з усіма підкатегоріями",
        required=False,
        initial=True,
        widget=UnfoldBooleanSwitchWidget,
    )

    def __init__(self, request: HttpRequest, object_id: Any = None, *args: Any, **kwargs: Any):
        super().__init__(request, object_id, *args, **kwargs)
        category = Category.objects.filter(pk=object_id).first()
        if category is None:
            return
        n_branch = affected_products(category, include_descendants=True).count()
        n_self = affected_products(category, include_descendants=False).count()
        n_cats = category.descendants(include_self=True).count() - 1
        self.fields["include_descendants"].help_text = format_html(
            "Категорія <b>{}</b> + <b>{}</b> підкатегорій.<br>"
            "Буде зачеплено товарів: <b>{}</b> (у самій категорії — {}).<br>"
            "Заразом зміниться значення за замовчуванням для НОВИХ товарів із синку.",
            category.name,
            n_cats,
            n_branch,
            n_self,
        )


class MergeForm(BaseDialogForm):
    """Спільний діалог «Злити з…». Список кандидатів наповнює конкретна адмінка."""

    target = forms.ModelChoiceField(
        label="Залишити (переможець)",
        queryset=Brand.objects.none(),
        widget=UnfoldAdminSelectWidget,
        help_text="Товари дубля переїдуть сюди; його назва і синоніми стануть синонімами "
        "переможця — щоб наступний синк упізнав той самий рядок прайсу.",
    )

    #: підкласи проставляють модель
    model: Any = None

    def __init__(self, request: HttpRequest, object_id: Any = None, *args: Any, **kwargs: Any):
        super().__init__(request, object_id, *args, **kwargs)
        obj = self.model.objects.filter(pk=object_id).first() if self.model else None
        if obj is not None:
            self.fields["target"].queryset = mergeable_targets(obj)


class BrandMergeForm(MergeForm):
    model = Brand


class CountryMergeForm(MergeForm):
    model = Country


class AttributeOptionMergeForm(MergeForm):
    model = AttributeOption


def _hx_redirect(url: str) -> HttpResponse:
    """Діалог unfold сабмітиться через htmx → звичайний 302 просто підмінив би вміст модалки."""
    response = HttpResponse(status=204)
    response["HX-Redirect"] = url
    return response


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------
class ProductImageInline(TabularInline, TranslationTabularInline):
    model = ProductImage
    extra = 0
    tab = True
    show_count = True
    verbose_name = "Фото"
    verbose_name_plural = "Фото"
    ordering = ("position", "id")
    fields = ("preview", "file", "alt", "position", "is_main", "origin")
    readonly_fields = ("preview", "origin")

    @display(description="Прев'ю")
    def preview(self, obj: ProductImage) -> SafeString:
        return _thumb(_file_url(obj.file_thumb, obj.file_card, obj.file), size=56)

    @display(description="Джерело")
    def origin(self, obj: ProductImage) -> str:
        # Фото з прайсу автоприбирання зносить, ручні — не чіпає ніколи (ProductImage.source).
        return obj.get_source_display() if obj.pk else "—"


_BOOL_CHOICES = (("", "—"), ("1", "Так"), ("0", "Ні"))
_BOOL_TRUE = {"1", "так", "є", "yes", "true", "+", "да", "наявний", "присутній"}
_BOOL_FALSE = {"0", "ні", "немає", "no", "false", "-", "нет", "відсутній"}


def _pav_value_field(attr: Attribute | None) -> forms.Field:
    """Одне поле «Значення» — рівно того типу, який оголошений у характеристиці."""
    vt = attr.value_type if attr else None
    if vt == Attribute.ValueType.OPTION:
        return forms.ModelChoiceField(
            label="Значення",
            queryset=attr.options.all(),  # type: ignore[union-attr]
            required=False,
            empty_label="—",
            widget=UnfoldAdminSelectWidget,
        )
    if vt == Attribute.ValueType.NUMBER:
        # Text input, а не NumberInput: спінер поруч із «240,0000» — це шум, а не допомога.
        return forms.DecimalField(
            label="Значення",
            required=False,
            max_digits=14,
            decimal_places=4,
            widget=UnfoldAdminTextInputWidget,
        )
    if vt == Attribute.ValueType.BOOL:
        # Власні choices, а не NullBooleanField: дефолтний віджет Django малює обрізане
        # російське «Неиз[вестно]», а інтерфейс адмінки — тільки український.
        return forms.ChoiceField(
            label="Значення", required=False, choices=_BOOL_CHOICES, widget=UnfoldAdminSelectWidget
        )
    # STRING — і НОВИЙ рядок, де характеристику ще не обрано: приймаємо текст, а звести
    # його до потрібного типу зможемо в clean(), коли характеристика вже відома.
    return forms.CharField(
        label="Значення", required=False, max_length=500, widget=UnfoldAdminTextInputWidget
    )


def _pav_value_initial(obj: ProductAttributeValue, attr: Attribute | None) -> Any:
    vt = attr.value_type if attr else None
    if vt == Attribute.ValueType.OPTION:
        return obj.option_id
    if vt == Attribute.ValueType.NUMBER:
        return _fmt_number(obj.value_number)
    if vt == Attribute.ValueType.BOOL:
        return "" if obj.value_bool is None else ("1" if obj.value_bool else "0")
    return obj.value_string


class ProductAttributeValueForm(forms.ModelForm):
    """Одна колонка «Значення» замість п'яти (варіант / текст uk / текст ru / число / так-ні).

    ⚠️ ЧОМУ ТИП НЕ НА ГРУПІ. Тип значення вже є — `Attribute.value_type` — і жити він має саме
       там: у групі «Основні» цілком законно стоять поруч «Загальний об'єм» (число), «Бренд»
       (текст) і «No Frost» (так/ні). Група — це підзаголовок у картці (AttributeGroup), а не
       тип даних; прив'язка типу до групи розсипалась би на першому ж товарі.

       Бракувало не поля, а того, щоб редактор його ЧИТАВ: інлайн малював усі п'ять колонок
       значення кожному рядку — звідси і горизонтальний скрол, і спінер числа навпроти
       «Тип варильної поверхні: газова».

    ⚠️ Рядок, якого не чіпали, НЕ переписується (див. clean): інакше зміна `value_type` заднім
       числом мовчки витирала б значення, що лежать у «старому» полі.
    """

    value = forms.CharField(label="Значення", required=False)  # підміняється в __init__

    class Meta:
        model = ProductAttributeValue
        fields = ("attribute", "sort_order")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        attr = self.instance.attribute if self.instance.attribute_id else None
        self.fields["value"] = _pav_value_field(attr)
        self.initial["value"] = _pav_value_initial(self.instance, attr)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        attr = cleaned.get("attribute") or (
            self.instance.attribute if self.instance.attribute_id else None
        )
        if attr is None:
            return cleaned

        swapped = bool(self.instance.pk) and self.instance.attribute_id != attr.pk
        if self.instance.pk and not swapped and "value" not in self.changed_data:
            return cleaned  # рядок не редагували — не чіпаємо і його значення

        try:
            self._apply_value(attr, cleaned.get("value"))
        except forms.ValidationError as exc:
            self.add_error("value", exc)
        return cleaned

    def _apply_value(self, attr: Attribute, raw: Any) -> None:
        """Кладе значення РІВНО в одну колонку — ту, що відповідає attr.value_type."""
        obj = self.instance
        obj.option = None
        obj.value_number = None
        obj.value_bool = None
        obj.value_string = ""
        obj.value_string_uk = ""
        obj.value_string_ru = ""
        # ⚠️ RU скидаємо разом з UK: лишити старий переклад біля нового українського значення —
        #    це показати покупцеві в RU-версії те, чого в товарі вже немає. До нового перекладу
        #    RU впаде на UA-фолбек (MODELTRANSLATION_FALLBACK_LANGUAGES).
        if raw in (None, "", []):
            return

        if attr.value_type == Attribute.ValueType.OPTION:
            obj.option = raw if isinstance(raw, AttributeOption) else _find_option(attr, str(raw))
        elif attr.value_type == Attribute.ValueType.NUMBER:
            obj.value_number = raw if isinstance(raw, Decimal) else _to_decimal(str(raw))
        elif attr.value_type == Attribute.ValueType.BOOL:
            obj.value_bool = _to_bool(raw)
        else:
            text = (raw.value if isinstance(raw, AttributeOption) else str(raw))[:500]
            obj.value_string = obj.value_string_uk = text


def _find_option(attr: Attribute, text: str) -> AttributeOption:
    """Шукає варіант серед уже наявних. НЕ створює: одна одруківка — і у фільтрі новий фасет."""
    text = text.strip()[:160]
    opt = (
        AttributeOption.objects.filter(attribute=attr, value_uk__iexact=text).first()
        or AttributeOption.objects.filter(attribute=attr, aliases__contains=[text]).first()
        or AttributeOption.objects.filter(attribute=attr, slug=text).first()
    )
    if opt is not None:
        return opt
    known = list(attr.options.values_list("value", flat=True)[:8])
    hint = ", ".join(f"«{v}»" for v in known) or "жодного ще немає"
    raise forms.ValidationError(
        f"У характеристиці «{attr.name}» немає варіанта «{text}». Наявні: {hint}. "
        f"Спершу додайте варіант у «Варіанти характеристик»."
    )


def _to_decimal(text: str) -> Decimal:
    try:
        return Decimal(text.strip().replace("\xa0", "").replace(" ", "").replace(",", "."))
    except (InvalidOperation, ArithmeticError, ValueError) as exc:
        raise forms.ValidationError(f"«{text}» — не число.") from exc


def _to_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().casefold()
    if text in _BOOL_TRUE:
        return True
    if text in _BOOL_FALSE:
        return False
    raise forms.ValidationError(f"«{raw}» — це не «Так» і не «Ні».")


class ProductAttributeValueInline(TabularInline, TranslationTabularInline):
    """Характеристики товару — джерело істини (EAV).

    ⚠️ Колонки «Одиниця» і «Як буде на картці» — READONLY і НЕ зберігаються: одиниця живе на
       Attribute.unit (FK на словник Unit), а не на значенні. Це і є «автопідстановка одиниці»:
       обрав характеристику — одиниця приїхала з неї, вручну її не вводять узагалі.

    ⚠️ Колонка значення ОДНА (ProductAttributeValueForm), а не п'ять — саме тому таблиця
       влазить у ширину екрана без горизонтального скролу.
    """

    model = ProductAttributeValue
    form = ProductAttributeValueForm
    extra = 0
    tab = True
    show_count = True
    autocomplete_fields = ("attribute",)
    ordering = ("attribute__group__sort_order", "attribute__sort_order", "sort_order", "id")
    fields = (
        "attribute",
        "attr_group",
        "value",
        "attr_unit",
        "rendered",
        "sort_order",
    )
    readonly_fields = ("attr_group", "attr_unit", "rendered")

    def get_queryset(self, request: HttpRequest) -> QuerySet[ProductAttributeValue]:
        return (
            super()
            .get_queryset(request)
            .select_related("attribute", "attribute__group", "attribute__unit", "option")
        )

    @display(description="Група")
    def attr_group(self, obj: ProductAttributeValue) -> str:
        return obj.attribute.group.name if obj.attribute_id else "—"

    @display(description="Одиниця")
    def attr_unit(self, obj: ProductAttributeValue) -> str:
        if not obj.attribute_id or not obj.attribute.unit_id:
            return "—"
        return obj.attribute.unit.name

    @display(description="Як буде на картці")
    def rendered(self, obj: ProductAttributeValue) -> SafeString | str:
        """Рівно те, що побачить покупець. Одиниця клеїться до ЗНАЧЕННЯ (INPUTS §2)."""
        if not obj.pk or not obj.attribute_id:
            return "—"
        attr = obj.attribute
        if obj.option_id:
            value = obj.option.value
        elif obj.value_bool is not None:
            value = "Так" if obj.value_bool else "Ні"
        elif obj.value_number is not None:
            value = _fmt_number(obj.value_number)
        else:
            value = obj.value_string
        unit = attr.unit.name if attr.unit_id else ""
        return format_html("<b>{}</b>: {}", attr.name, f"{value} {unit}".strip() or "—")


class VariantItemInline(TabularInline, TranslationTabularInline):
    model = VariantItem
    fk_name = "product"
    extra = 0
    tab = True
    show_count = True
    autocomplete_fields = ("group",)
    fields = ("group", "label", "swatch_hex", "sort_order")
    verbose_name = "Членство в групі варіантів"
    verbose_name_plural = "Групи варіантів (перемикач на картці)"


class RelatedGroupItemInline(TabularInline, TranslationTabularInline):
    model = RelatedGroupItem
    fk_name = "product"
    extra = 0
    tab = True
    show_count = True
    autocomplete_fields = ("group",)
    fields = ("group", "role", "sort_order")
    verbose_name = "Членство в групі супутніх"
    verbose_name_plural = "Супутні товари (взаємно всередині групи)"


class AttributeOptionInline(TabularInline, TranslationTabularInline):
    model = AttributeOption
    extra = 0
    show_count = True
    fields = ("value", "slug", "swatch_hex", "swatch_preview", "sort_order", "needs_review")
    readonly_fields = ("swatch_preview",)
    prepopulated_fields = {"slug": ("value_uk",)}

    @display(description="Кружечок")
    def swatch_preview(self, obj: AttributeOption) -> SafeString | str:
        url = _file_url(obj.swatch_image)
        if url:
            return _thumb(url, size=24, radius=12)
        if obj.swatch_hex:
            return format_html(
                '<div style="width:24px;height:24px;border-radius:50%;background:{};'
                'border:1px solid rgba(128,128,128,.4)"></div>',
                obj.swatch_hex,
            )
        return "—"

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        # PAV.option = PROTECT. Видалення робиться дією «Злити з…» (services/merge.py).
        return False


class VariantItemGroupInline(TabularInline, TranslationTabularInline):
    """Той самий VariantItem, але з боку групи — це і є «конструктор перемикача»."""

    model = VariantItem
    fk_name = "group"
    extra = 0
    show_count = True
    autocomplete_fields = ("product",)
    fields = ("product", "label", "swatch_hex", "sort_order")


class RelatedGroupItemGroupInline(TabularInline, TranslationTabularInline):
    model = RelatedGroupItem
    fk_name = "group"
    extra = 0
    show_count = True
    autocomplete_fields = ("product",)
    fields = ("product", "role", "sort_order")


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------
class ProductAdminForm(forms.ModelForm):
    class Meta:
        model = Product
        # DJ007 попереджає про "__all__" у ModelForm — тут це безпечно: форма використовується
        # ЛИШЕ як admin form, а ModelAdmin звужує набір полів через fieldsets. Перелічувати
        # поля вдруге означало б тримати два розсинхронізовані списки.
        fields = "__all__"  # noqa: DJ007

    def clean(self) -> dict[str, Any]:
        """CheckConstraint'и — друга лінія оборони. Перша має бути ТУТ, з людським текстом."""
        cleaned = super().clean()

        if cleaned.get("availability") == Availability.ON_ORDER and not cleaned.get(
            "order_lead_days"
        ):
            self.add_error(
                "order_lead_days",
                "Для наявності «Під замовлення» строк постачання обов'язковий.",
            )

        base = cleaned.get("base_price")
        currency = cleaned.get("source_currency")
        if base is not None and currency:
            solo = SiteSettings.get_solo()
            preview = compute_uah_price(
                base, currency, solo.usd_rate, cleaned.get("markup_percent"), solo.price_rounding
            )
            if preview is None:
                self.add_error(
                    "source_currency",
                    "Ціну в гривнях обчислити неможливо: курс USD у «Налаштуваннях магазину» "
                    "не заданий або ≤ 0.",
                )
        return cleaned


@admin.register(Product)
class ProductAdmin(WysiwygBodyMixin, ModelAdmin, TabbedTranslationAdmin):
    form = ProductAdminForm

    # Опис товару — rich-HTML із вбудованими фото+підписами (схема монтування, фото окремих
    # функцій із PDF): <figure><img><figcaption>. Той самий TipTap-редактор, що й у cms
    # (NewsPost/StaticPage), а картинки з нього осідають у cms.EditorImage. `short_description` —
    # короткий текстовий тизер, редактор йому не потрібен.
    wysiwyg_fields = ("description",)

    list_display = (
        "thumb",
        "product_header",
        "sku",
        "category",
        "brand",
        "price_badge",
        "availability_badge",
        "condition_badge",
        "data_state_badge",
        # ⚠️ Галочка, якою замовник САМ керує блоком «Хіти продажів» на головній.
        #    Правиться прямо зі списку (list_editable) — інакше зібрати добірку з 12 товарів
        #    означало б 12 разів відкрити картку товару й 12 разів натиснути «Зберегти».
        "is_featured",
        "hotline_enabled",
        "installment_available",
        "synced_at",
    )
    list_display_links = ("product_header",)
    list_editable = ("is_featured", "hotline_enabled", "installment_available")
    list_select_related = ("category", "brand")
    list_fullwidth = True
    list_horizontal_scrollbar_top = True
    list_filter_submit = True
    warn_unsaved_form = True
    list_per_page = 30

    list_filter = (
        # ⚠️ НЕ ("category", RelatedDropdownFilter) — той шукав точний збіг по FK і на
        #    кореневій категорії давав «No results». Див. CategoryBranchFilter.
        CategoryBranchFilter,
        ("brand", RelatedDropdownFilter),
        ("availability", ChoicesDropdownFilter),
        ("source_currency", ChoicesDropdownFilter),
        ("condition", ChoicesDropdownFilter),
        # «Показати всі товари, що зараз у Хітах продажів» — інакше перевірити склад блоку
        # на головній можна тільки очима по списку з 678 рядків.
        ("is_featured", BooleanRadioFilter),
        ("hotline_enabled", BooleanRadioFilter),
        ("installment_available", BooleanRadioFilter),
        ("price_locked", BooleanRadioFilter),
        ("is_active", BooleanRadioFilter),
        ("is_incomplete", IncompleteFilter),
        ("sku_is_surrogate", SurrogateSkuFilter),
        ("denorm_dirty", BooleanRadioFilter),
        ("synced_at", RangeDateTimeFilter),
    )
    search_fields = ("name_uk", "name_ru", "sku", "mpn", "barcode")
    autocomplete_fields = ("category", "brand", "country")
    inlines = (
        ProductImageInline,
        ProductAttributeValueInline,
        VariantItemInline,
        RelatedGroupItemInline,
    )
    actions = (
        "act_featured_on",
        "act_featured_off",
        "act_hotline_on",
        "act_hotline_off",
        "act_installment_on",
        "act_installment_off",
        "act_recalc_price",
        "act_rebuild_denorm",
    )
    actions_detail = ("detail_recalc_price", "detail_rebuild_denorm")

    readonly_fields = (
        "price_display",
        "visibility_reason",
        "is_incomplete",
        "sku_is_surrogate",
        "old_price",
        "usd_rate_used",
        "price_updated_at",
        # package_* габарити — РЕДАГОВАНІ (менеджер може підкоригувати вручну).
        # package_dims_source лишаємо readonly: це статус-замок, ним керує save_model.
        "package_dims_source",
        "warranty_months",
        "name_normalized",
        "source",
        "source_category_ext_id",
        "price_source",
        "winning_offer",
        "last_seen_run",
        "synced_at",
        "denorm_at",
        "denorm_state",
    )

    fieldsets = (
        (
            "Основне",
            {
                "fields": (
                    "name",
                    "slug",
                    "sku",
                    "mpn",
                    "barcode",
                    "category",
                    "brand",
                    "country",
                    "short_description",
                    "description",
                    "is_active",
                    "is_featured",
                )
            },
        ),
        (
            "Видимість на сайті",
            {
                "fields": ("visibility_reason", "is_incomplete", "sku_is_surrogate"),
                "description": "Ці два прапорці ставить СИНХРОНІЗАЦІЯ, руками вони не "
                "редагуються. «Неповний товар» = у прайсі немає артикула та/або фото — такий "
                "рядок ще заповнюють, тому товар схований (<code>is_active = ✗</code>). "
                "Шукати причину не треба: вона написана вище. Щойно в прайсі з'являться "
                "артикул І фото — наступний синк зніме прапорець і покаже товар САМ "
                "(товар, вимкнений руками, синк не вмикає). Повний список таких рядків "
                "з номерами — «Синхронізація → Проблеми в даних».",
            },
        ),
        (
            "Стан товару",
            {
                "fields": ("condition", "condition_note"),
                "description": "«Уцінка» — це ОЗНАКА товару, а не категорія: уцінений холодильник "
                "лишається в «Холодильники» з усіма фільтрами, а сторінка «Уцінка» будується "
                "віртуальною категорією з токеном <code>cond:2</code>.",
            },
        ),
        (
            "Ціна",
            {
                "fields": (
                    "base_price",
                    "source_currency",
                    "markup_percent",
                    "base_old_price",
                    "price_locked",
                    "price_display",
                    "old_price",
                    "usd_rate_used",
                    "price_updated_at",
                ),
                "description": "Ціна в гривнях НЕ вводиться руками — вона обчислюється: "
                "<code>base_price × (1 + націнка) × курс → округлення</code>. "
                "Правте джерела зліва, результат з'явиться у полі «Ціна, грн». "
                "«Ціна зафіксована» блокує ЛИШЕ ціну — наявність синк усе одно оновлює.",
            },
        ),
        (
            "Наявність",
            {"fields": ("availability", "availability_locked", "order_lead_days", "stock_qty")},
        ),
        (
            "Габарити упаковки і гарантія",
            {
                "classes": ("collapse",),
                "fields": (
                    "package_weight_kg",
                    "package_width_cm",
                    "package_height_cm",
                    "package_depth_cm",
                    "package_dims_source",
                    "warranty_months",
                ),
                "description": "READONLY — ці поля не вводяться руками. Вони приходять "
                "З ХАРАКТЕРИСТИК товару (вкладка «Характеристики»): характеристика з роллю "
                "«Габарити упаковки (ВхШхГ)» / «Вага в упаковці» / «Гарантійний термін». "
                "Щоб їх змінити — правте характеристику. У Нову Пошту йде ТІЛЬКИ упаковка; "
                "габарити самого товару лишаються звичайними характеристиками. "
                "Порожньо → калькулятор доставки візьме дефолти категорії, потім — магазину.",
            },
        ),
        (
            "Прапорці",
            {
                "fields": (
                    "hotline_enabled",
                    "installment_available",
                    "installment_max_payments",
                ),
                "description": "«Макс. платежів» порожньо → береться глобальне значення "
                "з «Налаштувань магазину».",
            },
        ),
        ("SEO", {"classes": ("collapse",), "fields": ("seo_title", "seo_description", "og_image")}),
        (
            "Службове",
            {
                "classes": ("collapse",),
                "fields": (
                    "source",
                    "source_category_ext_id",
                    "price_source",
                    "winning_offer",
                    "name_normalized",
                    "last_seen_run",
                    "synced_at",
                    "denorm_at",
                    "denorm_state",
                ),
                "description": "Заповнює синхронізація. Руками не редагується.",
            },
        ),
    )

    # -- список ------------------------------------------------------------
    def get_queryset(self, request: HttpRequest) -> QuerySet[Product]:
        return super().get_queryset(request).prefetch_related("images")

    @display(description="")
    def thumb(self, obj: Product) -> SafeString:
        if obj.main_image_url:
            return _thumb(obj.main_image_url)
        images = list(obj.images.all())
        main = next((i for i in images if i.is_main), images[0] if images else None)
        if main is None:
            return _thumb("")
        return _thumb(_file_url(main.file_thumb, main.file_card, main.file))

    @display(description="Товар", header=True, ordering="name")
    def product_header(self, obj: Product) -> list[str]:
        return [obj.name, f"арт. {obj.sku}"]

    @display(description="Ціна, грн", ordering="price")
    def price_badge(self, obj: Product) -> SafeString:
        source = f"{_fmt_number(obj.base_price)} {obj.source_currency}"
        if obj.source_currency == "USD" and obj.usd_rate_used:
            source += f" × {_fmt_number(obj.usd_rate_used)}"
        if obj.markup_percent:
            source += f" +{_fmt_number(obj.markup_percent)}%"
        lock = " 🔒" if obj.price_locked else ""
        return format_html(
            '<div style="white-space:nowrap"><b>{}</b>{}<br>'
            '<span style="font-size:11px;opacity:.6">{}</span></div>',
            _fmt_money(obj.price),
            lock,
            source,
        )

    @display(description="Наявність", label=AVAILABILITY_COLORS, ordering="availability")
    def availability_badge(self, obj: Product) -> str:
        return obj.get_availability_display()

    @display(description="Стан", label=CONDITION_COLORS, ordering="condition")
    def condition_badge(self, obj: Product) -> str:
        return obj.get_condition_display()

    @display(description="Дані з прайсу", ordering="is_incomplete")
    def data_state_badge(self, obj: Product) -> SafeString:
        """Компактні бейджі: чому товар неповний і чи є в нього справжній артикул."""
        chips = []
        if obj.is_incomplete:
            chips.append(("Неповний — схований", "#dc2626"))
        if obj.sku_is_surrogate:
            chips.append(("Без артикула", "#d97706"))
        if not chips:
            return format_html('<span style="opacity:.45">повний</span>')
        return format_html_join(
            " ",
            '<span style="display:inline-block;padding:1px 7px;border-radius:9px;'
            'font-size:11px;white-space:nowrap;color:#fff;background:{}">{}</span>',
            ((color, text) for text, color in chips),
        )

    # -- форма -------------------------------------------------------------
    @display(description="Чому товар не на сайті")
    def visibility_reason(self, obj: Product) -> SafeString:
        """
        ПРЯМА відповідь на питання «чому цього товару немає на сайті».

        Без неї менеджер шукає причину в п'яти місцях (is_active, категорія, ціна) і зрештою
        пише розробнику. Причина завжди одна з трьох, і всі три видно з самого товару.
        """
        if obj.pk is None:
            return format_html('<span style="opacity:.6">Буде видно після збереження.</span>')

        if obj.is_incomplete:
            missing = []
            if obj.sku_is_surrogate:
                missing.append("артикула (у прайсі «Уточнюється» → ключ згенеровано автоматично)")
            if not obj.images.exists():
                missing.append("фото")
            return format_html(
                '<b style="color:#dc2626">Схований: неповні дані в прайсі.</b><br>'
                "Немає: {}.<br>"
                '<span style="opacity:.7">Виправляти треба В ПРАЙСІ (Google-таблиця), '
                "не тут: наступний синк перезапише. Щойно рядок допишуть — товар "
                "з'явиться на сайті сам.</span>",
                ", ".join(missing) or "даних, обов'язкових для показу",
            )
        if not obj.is_active:
            return format_html(
                '<b style="color:#d97706">Схований вручну</b> (прапорець «Активний» знято). '
                "Дані з прайсу повні — синк цей товар назад НЕ вмикає."
            )
        return format_html('<b style="color:#16a34a">Показується на сайті.</b> Дані повні.')

    @display(description="Ціна, грн (обчислена)")
    def price_display(self, obj: Product) -> SafeString:
        if obj.pk is None or obj.price is None:
            return format_html('<span style="opacity:.6">Буде обчислена при збереженні.</span>')
        solo = SiteSettings.get_solo()
        formula = (
            f"{_fmt_number(obj.base_price)} {obj.source_currency}"
            f" × (1 + {_fmt_number(obj.markup_percent)}%)"
        )
        if obj.source_currency == "USD":
            formula += f" × {_fmt_number(obj.usd_rate_used or solo.usd_rate)}"
        formula += f" → {solo.get_price_rounding_display()}"
        return format_html(
            '<b style="font-size:18px">{} ₴</b>'
            '<div style="font-size:11px;opacity:.6;margin-top:2px">{}</div>',
            _fmt_money(obj.price),
            formula,
        )

    @display(description="Денормалізація")
    def denorm_state(self, obj: Product) -> SafeString:
        if obj.pk is None:
            return format_html("—")
        specs = obj.specs_json or []
        tokens = obj.filter_tokens or []
        state = "ПОТРЕБУЄ ПЕРЕБУДОВИ" if obj.denorm_dirty else "актуальна"
        return format_html(
            "{} · характеристик у проєкції: {} · токенів фільтра: {}",
            state,
            len(specs),
            len(tokens),
        )

    # -- збереження --------------------------------------------------------
    def save_model(self, request: HttpRequest, obj: Product, form: Any, change: bool) -> None:
        """Ціна рахується ТУТ (вона readonly у формі), історія — PG-тригером з reason='manual'."""
        if not change:
            obj.source = ProductSource.MANUAL

        # Ручна правка габаритів упаковки → ставимо замок MANUAL, щоб синк їх НЕ затер
        # (sync/services.py::_apply_package_and_warranty перевіряє саме package_dims_source).
        # Очищено всі чотири → знімаємо замок, хай синк знову заповнює з таблиці характеристик.
        _pkg = ("package_weight_kg", "package_width_cm", "package_height_cm", "package_depth_cm")
        if any(f in form.changed_data for f in _pkg):
            if any(getattr(obj, f) is not None for f in _pkg):
                obj.package_dims_source = ProductSource.MANUAL
            else:
                obj.package_dims_source = ProductSource.SHEET

        obj.denorm_dirty = True  # будь-яка ручна правка робить проєкцію застарілою

        with price_reason("manual"):
            try:
                from catalog.services.pricing import apply_pricing

                apply_pricing(obj)
            except PriceError as exc:  # ловить ProductAdminForm.clean, але хай буде
                messages.error(request, str(exc))
                obj.price = obj.price or Decimal(0)
            super().save_model(request, obj, form, change)

    def save_formset(self, request: HttpRequest, form: Any, formset: Any, change: bool) -> None:
        """Фото зберігаються ОКРЕМИМ шляхом — інакше `uniq_main_image` валить формсет."""
        if formset.model is ProductImage:
            save_image_formset(form.instance, formset)
        else:
            super().save_formset(request, form, formset, change)

        if formset.model in (ProductImage, ProductAttributeValue, VariantItem, RelatedGroupItem):
            Product.objects.filter(pk=form.instance.pk).update(denorm_dirty=True)

    # -- масові дії --------------------------------------------------------
    @admin.action(description="Додати в «Хіти продажів» (головна)")
    def act_featured_on(self, request: HttpRequest, queryset: QuerySet[Product]) -> None:
        n = queryset.update(is_featured=True)
        # ⚠️ `queryset.update()` НЕ шле post_save → сигнал інвалідації кешу добірок не спрацює.
        #    Без цього рядка замовник поставив би 12 хітів і 5 хвилин дивився на стару головну.
        invalidate_collections_cache()

        hidden = queryset.filter(is_active=False).count()
        msg = f"Додано в «Хіти продажів»: {n} товар(ів)."
        if hidden:
            # Чесно попереджаємо, а не мовчимо: неактивний товар у добірку НЕ потрапить
            # (collections._base_qs фільтрує is_active=True), і замовник шукав би його на головній.
            msg += f" З них прихованих (is_active ✗) — {hidden}: на головній вони НЕ з'являться."
        messages.success(request, msg)

    @admin.action(description="Прибрати з «Хітів продажів»")
    def act_featured_off(self, request: HttpRequest, queryset: QuerySet[Product]) -> None:
        n = queryset.update(is_featured=False)
        invalidate_collections_cache()
        messages.success(request, f"Прибрано з «Хітів продажів»: {n} товар(ів).")

    @admin.action(description="Hotline: увімкнути")
    def act_hotline_on(self, request: HttpRequest, queryset: QuerySet[Product]) -> None:
        n = queryset.update(hotline_enabled=True)
        messages.success(request, f"Hotline увімкнено для {n} товар(ів).")

    @admin.action(description="Hotline: вимкнути")
    def act_hotline_off(self, request: HttpRequest, queryset: QuerySet[Product]) -> None:
        n = queryset.update(hotline_enabled=False)
        messages.success(request, f"Hotline вимкнено для {n} товар(ів).")

    @admin.action(description="Оплата частинами: увімкнути")
    def act_installment_on(self, request: HttpRequest, queryset: QuerySet[Product]) -> None:
        n = queryset.update(installment_available=True, denorm_dirty=True)
        messages.success(request, f"Оплату частинами увімкнено для {n} товар(ів).")

    @admin.action(description="Оплата частинами: вимкнути")
    def act_installment_off(self, request: HttpRequest, queryset: QuerySet[Product]) -> None:
        n = queryset.update(installment_available=False, denorm_dirty=True)
        messages.success(request, f"Оплату частинами вимкнено для {n} товар(ів).")

    @admin.action(description="Перерахувати ціну за поточним курсом")
    def act_recalc_price(self, request: HttpRequest, queryset: QuerySet[Product]) -> None:
        locked = queryset.filter(price_locked=True).count()
        target = queryset.filter(price_locked=False)
        try:
            n = recalc_products(target)
        except PriceError as exc:
            messages.error(request, str(exc))
            return
        msg = f"Ціну перераховано для {n} товар(ів)."
        if locked:
            msg += f" Пропущено із зафіксованою ціною: {locked}."
        messages.success(request, msg)

    @admin.action(description="Перебудувати денормалізацію")
    def act_rebuild_denorm(self, request: HttpRequest, queryset: QuerySet[Product]) -> None:
        ids = list(queryset.values_list("pk", flat=True))
        n = queryset.update(denorm_dirty=True)
        transaction.on_commit(lambda: rebuild_product_denorm.delay(ids))
        messages.success(
            request,
            f"{n} товар(ів) поставлено на перебудову проєкції (specs_json, filter_tokens). "
            "Оновіть сторінку за хвилину.",
        )

    # -- дії на картці товару ---------------------------------------------
    @action(
        description="Перерахувати ціну",
        url_path="recalc-price",
        permissions=["change"],
        icon="calculate",
        variant=ActionVariant.PRIMARY,
    )
    def detail_recalc_price(self, request: HttpRequest, object_id: int) -> HttpResponse:
        product = self.get_object(request, object_id)
        if product is None:
            messages.error(request, "Товар не знайдено.")
        elif product.price_locked:
            messages.warning(
                request, "Ціна зафіксована («Ціна зафіксована» = ✓). Перерахунок пропущено."
            )
        else:
            try:
                recalc_products([product])
                messages.success(request, f"Ціну перераховано: {_fmt_money(product.price)} ₴.")
            except PriceError as exc:
                messages.error(request, str(exc))
        return self._back_to_change(object_id)

    @action(
        description="Перебудувати денормалізацію",
        url_path="rebuild-denorm",
        permissions=["change"],
        icon="autorenew",
    )
    def detail_rebuild_denorm(self, request: HttpRequest, object_id: int) -> HttpResponse:
        Product.objects.filter(pk=object_id).update(denorm_dirty=True)
        transaction.on_commit(lambda: rebuild_product_denorm.delay([int(object_id)]))
        messages.success(
            request,
            "Товар поставлено на перебудову проєкції. Оновіть сторінку за хвилину.",
        )
        return self._back_to_change(object_id)

    def _back_to_change(self, object_id: Any) -> HttpResponse:
        from django.shortcuts import redirect

        return redirect(reverse("admin:catalog_product_change", args=[object_id]))


# ---------------------------------------------------------------------------
# Category — ДЕРЕВО
# ---------------------------------------------------------------------------
@admin.register(Category)
class CategoryAdmin(ModelAdmin, TabbedTranslationAdmin):
    """Дерево, а не плаский список.

    Порядок рядків — за матеріалізованим шляхом (`path`), тому дитина завжди стоїть одразу під
    батьком: лексикографічне сортування шляхів «877» < «877/560» < «878» і є обхід дерева.
    `sort_order` — це порядок НА САЙТІ (мегаменю), він редагується прямо в списку.
    """

    list_display = (
        "tree_name",
        "external_id",
        "icon_preview",
        "products_badge",
        "sort_order",
        "is_active",
        "show_in_megamenu",
        "show_in_quick_nav",
        "hotline_enabled_default",
        "hotline_category",
    )
    list_display_links = ("tree_name",)
    # ⚠️ `show_in_quick_nav` — саме тут, у СПИСКУ: рядок під шапкою збирають, порівнюючи
    #   категорії між собою, а не відкриваючи кожну по черзі.
    list_editable = (
        "sort_order",
        "is_active",
        "show_in_megamenu",
        "show_in_quick_nav",
        "hotline_enabled_default",
    )
    list_select_related = ("parent", "hotline_category")
    list_filter = (
        ("is_active", BooleanRadioFilter),
        ("show_in_megamenu", BooleanRadioFilter),
        ("show_in_quick_nav", BooleanRadioFilter),
        ("hotline_enabled_default", BooleanRadioFilter),
        ("parent", RelatedDropdownFilter),
    )
    list_filter_submit = True
    list_per_page = 100  # дерево — ~59 вузлів, посторінковий тр tree ніхто не любить
    ordering = ("path",)
    search_fields = ("name_uk", "name_ru", "external_id", "path")
    autocomplete_fields = ("parent",)
    prepopulated_fields = {"slug_uk": ("name_uk",)}
    actions = ("act_hotline_branch_on", "act_hotline_branch_off")
    actions_detail = ("detail_hotline_cascade",)
    warn_unsaved_form = True

    readonly_fields = ("path", "depth", "products_count", "is_service")
    fieldsets = (
        # ⚠️ «Медіа» СТОЇТЬ ПЕРШИМ НАВМИСНО. Раніше блок був третім, і поле іконки
        # опинялось нижче половини сторінки — замовник просто не знаходив, «де
        # редагувати фото іконок категорій». Емблеми зараз заводять активно, тому
        # найчастіша дія має бути найпомітнішою, а не технічні slug/external_id.
        (
            "Медіа",
            {
                "fields": ("icon", "image"),
                "description": (
                    "Два РІЗНІ зображення. <b>Міні-емблема</b> — значок групи в меню каталогу "
                    "(кругла іконка біля назви). <b>Плитка</b> — велике фото: для кореневих "
                    "груп воно йде на головну, для ПІДГРУП — на сторінку батьківської "
                    "категорії. Тобто плитку має сенс заливати на будь-якому рівні.<br>"
                    "<b>Порожнє поле = типовий значок сайту</b>, підібраний під категорію. "
                    "Щоб повернутися до нього, поставте галочку «Очистити» біля файлу.<br>"
                    "🔴 <b>Формат має значення.</b> SVG перефарбовується в колір теми й "
                    "виглядає як решта іконок. PNG/WebP показується ЯК Є — тому знімок екрана "
                    "чи фото товару в кружечку 20×20 перетворяться на нерозбірливу пляму. "
                    "Для емблем беріть КВАДРАТНИЙ SVG на прозорому фоні, до 64 КБ."
                ),
            },
        ),
        (
            "Основне",
            {
                "fields": (
                    "name",
                    "slug",
                    "external_id",
                    "parent",
                    "sort_order",
                    "is_active",
                    "show_in_megamenu",
                    "show_in_quick_nav",
                    "description",
                )
            },
        ),
        (
            "Hotline",
            {
                "fields": ("hotline_category", "hotline_enabled_default"),
                "description": "«Категорія Hotline» порожня → товари цієї категорії у фід "
                "НЕ ПОТРАПЛЯЮТЬ узагалі. «Нові товари — на Hotline» успадковується товаром "
                "при створенні синком і НЕ перезаписує вже виставлений вручну прапорець. "
                "Щоб перемкнути прапорець на ВЖЕ ІСНУЮЧИХ товарах гілки — кнопка "
                "«Hotline на гілку» вгорі сторінки.",
            },
        ),
        (
            "Віртуальна категорія",
            {
                "classes": ("collapse",),
                "fields": ("virtual_tokens",),
                "description": "Непорожньо → категорія не має власних товарів через FK, а її "
                "лістинг збирається за токенами. «Уцінка» = <code>cond:2</code>, "
                "«Акції» = <code>sale:1</code> (товари, у яких заповнена стара ціна). "
                "Нуль дублювання товарів у дереві.<br>"
                "🔴 Тут раніше було написано <code>promo:1</code> — такого токена НІКОЛИ не "
                "існувало, і категорія з ним мовчки лишалась порожньою. Правильний — "
                "<code>sale:1</code>.",
            },
        ),
        (
            "Габарити упаковки за замовчуванням",
            {
                "classes": ("collapse",),
                "fields": (
                    "default_weight_kg",
                    "default_width_cm",
                    "default_height_cm",
                    "default_depth_cm",
                ),
                "description": "Фолбек калькулятора Нової Пошти: використовується, ЛИШЕ коли в "
                "самого товару порожні габарити упаковки.",
            },
        ),
        ("SEO", {"classes": ("collapse",), "fields": ("seo_title", "seo_description", "og_image")}),
        (
            "Службове",
            {
                "classes": ("collapse",),
                "fields": ("path", "depth", "products_count", "is_service"),
            },
        ),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Category]:
        return super().get_queryset(request).annotate(live_products=Count("products"))

    @display(description="Категорія", ordering="path")
    def tree_name(self, obj: Category) -> SafeString:
        prefix = "└─ " if obj.depth else ""
        return format_html(
            '<span style="display:inline-block;padding-left:{}px">{}<b>{}</b></span>',
            obj.depth * 24,
            prefix,
            obj.name,
        )

    @display(description="Емблема")
    def icon_preview(self, obj: Category) -> SafeString:
        return _thumb(_file_url(obj.icon, obj.image), size=28, radius=4)

    @display(description="Товарів", ordering="live_products")
    def products_badge(self, obj: Category) -> SafeString:
        if obj.virtual_tokens:
            return format_html('<span title="{}">віртуальна</span>', ", ".join(obj.virtual_tokens))
        return format_html("{}", obj.live_products)

    # -- каскад Hotline (ВИМОГА ТЗ) ---------------------------------------
    @action(
        description="Hotline на гілку",
        url_path="hotline-cascade",
        permissions=["change"],
        icon="hub",
        variant=ActionVariant.WARNING,
        dialog={
            "title": "Hotline: категорія та всі підкатегорії",
            "description": "Прапорець «Розміщувати на Hotline» буде перезаписано на ВСІХ "
            "товарах гілки. Дію не можна скасувати однією кнопкою.",
            "form_class": HotlineCascadeForm,
            "form_submit_text": "Застосувати",
        },
    )
    def detail_hotline_cascade(
        self, request: HttpRequest, form: HotlineCascadeForm, object_id: int
    ) -> HttpResponse:
        category = self.get_object(request, object_id)
        if category is None:
            messages.error(request, "Категорію не знайдено.")
            return _hx_redirect(reverse("admin:catalog_category_changelist"))

        enabled = form.cleaned_data["mode"] == "on"
        include = form.cleaned_data["include_descendants"]
        n_products, n_cats = set_hotline_for_category(
            category, enabled=enabled, include_descendants=include
        )
        verb = "увімкнено" if enabled else "вимкнено"
        messages.success(
            request,
            f"Hotline {verb}: товарів — {n_products}, категорій — {n_cats} "
            f"(включно з «{category.name}»).",
        )
        return _hx_redirect(reverse("admin:catalog_category_change", args=[object_id]))

    @admin.action(description="Hotline: увімкнути на гілку (з підкатегоріями)")
    def act_hotline_branch_on(self, request: HttpRequest, queryset: QuerySet[Category]) -> None:
        self._branch(request, queryset, enabled=True)

    @admin.action(description="Hotline: вимкнути на гілку (з підкатегоріями)")
    def act_hotline_branch_off(self, request: HttpRequest, queryset: QuerySet[Category]) -> None:
        self._branch(request, queryset, enabled=False)

    def _branch(self, request: HttpRequest, queryset: QuerySet[Category], *, enabled: bool) -> None:
        total_p = total_c = 0
        for category in queryset:
            p, c = set_hotline_for_category(category, enabled=enabled, include_descendants=True)
            total_p += p
            total_c += c
        verb = "увімкнено" if enabled else "вимкнено"
        messages.success(request, f"Hotline {verb}: товарів — {total_p}, категорій — {total_c}.")


# ---------------------------------------------------------------------------
# Словники: Brand / Country
# ---------------------------------------------------------------------------
class NoDeleteMixin:
    """Видалення заборонене — замість нього «Злити з…» (див. services/merge.py)."""

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Brand)
class BrandAdmin(NoDeleteMixin, ModelAdmin):
    list_display = (
        "logo_preview",
        "name",
        "slug",
        "aliases_badge",
        "products_badge",
        "is_active",
        "review_badge",
    )
    list_display_links = ("name",)
    list_editable = ("is_active",)
    list_filter = (("needs_review", BooleanRadioFilter), ("is_active", BooleanRadioFilter))
    list_filter_submit = True
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    actions = ("act_mark_reviewed",)
    actions_detail = ("detail_merge",)
    fieldsets = (
        ("Бренд", {"fields": ("name", "slug", "logo", "is_active")}),
        (
            "Синоніми і перевірка",
            {
                "fields": ("aliases", "needs_review"),
                "description": "Синоніми — усі написання, якими бренд приходить у прайсах "
                "(«SHARP», «sharp», «Шарп»). За ними синк упізнає бренд і не плодить дублів. "
                "«Потребує перевірки» = бренд створено автоматично синком. "
                "Дубль не видаляють — його ЗЛИВАЮТЬ (кнопка вгорі).",
            },
        ),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Brand]:
        return super().get_queryset(request).annotate(live_products=Count("product"))

    @display(description="")
    def logo_preview(self, obj: Brand) -> SafeString:
        return _thumb(_file_url(obj.logo), size=32, radius=4)

    @display(description="Синоніми")
    def aliases_badge(self, obj: Brand) -> str:
        return ", ".join(obj.aliases) if obj.aliases else "—"

    @display(description="Товарів", ordering="live_products")
    def products_badge(self, obj: Brand) -> int:
        return obj.live_products

    @display(description="Статус", label={"потребує перевірки": "warning", "перевірено": "success"})
    def review_badge(self, obj: Brand) -> str:
        return "потребує перевірки" if obj.needs_review else "перевірено"

    @admin.action(description="Позначити перевіреними")
    def act_mark_reviewed(self, request: HttpRequest, queryset: QuerySet[Brand]) -> None:
        n = queryset.update(needs_review=False)
        messages.success(request, f"Позначено перевіреними: {n}.")

    @action(
        description="Злити з іншим брендом",
        url_path="merge",
        permissions=["change"],
        icon="merge",
        variant=ActionVariant.DANGER,
        dialog={
            "title": "Злити бренд-дубль",
            "description": "Усі товари цього бренду переїдуть на обраний. Цей бренд буде "
            "видалено, а його назва і синоніми стануть синонімами переможця.",
            "form_class": BrandMergeForm,
            "form_submit_text": "Злити",
        },
    )
    def detail_merge(
        self, request: HttpRequest, form: BrandMergeForm, object_id: int
    ) -> HttpResponse:
        src = self.get_object(request, object_id)
        dst = form.cleaned_data["target"]
        try:
            moved = merge_brands(src, dst)
        except MergeError as exc:
            messages.error(request, str(exc))
            return _hx_redirect(reverse("admin:catalog_brand_change", args=[object_id]))
        messages.success(request, f"Злито. Перенесено товарів: {moved}. Залишився «{dst.name}».")
        return _hx_redirect(reverse("admin:catalog_brand_changelist"))


@admin.register(Country)
class CountryAdmin(NoDeleteMixin, ModelAdmin, TabbedTranslationAdmin):
    list_display = (
        "flag_preview",
        "name",
        "code",
        "slug",
        "aliases_badge",
        "products_badge",
        "review_badge",
    )
    list_display_links = ("name",)
    list_filter = (("needs_review", BooleanRadioFilter),)
    list_filter_submit = True
    search_fields = ("name_uk", "name_ru", "code", "slug")
    actions = ("act_mark_reviewed",)
    actions_detail = ("detail_merge",)
    fieldsets = (
        ("Країна", {"fields": ("name", "code", "slug", "flag")}),
        (
            "Синоніми і перевірка",
            {
                "fields": ("aliases", "needs_review"),
                "description": "Дубль не видаляють — його ЗЛИВАЮТЬ (кнопка вгорі): "
                "Product.country = SET_NULL, тобто видалення просто обнулило б країну в товарах.",
            },
        ),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Country]:
        return super().get_queryset(request).annotate(live_products=Count("product"))

    @display(description="")
    def flag_preview(self, obj: Country) -> SafeString:
        return _thumb(_file_url(obj.flag), size=24, radius=3)

    @display(description="Синоніми")
    def aliases_badge(self, obj: Country) -> str:
        return ", ".join(obj.aliases) if obj.aliases else "—"

    @display(description="Товарів", ordering="live_products")
    def products_badge(self, obj: Country) -> int:
        return obj.live_products

    @display(description="Статус", label={"потребує перевірки": "warning", "перевірено": "success"})
    def review_badge(self, obj: Country) -> str:
        return "потребує перевірки" if obj.needs_review else "перевірено"

    @admin.action(description="Позначити перевіреними")
    def act_mark_reviewed(self, request: HttpRequest, queryset: QuerySet[Country]) -> None:
        n = queryset.update(needs_review=False)
        messages.success(request, f"Позначено перевіреними: {n}.")

    @action(
        description="Злити з іншою країною",
        url_path="merge",
        permissions=["change"],
        icon="merge",
        variant=ActionVariant.DANGER,
        dialog={
            "title": "Злити країну-дубль",
            "description": "Усі товари цієї країни переїдуть на обрану.",
            "form_class": CountryMergeForm,
            "form_submit_text": "Злити",
        },
    )
    def detail_merge(
        self, request: HttpRequest, form: CountryMergeForm, object_id: int
    ) -> HttpResponse:
        src = self.get_object(request, object_id)
        dst = form.cleaned_data["target"]
        try:
            moved = merge_countries(src, dst)
        except MergeError as exc:
            messages.error(request, str(exc))
            return _hx_redirect(reverse("admin:catalog_country_change", args=[object_id]))
        messages.success(request, f"Злито. Перенесено товарів: {moved}. Залишилась «{dst.name}».")
        return _hx_redirect(reverse("admin:catalog_country_changelist"))


# ---------------------------------------------------------------------------
# Словник характеристик
# ---------------------------------------------------------------------------
@admin.register(AttributeGroup)
class AttributeGroupAdmin(ModelAdmin, TabbedTranslationAdmin):
    list_display = ("name", "code", "sort_order", "is_active", "attributes_badge")
    list_display_links = ("name",)
    list_editable = ("sort_order", "is_active")
    search_fields = ("name_uk", "name_ru", "code")
    prepopulated_fields = {"code": ("name_uk",)}
    fieldsets = (
        (
            "Група характеристик",
            {
                "fields": ("name", "code", "sort_order", "is_active"),
                "description": "Групи — це підзаголовки в блоці «Характеристики» на картці: "
                "«Основні», «Габаритні розміри». Порядок груп — <code>sort_order</code>, "
                "усередині групи — порядок самих характеристик.",
            },
        ),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[AttributeGroup]:
        return super().get_queryset(request).annotate(attr_count=Count("attributes"))

    @display(description="Характеристик", ordering="attr_count")
    def attributes_badge(self, obj: AttributeGroup) -> int:
        return obj.attr_count


@admin.register(Unit)
class UnitAdmin(ModelAdmin, TabbedTranslationAdmin):
    list_display = ("name", "code", "aliases_badge", "attributes_badge", "review_badge")
    list_display_links = ("name",)
    list_filter = (("needs_review", BooleanRadioFilter),)
    search_fields = ("code", "name_uk", "name_ru")
    fieldsets = (
        (
            "Одиниця виміру",
            {
                "fields": ("name", "code", "aliases", "needs_review"),
                "description": "СЛОВНИК: ~25 рядків на весь каталог. Переклад робиться РІВНО "
                "ОДИН РАЗ («міс» → «мес») і застосовується до всіх 10 000 товарів. "
                "Одиниця клеїться до ЗНАЧЕННЯ на картці («284 мм»), а не до назви "
                "характеристики — тому в назві характеристики одиниці бути НЕ МОЖЕ.",
            },
        ),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Unit]:
        return super().get_queryset(request).annotate(attr_count=Count("attributes"))

    @display(description="Синоніми")
    def aliases_badge(self, obj: Unit) -> str:
        return ", ".join(obj.aliases) if obj.aliases else "—"

    @display(description="Характеристик", ordering="attr_count")
    def attributes_badge(self, obj: Unit) -> int:
        return obj.attr_count

    @display(description="Статус", label={"потребує перевірки": "warning", "перевірено": "success"})
    def review_badge(self, obj: Unit) -> str:
        return "потребує перевірки" if obj.needs_review else "перевірено"


class AttributeAdminForm(forms.ModelForm):
    class Meta:
        model = Attribute
        # Див. пояснення у ProductAdminForm вище.
        fields = "__all__"  # noqa: DJ007

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        widget = cleaned.get("filter_widget")
        buckets = cleaned.get("range_buckets")

        # CheckConstraint attr_buckets_required_for_range — ловимо ДО IntegrityError.
        if widget == Attribute.FilterWidget.RANGE_BUCKETS and not buckets:
            self.add_error(
                "range_buckets",
                "Для віджета «Діапазон (бакети)» потрібні самі бакети, напр. "
                "[[0,199],[200,299],[300,399],[400,null]]. "
                "Підказку згенерує manage.py suggest_buckets --attr=<код>.",
            )

        # UniqueConstraint uniq_attr_role — рівно одна характеристика на роль.
        role = cleaned.get("role")
        if role:
            clash = Attribute.objects.filter(role=role).exclude(pk=self.instance.pk).first()
            if clash is not None:
                self.add_error(
                    "role",
                    f"Роль «{dict(Attribute.Role.choices)[role]}» вже закріплена за "
                    f"характеристикою «{clash.name}». Дві характеристики з однією роллю = "
                    "недетермінований розмір посилки в Новій Пошті.",
                )
        return cleaned


@admin.register(Attribute)
class AttributeAdmin(ModelAdmin, TabbedTranslationAdmin):
    form = AttributeAdminForm
    list_display = (
        "name",
        "code",
        "group",
        "unit",
        "value_type",
        "filter_badge",
        "is_filterable",
        "sort_order",
        "role",
        "review_badge",
    )
    list_display_links = ("name",)
    list_editable = ("is_filterable", "sort_order")
    list_select_related = ("group", "unit")
    list_filter = (
        ("group", RelatedDropdownFilter),
        ("value_type", ChoicesDropdownFilter),
        ("filter_widget", ChoicesDropdownFilter),
        ("role", ChoicesDropdownFilter),
        ("is_filterable", BooleanRadioFilter),
        ("needs_review", BooleanRadioFilter),
    )
    list_filter_submit = True
    search_fields = ("name_uk", "name_ru", "code")
    autocomplete_fields = ("group", "unit")
    filter_horizontal = ("categories",)
    prepopulated_fields = {"code": ("name_uk",)}
    inlines = (AttributeOptionInline,)
    actions = ("act_mark_reviewed", "act_enable_filter", "act_disable_filter")

    fieldsets = (
        (
            "Характеристика",
            {
                "fields": ("name", "code", "group", "unit", "value_type", "sort_order"),
                "description": "У НАЗВІ одиниці бути не може: «Висота» + одиниця «мм», "
                "а не «Висота (мм)». На картці це рендериться як <b>Висота: 284 мм</b>.<br>"
                "<b>Порядок</b> — це місце характеристики <b>всередині своєї групи</b> на "
                "сайті: менше число = вище. Порядок самих груп задається в "
                "«Групи характеристик». Зручніше правити пачкою: у списку характеристик "
                "відфільтруйте за групою і проставте 10, 20, 30… прямо в колонці «Порядок» "
                "(поле редагується просто в списку). Проміжки лишайте — буде куди вставити "
                "нову характеристику, не перенумеровуючи все.<br>"
                "Зміна порядку доїжджає на сайт не миттєво: картки товарів перебудовуються "
                "фоново, зазвичай протягом 5 хвилин.",
            },
        ),
        (
            "Фільтрація",
            {
                "fields": ("is_filterable", "filter_widget", "range_buckets"),
                "description": "Фільтруються ТІЛЬКИ характеристики — опис товару у фасети не "
                "потрапляє ніколи. Характеристики, створені синком автоматично, приходять з "
                "<code>is_filterable = ✗</code>: вони одразу видно в картці, але у фільтри "
                "потрапляють лише після вашого схвалення. Це те, що рятує фасети від сміття "
                "(«Sharp» vs «SHARP»).",
            },
        ),
        (
            "Показ",
            {"fields": ("show_in_card", "is_comparable", "categories")},
        ),
        (
            "Службова роль",
            {
                "classes": ("collapse",),
                "fields": ("role", "hotline_param_name"),
                "description": "«Роль» — ким характеристика працює, КРІМ себе самої. Саме через "
                "неї синк заповнює габарити упаковки, вагу і гарантію товару — без хардкоду назв "
                "у коді. Рівно ОДНА характеристика на роль.",
            },
        ),
        (
            "Синоніми і перевірка",
            {
                "fields": ("aliases", "needs_review"),
                "description": "Синоніми — сирі назви з прайсів («Общий объем», «Об'єм, л»).",
            },
        ),
    )

    @display(
        description="Фільтр",
        label={
            "Не фільтрувати": "danger",
            "Чекбокси": "success",
            "Випадаючий список": "success",
            "Діапазон (бакети)": "info",
            "Діапазон (слайдер)": "warning",
        },
    )
    def filter_badge(self, obj: Attribute) -> str:
        return obj.get_filter_widget_display()

    @display(description="Статус", label={"потребує перевірки": "warning", "перевірено": "success"})
    def review_badge(self, obj: Attribute) -> str:
        return "потребує перевірки" if obj.needs_review else "перевірено"

    @admin.action(description="Позначити перевіреними")
    def act_mark_reviewed(self, request: HttpRequest, queryset: QuerySet[Attribute]) -> None:
        n = queryset.update(needs_review=False)
        messages.success(request, f"Позначено перевіреними: {n}.")

    @admin.action(description="Увімкнути фільтрацію (чекбокси)")
    def act_enable_filter(self, request: HttpRequest, queryset: QuerySet[Attribute]) -> None:
        # range_buckets НЕ чіпаємо: перемикання в bucket-режим без бакетів = IntegrityError.
        n = queryset.exclude(value_type=Attribute.ValueType.NUMBER).update(
            is_filterable=True, filter_widget=Attribute.FilterWidget.CHECKBOX, needs_review=False
        )
        skipped = queryset.filter(value_type=Attribute.ValueType.NUMBER).count()
        messages.success(request, f"Фільтрацію увімкнено: {n}.")
        if skipped:
            messages.warning(
                request,
                f"Числових характеристик пропущено: {skipped}. Їм потрібні бакети діапазонів — "
                "виставте віджет і бакети в картці характеристики.",
            )

    @admin.action(description="Вимкнути фільтрацію")
    def act_disable_filter(self, request: HttpRequest, queryset: QuerySet[Attribute]) -> None:
        n = queryset.update(is_filterable=False, filter_widget=Attribute.FilterWidget.NONE)
        messages.success(request, f"Фільтрацію вимкнено: {n}.")


@admin.register(AttributeOption)
class AttributeOptionAdmin(NoDeleteMixin, ModelAdmin, TabbedTranslationAdmin):
    list_display = (
        "value",
        "attribute",
        "slug",
        "swatch_preview",
        "usage_badge",
        "sort_order",
        "review_badge",
    )
    list_display_links = ("value",)
    list_editable = ("sort_order",)
    list_select_related = ("attribute",)
    list_filter = (
        ("attribute", RelatedDropdownFilter),
        ("needs_review", BooleanRadioFilter),
    )
    list_filter_submit = True
    search_fields = ("value_uk", "value_ru", "slug")
    autocomplete_fields = ("attribute",)
    prepopulated_fields = {"slug": ("value_uk",)}
    actions = ("act_mark_reviewed",)
    actions_detail = ("detail_merge",)
    fieldsets = (
        (
            "Варіант",
            {
                "fields": ("attribute", "value", "slug", "sort_order"),
                "description": "<code>slug</code> — ВІЧНИЙ ключ токена фільтра й URL "
                "(<code>?color=sriblyastyi</code>). Він будується з української назви і НЕ "
                "перекладається — тому перемикання мови не змінює результат фільтра.",
            },
        ),
        (
            "Кружечок (для перемикача варіантів)",
            {"classes": ("collapse",), "fields": ("swatch_hex", "swatch_image")},
        ),
        ("Синоніми і перевірка", {"fields": ("aliases", "needs_review")}),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[AttributeOption]:
        return super().get_queryset(request).annotate(usage=Count("values"))

    @display(description="Кружечок")
    def swatch_preview(self, obj: AttributeOption) -> SafeString | str:
        url = _file_url(obj.swatch_image)
        if url:
            return _thumb(url, size=24, radius=12)
        if obj.swatch_hex:
            return format_html(
                '<div style="width:24px;height:24px;border-radius:50%;background:{};'
                'border:1px solid rgba(128,128,128,.4)"></div>',
                obj.swatch_hex,
            )
        return "—"

    @display(description="Товарів", ordering="usage")
    def usage_badge(self, obj: AttributeOption) -> int:
        return obj.usage

    @display(description="Статус", label={"потребує перевірки": "warning", "перевірено": "success"})
    def review_badge(self, obj: AttributeOption) -> str:
        return "потребує перевірки" if obj.needs_review else "перевірено"

    @admin.action(description="Позначити перевіреними")
    def act_mark_reviewed(self, request: HttpRequest, queryset: QuerySet[AttributeOption]) -> None:
        n = queryset.update(needs_review=False)
        messages.success(request, f"Позначено перевіреними: {n}.")

    @action(
        description="Злити з іншим варіантом",
        url_path="merge",
        permissions=["change"],
        icon="merge",
        variant=ActionVariant.DANGER,
        dialog={
            "title": "Злити варіант-дубль",
            "description": "Значення товарів переїдуть на обраний варіант. Злиття можливе лише "
            "в межах ОДНІЄЇ характеристики.",
            "form_class": AttributeOptionMergeForm,
            "form_submit_text": "Злити",
        },
    )
    def detail_merge(
        self, request: HttpRequest, form: AttributeOptionMergeForm, object_id: int
    ) -> HttpResponse:
        src = self.get_object(request, object_id)
        dst = form.cleaned_data["target"]
        try:
            moved = merge_options(src, dst)
        except MergeError as exc:
            messages.error(request, str(exc))
            return _hx_redirect(reverse("admin:catalog_attributeoption_change", args=[object_id]))
        messages.success(request, f"Злито. Зачеплено товарів: {moved}. Залишився «{dst.value}».")
        return _hx_redirect(reverse("admin:catalog_attributeoption_changelist"))


# ---------------------------------------------------------------------------
# Групи варіантів / супутніх
# ---------------------------------------------------------------------------
@admin.register(VariantGroup)
class VariantGroupAdmin(ModelAdmin, TabbedTranslationAdmin):
    list_display = ("name", "axis_attribute", "widget_badge", "items_badge", "is_active")
    list_display_links = ("name",)
    list_editable = ("is_active",)
    list_select_related = ("axis_attribute",)
    list_filter = (("widget", ChoicesDropdownFilter), ("is_active", BooleanRadioFilter))
    search_fields = ("name_uk", "name_ru")
    autocomplete_fields = ("axis_attribute",)
    inlines = (VariantItemGroupInline,)
    fieldsets = (
        (
            "Група варіантів",
            {
                "fields": ("name", "axis_attribute", "widget", "is_active"),
                "description": "Вісь перемикача — БУДЬ-ЯКА характеристика: діагональ, об'єм, "
                "колір. «Кнопки» — для діагоналі/об'єму, «Кружечки» — для кольору "
                "(колір кружечка береться з поля «Колір (HEX)» варіанта характеристики). "
                "Товар може бути в ДВОХ групах одночасно (і діагональ, і колір).",
            },
        ),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[VariantGroup]:
        return super().get_queryset(request).annotate(n_items=Count("items"))

    @display(
        description="Віджет",
        label={"Кнопки (діагональ, об'єм)": "info", "Кружечки (колір)": "success"},
    )
    def widget_badge(self, obj: VariantGroup) -> str:
        return obj.get_widget_display()

    @display(description="Товарів", ordering="n_items")
    def items_badge(self, obj: VariantGroup) -> int:
        return obj.n_items


@admin.register(RelatedGroup)
class RelatedGroupAdmin(ModelAdmin, TabbedTranslationAdmin):
    list_display = ("name", "kind_badge", "items_badge", "sort_order", "is_active")
    list_display_links = ("name",)
    list_editable = ("sort_order", "is_active")
    list_filter = (("kind", ChoicesDropdownFilter), ("is_active", BooleanRadioFilter))
    search_fields = ("name_uk", "name_ru")
    inlines = (RelatedGroupItemGroupInline,)
    fieldsets = (
        (
            "Група супутніх товарів",
            {
                "fields": ("name", "kind", "sort_order", "is_active"),
                "description": "ВЗАЄМНІСТЬ — властивість структури, а не робота коду. Складіть "
                "ОДНУ групу з 3 товарів — усі троє автоматично бачитимуть двох інших. "
                "Асиметрія («А бачить Б, Б не бачить А») тут неможлива в принципі.",
            },
        ),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[RelatedGroup]:
        return super().get_queryset(request).annotate(n_items=Count("items"))

    @display(
        description="Тип",
        label={"Комплект (взаємно)": "success", "Аксесуари": "info", "Разом купують": "warning"},
    )
    def kind_badge(self, obj: RelatedGroup) -> str:
        return obj.get_kind_display()

    @display(description="Товарів", ordering="n_items")
    def items_badge(self, obj: RelatedGroup) -> int:
        return obj.n_items


# ---------------------------------------------------------------------------
# PriceHistory — append-only журнал
# ---------------------------------------------------------------------------
@admin.register(PriceHistory)
class PriceHistoryAdmin(ModelAdmin):
    """READONLY НАЗАВЖДИ. Журнал наповнює PG-тригер `price_history_trg`, а не Python.

    Правити його руками = зламати аудит цін: тригер спрацьовує і на масовому `qs.update()`
    синку, і на `bulk_create`, і на ручній правці в адмінці — саме тому історія повна.
    """

    list_display = (
        "changed_at",
        "product_link",
        "price_cell",
        "availability",
        "reason_badge",
        "run",
    )
    list_select_related = ("product",)
    list_filter = (
        ("product", AutocompleteSelectFilter),
        ("reason", ChoicesDropdownFilter),
        ("changed_at", RangeDateTimeFilter),
        ("source_currency", ChoicesDropdownFilter),
    )
    list_filter_submit = True
    search_fields = ("product__sku", "product__name_uk")
    date_hierarchy = "changed_at"
    ordering = ("-changed_at",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    @display(description="Товар", ordering="product__name")
    def product_link(self, obj: PriceHistory) -> SafeString:
        url = reverse("admin:catalog_product_change", args=[obj.product_id])
        return format_html('<a href="{}">{} — {}</a>', url, obj.product.sku, obj.product.name)

    @display(description="Ціна", ordering="price")
    def price_cell(self, obj: PriceHistory) -> SafeString:
        source = f"{_fmt_number(obj.base_price)} {obj.source_currency}"
        if obj.usd_rate_used:
            source += f" × {_fmt_number(obj.usd_rate_used)}"
        if obj.markup_percent:
            source += f" +{_fmt_number(obj.markup_percent)}%"
        return format_html(
            '<b>{} ₴</b><br><span style="font-size:11px;opacity:.6">{}</span>',
            _fmt_money(obj.price),
            source,
        )

    @display(
        description="Причина",
        label={
            "Синхронізація прайсу": "info",
            "Перерахунок (курс/округлення/націнка)": "warning",
            "Ручна правка": "success",
            "Відкат прогону": "danger",
            "Імпорт": "info",
        },
        ordering="reason",
    )
    def reason_badge(self, obj: PriceHistory) -> str:
        return obj.get_reason_display()
