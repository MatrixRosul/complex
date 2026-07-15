"""
Реєстр перекладних цілей: (модель, поле) → kind.

Один список — одне джерело правди для collect_*, для management-команди й для оцінки
вартості. Додав перекладне поле в <app>/translation.py — додай рядок сюди, інакше воно
просто ніколи не потрапить у чергу.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.apps import apps

from translation.models import TranslationKind


@dataclass(frozen=True)
class Target:
    app_label: str
    model_name: str
    field: str
    kind: str

    @property
    def model(self):
        return apps.get_model(self.app_label, self.model_name)

    def __str__(self) -> str:
        return f"{self.app_label}.{self.model_name}.{self.field}"


# СЛОВНИК — перекладається ОДИН раз і застосовується до всіх товарів (TRANSLATION.md §2).
# Саме ці рядки дають ~140× економію: «Колір виробу: Чорний» лежить в ОДНОМУ рядку БД,
# а не всередині кожного з 10 000 товарів.
DICTIONARY_TARGETS: list[Target] = [
    Target("catalog", "Attribute", "name", TranslationKind.ATTRIBUTE_NAME),
    Target("catalog", "AttributeGroup", "name", TranslationKind.ATTRIBUTE_NAME),
    Target("catalog", "Unit", "name", TranslationKind.UNIT),
    Target("catalog", "AttributeOption", "value", TranslationKind.ATTRIBUTE_VALUE),
    Target("catalog", "Category", "name", TranslationKind.CATEGORY_NAME),
]

# ОБСЯГ — по одиниці на кожен товар/сторінку.
CONTENT_TARGETS: list[Target] = [
    Target("catalog", "Product", "name", TranslationKind.PRODUCT_NAME),
    Target("catalog", "Product", "short_description", TranslationKind.PRODUCT_SHORT_DESCRIPTION),
    Target("catalog", "Product", "description", TranslationKind.PRODUCT_DESCRIPTION),
    Target("catalog", "Product", "seo_title", TranslationKind.SEO_TITLE),
    Target("catalog", "Product", "seo_description", TranslationKind.SEO_DESCRIPTION),
    Target("catalog", "Category", "seo_title", TranslationKind.SEO_TITLE),
    Target("catalog", "Category", "seo_description", TranslationKind.SEO_DESCRIPTION),
    Target("cms", "StaticPage", "title", TranslationKind.OTHER),
    Target("cms", "StaticPage", "body", TranslationKind.PAGE_HTML),
    Target("cms", "NewsPost", "title", TranslationKind.OTHER),
    Target("cms", "NewsPost", "excerpt", TranslationKind.OTHER),
    Target("cms", "NewsPost", "body", TranslationKind.NEWS_HTML),
]

ALL_TARGETS: list[Target] = [*DICTIONARY_TARGETS, *CONTENT_TARGETS]


def targets_for_kinds(kinds: set[str] | None = None) -> list[Target]:
    if not kinds:
        return ALL_TARGETS
    return [t for t in ALL_TARGETS if t.kind in kinds]
