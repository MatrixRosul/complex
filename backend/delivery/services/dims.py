"""
Габарити й вага, які реально йдуть у Нову Пошту (ADR-021, INTEGRATIONS §1.7).

🔴 НАЙДОРОЖЧА ПАСТКА ПРОЄКТУ — ОБ'ЄМНА ВАГА.
   Коефіцієнт НП = 250 кг/м³, ціна рахується від `max(фактична вага, об'ємна вага)`.
   Живі заміри (Ужгород→Київ, Cost=1500):

       Weight=2 БЕЗ габаритів .................  97.5 грн
       Weight=2 + OptionsSeat 50×50×60 см ..... 539.5 грн   (0.15 м³ × 250 = 37.5 кг)

   Різниця 5.5×. Для магазину побутової техніки калькулятор без габаритів СИСТЕМАТИЧНО
   бреше покупцеві вниз, і магазин попадає на гроші при КОЖНОМУ замовленні.
   → `OptionsSeat` шлеться ЗАВЖДИ, навіть коли габарити дефолтні (source="default").

🔴 ГАБАРИТИ УПАКОВКИ ≠ ГАБАРИТИ ВИРОБУ. Холодильник: виріб 1635×490×278 мм / 58 кг,
   упаковка 171,5×56×35 см / 61,5 кг. Об'ємна вага рахується ПО УПАКОВЦІ.
   Тому джерело — тільки `Product.package_*` (проєкція характеристик «Габарити упаковки
   (ВхШхГ)» / «Вага в упаковці», яку пише sync_specs через Attribute.role), і НІКОЛИ —
   поля виробу з EAV.

ЛАНЦЮГ ФОЛБЕКІВ (той самий у /delivery/quote, в OrderItem і в генерації ТТН):
   1. Product.package_{height,width,depth}_cm + package_weight_kg   → spec | product
   2. Category.default_{height,width,depth}_cm + default_weight_kg  → category
   3. SiteSettings.default_*                                        → default

Габарити і вага резолвляться НЕЗАЛЕЖНО (у товару може бути вага з характеристик, а
габарити — з дефолтів категорії). Підсумковий `source` — НАЙСЛАБША з двох ланок:
метрика «покриття» має показувати найгіршу правду, а не найкращу.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from catalog.enums import ProductSource
from core.models import SiteSettings
from delivery.models import NP_VOLUMETRIC_FACTOR, DimsSource

log = logging.getLogger(__name__)

__all__ = [
    "CartDims",
    "EffectiveDims",
    "cart_dims",
    "effective_dims",
]

#: Слабкість джерела: чим більше, тим гірше. Для вибору найслабшої ланки.
_WEAKNESS = {
    DimsSource.SPEC: 0,
    DimsSource.PRODUCT: 1,
    DimsSource.CATEGORY: 2,
    DimsSource.DEFAULT: 3,
}

_CM3_IN_M3 = Decimal("1000000")
_M3 = Decimal("0.00001")  # точність об'єму (DeliveryQuote.volume_m3: decimal_places=5)
_KG = Decimal("0.001")  # точність ваги  (decimal_places=3)


@dataclass(frozen=True, slots=True)
class EffectiveDims:
    """Те, що РЕАЛЬНО піде в НП для ОДНОГО місця (одиниці товару)."""

    height_cm: Decimal
    width_cm: Decimal
    depth_cm: Decimal
    weight_kg: Decimal
    source: str  # DimsSource

    @property
    def volume_m3(self) -> Decimal:
        vol = self.height_cm * self.width_cm * self.depth_cm / _CM3_IN_M3
        return vol.quantize(_M3, rounding=ROUND_HALF_UP)

    @property
    def volumetric_weight_kg(self) -> Decimal:
        """Об'ємна вага = об'єм × 250 кг/м³."""
        return (self.volume_m3 * NP_VOLUMETRIC_FACTOR).quantize(_KG, rounding=ROUND_HALF_UP)

    @property
    def chargeable_weight_kg(self) -> Decimal:
        """🔴 Саме від цього числа НП рахує ціну: max(фактична, об'ємна)."""
        return max(self.weight_kg, self.volumetric_weight_kg)

    def as_options_seat(self) -> dict[str, str]:
        """Одне місце для `OptionsSeat`. Одиниця — СМ (як у характеристиках; конвертація
        не потрібна, і саме тому габарити виробу в мм для НП не годяться)."""
        return {
            "weight": _fmt(self.weight_kg),
            "volumetricWidth": _fmt(self.width_cm),
            "volumetricHeight": _fmt(self.height_cm),
            "volumetricLength": _fmt(self.depth_cm),
        }


@dataclass(frozen=True, slots=True)
class CartDims:
    """Сумарні габарити кошика: N місць, кожне зі своїми розмірами.

    ⚠️ НЕ «складаємо коробки в одну». НП рахує пооб'ємно ПО КОЖНОМУ МІСЦЮ окремо
    (`SeatsAmount` + масив `OptionsSeat`), і склеювання двох холодильників в одну
    віртуальну коробку 2×h дало б інший (неправильний) об'єм.
    """

    seats: list[EffectiveDims]
    source: str  # найслабше джерело серед позицій

    @property
    def seats_amount(self) -> int:
        return len(self.seats)

    @property
    def weight_kg(self) -> Decimal:
        return sum((s.weight_kg for s in self.seats), Decimal("0")).quantize(_KG)

    @property
    def volume_m3(self) -> Decimal:
        return sum((s.volume_m3 for s in self.seats), Decimal("0")).quantize(_M3)

    @property
    def chargeable_weight_kg(self) -> Decimal:
        return sum((s.chargeable_weight_kg for s in self.seats), Decimal("0")).quantize(_KG)

    def as_options_seat(self) -> list[dict[str, str]]:
        return [s.as_options_seat() for s in self.seats]


def _fmt(value: Decimal) -> str:
    d = value.normalize()
    if d == d.to_integral_value():
        d = d.quantize(Decimal("1"))
    return format(d, "f")


def _positive(*values: Decimal | None) -> bool:
    return all(v is not None and v > 0 for v in values)


def _product_dims_source(product) -> str:
    """`Product.package_dims_source` — це ProductSource (sheet/manual/ai), не DimsSource.

    З таблиці характеристик (sheet) → SPEC. Руками/ШІ → PRODUCT (ручний override).
    """
    return (
        DimsSource.SPEC
        if product.package_dims_source == ProductSource.SHEET
        else DimsSource.PRODUCT
    )


def effective_dims(product) -> EffectiveDims:
    """Габарити й вага ОДНІЄЇ одиниці товару — з повним ланцюгом фолбеків.

    НІКОЛИ не падає і НІКОЛИ не повертає нулі: у найгіршому разі — глобальні дефолти
    з SiteSettings. Порожній вимір = недоплата за доставку, тому «немає габаритів»
    не є допустимим результатом.
    """
    site = SiteSettings.get_solo()
    category = product.category

    # --- габарити (усі три виміри мають бути на одному рівні) ---
    if _positive(product.package_height_cm, product.package_width_cm, product.package_depth_cm):
        h, w, d = (
            product.package_height_cm,
            product.package_width_cm,
            product.package_depth_cm,
        )
        dims_src = _product_dims_source(product)
    elif category is not None and _positive(
        category.default_height_cm, category.default_width_cm, category.default_depth_cm
    ):
        h, w, d = (
            category.default_height_cm,
            category.default_width_cm,
            category.default_depth_cm,
        )
        dims_src = DimsSource.CATEGORY
    else:
        h, w, d = site.default_height_cm, site.default_width_cm, site.default_depth_cm
        dims_src = DimsSource.DEFAULT

    # --- вага (окремий ланцюг!) ---
    if _positive(product.package_weight_kg):
        weight = product.package_weight_kg
        weight_src = _product_dims_source(product)
    elif category is not None and _positive(category.default_weight_kg):
        weight = category.default_weight_kg
        weight_src = DimsSource.CATEGORY
    else:
        weight = site.default_weight_kg
        weight_src = DimsSource.DEFAULT

    # Підсумкове джерело — найслабша ланка.
    source = max((dims_src, weight_src), key=lambda s: _WEAKNESS[DimsSource(s)])

    if source != DimsSource.SPEC:
        # Метрика покриття (INTEGRATIONS §1.7): категорія, де >20% товарів не на "spec", —
        # це категорія, де магазин системно недоплачує за доставку.
        log.debug(
            "effective_dims(product=%s): джерело '%s' — габарити НЕ з характеристик",
            product.pk,
            source,
        )

    return EffectiveDims(
        height_cm=Decimal(h),
        width_cm=Decimal(w),
        depth_cm=Decimal(d),
        weight_kg=Decimal(weight),
        source=str(source),
    )


def cart_dims(items: list[tuple[object, int]]) -> CartDims:
    """Сумарні габарити кошика.

    `items` — [(product, qty), …]. Кожна ОДИНИЦЯ товару = окреме місце (`OptionsSeat`):
    два холодильники їдуть двома коробками, а не однією подвоєної висоти.
    """
    seats: list[EffectiveDims] = []
    worst = DimsSource.SPEC
    for product, qty in items:
        dims = effective_dims(product)
        seats.extend([dims] * max(int(qty), 1))
        if _WEAKNESS[DimsSource(dims.source)] > _WEAKNESS[DimsSource(worst)]:
            worst = DimsSource(dims.source)
    return CartDims(seats=seats, source=str(worst))
