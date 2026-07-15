"""Масове вмикання/вимикання Hotline на категорію та всі її підкатегорії.

Пряма вимога ТЗ. Працює на матеріалізованому шляху (ADR-001): усі нащадки — один
індексований запит по `cat_path_btree`, без рекурсії й без MPTT.

⚠️ ПАСТКА `path__startswith`: шлях «123» є ПРЕФІКСОМ шляху «1234» (це РІЗНІ корені, не
   батько й дитина). Тому умова — `path == p OR path LIKE p || '/%'`, а не голий startswith.
   Інакше вмикання Hotline на одній категорії мовчки зачепило б чужу гілку.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from catalog.models import Category, Product

log = logging.getLogger(__name__)

__all__ = ["affected_categories", "affected_products", "set_hotline_for_category"]


def affected_categories(category: Category, *, include_descendants: bool = True) -> QuerySet[Category]:
    if not include_descendants:
        return Category.objects.filter(pk=category.pk)
    return category.descendants(include_self=True)


def affected_products(category: Category, *, include_descendants: bool = True) -> QuerySet[Product]:
    """Товари категорії (+ підкатегорій). Саме цей queryset показуємо в підтвердженні."""
    if not include_descendants:
        return Product.objects.filter(category_id=category.pk)
    cond = Q(category__path=category.path) | Q(category__path__startswith=f"{category.path}/")
    return Product.objects.filter(cond)


@transaction.atomic
def set_hotline_for_category(
    category: Category,
    *,
    enabled: bool,
    include_descendants: bool = True,
) -> tuple[int, int]:
    """Вмикає/вимикає Hotline на всіх товарах гілки. Повертає (товарів, категорій).

    Заразом переставляє `Category.hotline_enabled_default` — інакше товари, які прилетять
    наступним синком, знову прийшли б зі старим значенням, і категорія «протікала» б у фід
    (або навпаки — назавжди лишилась би поза ним).

    ⚠️ `qs.update()` обходить `save()`, тому `updated_at` (auto_now) проставляємо руками:
       по ньому ходять sitemap і ISR-ревалідація фронту.
       PG-тригер історії цін тут не спрацьовує — він висить на UPDATE OF price, availability.
    """
    now = timezone.now()

    products = affected_products(category, include_descendants=include_descendants)
    n_products = products.update(hotline_enabled=enabled, updated_at=now)

    categories = affected_categories(category, include_descendants=include_descendants)
    n_categories = categories.update(hotline_enabled_default=enabled, updated_at=now)

    log.info(
        "hotline_cascade category=%s enabled=%s products=%s categories=%s",
        category.pk,
        enabled,
        n_products,
        n_categories,
    )
    return n_products, n_categories
