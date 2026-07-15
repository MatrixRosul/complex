"""Фасети: метадані, парсинг query-string, збірка груп з лічильниками (ADR-007, ADR-008).

АРХІТЕКТУРНЕ ЯДРО МОДУЛЯ — В ДВОХ РЕЧЕННЯХ:
  `Product.filter_tokens` (ArrayField + GIN) — ЄДИНЕ джерело і фільтрації, і лічильників.
  EAV (`Attribute` / `AttributeOption` / `Unit`) — ВИКЛЮЧНО джерело ПІДПИСІВ (назва, одиниця,
  порядок, віджет); у підрахунку він не бере участі взагалі.

Саме ця межа — фікс critical-баги, знайденої критиками архітектури v1: там фільтрація йшла по
денормі, а лічильники рахувались по EAV. Два джерела розходяться щоразу, коли `rebuild_denorm`
відстає хоч на секунду, і користувач бачить «Bosch (12)», клікає — і отримує 9 товарів.
Тут це неможливо ЗА ПОБУДОВОЮ: і `&&`-фільтр, і `unnest + GROUP BY` читають ту саму колонку.

ТОКЕН — `"{code}:{value}"`, мовонезалежний (slug, не текст):
    brand:gorenje · country:kytai · avail:in_stock · cond:2 · installment:1
    kamer:2 (число-опція) · obiem:300-399 (БАКЕТ, ADR-007) · no-frost:1 (bool)
Через мовонезалежність перемикання uk↔ru НЕ змінює вибірку і не подвоює фасети (ADR-003).

⚠️ Мультивибір — `&&` (overlap = OR у межах групи), а НЕ `@>`.
   `filter_tokens @> ['brand:bosch','brand:gorenje']` означає «товар І Bosch, І Gorenje ОДНОЧАСНО»
   → завжди нуль результатів. Між різними групами — AND.
"""

from __future__ import annotations

from typing import Any, Final

from django.core.cache import cache

from catalog.models import Attribute, Brand, Country
from catalog.services.cache import FACET_META_TTL, facet_meta_key
from catalog.services.lang import tr

__all__ = [
    "RESERVED_PARAMS",
    "build_facet_groups",
    "get_facet_meta",
    "parse_facet_params",
    "tokens_for",
]

# Query-параметри, які НЕ є фасетами. Усе інше в query-string, що збігається з кодом відомого
# фасета, трактується як вибір фасета (?brand=bosch&obiem=300-399).
RESERVED_PARAMS: Final[frozenset[str]] = frozenset(
    {
        "lang",
        "page",
        "page_size",
        "sort",
        "q",
        "price_min",
        "price_max",
        "ids",
        "category",
        "format",
    }
)

# --- Підписи системних фасетів -------------------------------------------------------------
# Джерело для них — не EAV, а самі поля Product, тому підпис живе в коді. Модельні `choices`
# українські, тож RU довелося б брати з gettext; тут словник явний — його видно і легко правити.
_AVAIL_LABELS: Final[dict[str, dict[str, str]]] = {
    "in_stock": {"uk": "В наявності", "ru": "В наличии"},
    "on_order": {"uk": "Під замовлення", "ru": "Под заказ"},
    "out_of_stock": {"uk": "Немає в наявності", "ru": "Нет в наличии"},
}
_COND_LABELS: Final[dict[str, dict[str, str]]] = {
    "0": {"uk": "Новий", "ru": "Новый"},
    "1": {"uk": "Відновлений", "ru": "Восстановленный"},
    "2": {"uk": "Уцінений", "ru": "Уценённый"},
    "3": {"uk": "Б/в", "ru": "Б/у"},
}
_GROUP_LABELS: Final[dict[str, dict[str, str]]] = {
    "brand": {"uk": "Виробник", "ru": "Производитель"},
    "country": {"uk": "Країна виробництва", "ru": "Страна производства"},
    "avail": {"uk": "Наявність", "ru": "Наличие"},
    "cond": {"uk": "Стан", "ru": "Состояние"},
    "installment": {"uk": "Оплата частинами", "ru": "Оплата частями"},
}
_YES: Final[dict[str, str]] = {"uk": "Так", "ru": "Да"}

# Порядок системних груп у сайдбарі: бренд завжди перший (найчастіший фільтр).
_SYSTEM_ORDER: Final[dict[str, int]] = {
    "brand": 0,
    "avail": 1,
    "installment": 2,
    "cond": 3,
    "country": 4,
}


def _bucket_token(lo: Any, hi: Any) -> str:
    """[[300, 399]] → "300-399";  [[400, null]] → "400-plus" (ADR-007)."""
    return f"{lo}-plus" if hi is None else f"{lo}-{hi}"


def _bucket_label(lo: Any, hi: Any) -> str:
    return f"{lo}+" if hi is None else f"{lo}–{hi}"  # en-dash


def _with_unit(label: str, unit: str) -> str:
    """Одиниця клеїться до ЗНАЧЕННЯ, а не до назви (INPUTS §2 — жорстка вимога замовника).

    «300–399» + «л» → «300–399 л».  Ніколи не «Об'єм (л): 300–399».
    """
    return f"{label} {unit}".strip() if unit else label


def _build_meta(lang: str) -> dict[str, dict[str, Any]]:
    """Метадані ВСІХ фасетів: підписи, одиниці, віджети, порядок значень.

    Це ЄДИНЕ місце, де ми ходимо в EAV на запиті каталогу. Кешується на годину — атрибути
    змінюються раз на тиждень, а лічильники (які змінюються щосинку) сюди не входять.
    """
    meta: dict[str, dict[str, Any]] = {}

    # --- системні фасети ---
    meta["brand"] = {
        "label": _GROUP_LABELS["brand"][lang],
        "unit": "",
        "widget": "checkbox",
        "group": "",
        "group_sort": 0,
        "sort_order": _SYSTEM_ORDER["brand"],
        "labels": {b.slug: b.name for b in Brand.objects.filter(is_active=True)},
        "order": list(
            Brand.objects.filter(is_active=True).order_by("name").values_list("slug", flat=True)
        ),
    }
    meta["country"] = {
        "label": _GROUP_LABELS["country"][lang],
        "unit": "",
        "widget": "checkbox",
        "group": "",
        "group_sort": 0,
        "sort_order": _SYSTEM_ORDER["country"],
        "labels": {c.slug: tr(c, "name", lang) for c in Country.objects.all()},
        "order": [],
    }
    meta["avail"] = {
        "label": _GROUP_LABELS["avail"][lang],
        "unit": "",
        "widget": "checkbox",
        "group": "",
        "group_sort": 0,
        "sort_order": _SYSTEM_ORDER["avail"],
        "labels": {k: v[lang] for k, v in _AVAIL_LABELS.items()},
        "order": ["in_stock", "on_order", "out_of_stock"],
    }
    meta["cond"] = {
        "label": _GROUP_LABELS["cond"][lang],
        "unit": "",
        "widget": "checkbox",
        "group": "",
        "group_sort": 0,
        "sort_order": _SYSTEM_ORDER["cond"],
        "labels": {k: v[lang] for k, v in _COND_LABELS.items()},
        "order": ["0", "1", "2", "3"],
    }
    meta["installment"] = {
        "label": _GROUP_LABELS["installment"][lang],
        "unit": "",
        "widget": "switch",
        "group": "",
        "group_sort": 0,
        "sort_order": _SYSTEM_ORDER["installment"],
        "labels": {"1": _YES[lang]},
        "order": ["1"],
    }

    # --- фасети з характеристик ---
    # ⚠️ ТІЛЬКИ is_filterable=True. Атрибути, створені автоматично (синком/ШІ), мають
    #    is_filterable=False + needs_review=True — вони видно в картці, але у фільтри не
    #    потрапляють, поки контент-менеджер їх не схвалить. Це прямий фікс брудних фасетів
    #    старого сайту (Sharp vs SHARP, сирі кВт·год у фільтрах).
    attributes = (
        Attribute.objects.filter(is_filterable=True)
        .select_related("unit", "group")
        .prefetch_related("options")
        .order_by("group__sort_order", "sort_order", "id")
    )

    for attr in attributes:
        unit = tr(attr.unit, "name", lang) if attr.unit_id else ""

        if attr.value_type == Attribute.ValueType.BOOL:
            widget = "switch"
        elif attr.filter_widget == Attribute.FilterWidget.RANGE_SLIDER:
            widget = "range_slider"
        else:
            # checkbox / select / range_buckets — усі рендеряться списком з лічильниками.
            widget = "checkbox"

        labels: dict[str, str] = {}
        order: list[str] = []

        if attr.value_type == Attribute.ValueType.BOOL:
            labels["1"] = _YES[lang]
            order = ["1"]
        elif attr.filter_widget == Attribute.FilterWidget.RANGE_BUCKETS and attr.range_buckets:
            for bucket in attr.range_buckets:
                lo, hi = bucket[0], bucket[1]
                token_value = _bucket_token(lo, hi)
                labels[token_value] = _with_unit(_bucket_label(lo, hi), unit)
                order.append(token_value)
        else:
            for option in attr.options.all():
                labels[option.slug] = _with_unit(tr(option, "value", lang), unit)
                order.append(option.slug)

        meta[attr.code] = {
            "label": tr(attr, "name", lang),
            "unit": unit,
            "widget": widget,
            "group": tr(attr.group, "name", lang),
            "group_sort": attr.group.sort_order,
            # +10: характеристики завжди нижче системних фасетів (бренд/наявність).
            "sort_order": 10 + attr.sort_order,
            "labels": labels,
            "order": order,
            "numeric": attr.value_type == Attribute.ValueType.NUMBER,
        }

    return meta


def get_facet_meta(lang: str) -> dict[str, dict[str, Any]]:
    key = facet_meta_key(lang)
    cached = cache.get(key)
    if cached is not None:
        return cached
    meta = _build_meta(lang)
    cache.set(key, meta, FACET_META_TTL)
    return meta


def parse_facet_params(params: dict[str, list[str]], meta: dict[str, Any]) -> dict[str, list[str]]:
    """Query-string → {code: [values]}.

    Підтримує обидві форми мультивибору:
        ?brand=bosch&brand=gorenje     (повторення — так шле фронт, http.ts::buildCatalogSearch)
        ?brand=bosch,gorenje           (кома — зручно руками й у curl)

    Невідомі коди мовчки ігноруються: інакше будь-який трекінговий `?utm_source=` або
    `?fbclid=` у посиланні перетворював би каталог на порожню видачу.
    """
    selected: dict[str, list[str]] = {}

    for code, raw_values in params.items():
        if code in RESERVED_PARAMS or code not in meta:
            continue

        values: list[str] = []
        for raw in raw_values:
            for part in raw.split(","):
                part = part.strip()
                if part and part not in values:
                    values.append(part)

        if values:
            selected[code] = values

    return selected


def tokens_for(code: str, values: list[str]) -> list[str]:
    """{brand: [bosch, gorenje]} → ['brand:bosch', 'brand:gorenje'] — вхід для `&&`."""
    return [f"{code}:{value}" for value in values]


def build_facet_groups(
    counts: dict[str, dict[str, int]],
    selected: dict[str, list[str]],
    meta: dict[str, dict[str, Any]],
    lang: str,
) -> list[dict[str, Any]]:
    """Збирає фасети для відповіді.

    `counts` — {code: {value: count}}, ВЖЕ порахований з drop-self (див. listing.py).

    Значення беруться з РЕАЛЬНО ЗНАЙДЕНИХ токенів, а не зі списку опцій в EAV. Тому у фасеті
    ніколи не з'явиться варіант, під яким нуль товарів (класичне «Bosch (0)», по якому клікають
    і отримують порожньо). Виняток — ОБРАНІ значення: вони показуються завжди, навіть з нулем,
    інакше галочка, яку користувач щойно поставив, зникала б у нього з-під курсора.
    """
    groups: list[dict[str, Any]] = []

    for code, values_count in counts.items():
        info = meta.get(code)
        if info is None:
            # Токен є в даних, але його атрибут не is_filterable (або його вже видалили) —
            # у фасети він не йде. Саме так «сирі» автоматичні атрибути не течуть у фільтри.
            continue

        chosen = selected.get(code, [])
        present = set(values_count) | set(chosen)
        if not present:
            continue

        labels: dict[str, str] = info["labels"]
        explicit_order: list[str] = info["order"]

        def sort_key(value: str, _order: list[str] = explicit_order, _info: dict = info) -> Any:
            if value in _order:
                return (0, _order.index(value), "")
            # Числові значення без явного порядку («kamer:2») — сортуємо як числа, інакше
            # отримаємо «10, 2, 3» замість «2, 3, 10».
            if _info.get("numeric"):
                try:
                    return (1, float(value), "")
                except ValueError:
                    pass
            return (2, 0, value)

        facet_values = [
            {
                "value": value,
                "label": labels.get(value, value),
                "count": values_count.get(value, 0),
            }
            for value in sorted(present, key=sort_key)
        ]

        groups.append(
            {
                "code": code,
                "label": info["label"],
                "widget": info["widget"],
                "unit": info["unit"],
                "group": info["group"],
                "values": facet_values,
                "_sort": (info["group_sort"], info["sort_order"], info["label"]),
            }
        )

    groups.sort(key=lambda g: g.pop("_sort"))
    return groups
