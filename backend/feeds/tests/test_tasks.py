"""Задачі: масове вмикання по категорії, heal, рубрикатор, management-команда."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from catalog.models import Category, Product
from core.models import SiteSettings
from feeds import rubricator, services, tasks
from feeds.models import FeedArtifact, HotlineCategory
from feeds.tests.conftest import make_product

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# 🔴 Масове вмикання Hotline на категорію ТА ВСІ ПІДКАТЕГОРІЇ (вимога ТЗ)
# ---------------------------------------------------------------------------
@pytest.fixture
def tree(db, brand):
    """Дерево: Техніка → Велика → Холодильники (+ сусідня гілка, яку чіпати НЕ можна)."""
    root = Category.objects.create(external_id="100", name="Техніка", slug="tehnika")
    big = Category.objects.create(external_id="101", name="Велика", slug="velyka", parent=root)
    fridges = Category.objects.create(
        external_id="102", name="Холодильники", slug="holod", parent=big
    )
    other = Category.objects.create(external_id="200", name="Інше", slug="inshe")

    products = {
        "root": make_product(category=root, brand=brand, sku="R", hotline_enabled=False),
        "big": make_product(category=big, brand=brand, sku="B", hotline_enabled=False),
        "fridge": make_product(category=fridges, brand=brand, sku="F", hotline_enabled=False),
        "other": make_product(category=other, brand=brand, sku="O", hotline_enabled=False),
    }
    return {"root": root, "big": big, "fridges": fridges, "other": other, "products": products}


def test_bulk_enable_covers_all_descendants(tree):
    result = tasks.set_hotline_for_category(tree["root"].pk, True)

    assert result["products"] == 3  # root + big + fridge, але НЕ «Інше»
    for key in ("root", "big", "fridge"):
        tree["products"][key].refresh_from_db()
        assert tree["products"][key].hotline_enabled is True

    tree["products"]["other"].refresh_from_db()
    assert tree["products"]["other"].hotline_enabled is False  # сусідня гілка не зачеплена


def test_bulk_enable_sets_category_default_so_new_products_inherit(tree):
    """🔴 Без hotline_enabled_default категорія «протікає»: синк 4×/добу створює НОВІ товари
    з hotline_enabled=False, і вони назавжди лишаються поза фідом."""
    tasks.set_hotline_for_category(tree["root"].pk, True)

    for cat in (tree["root"], tree["big"], tree["fridges"]):
        cat.refresh_from_db()
        assert cat.hotline_enabled_default is True

    tree["other"].refresh_from_db()
    assert tree["other"].hotline_enabled_default is False


def test_bulk_disable_without_descendants(tree):
    tasks.set_hotline_for_category(tree["root"].pk, True)
    tasks.set_hotline_for_category(tree["root"].pk, False, include_descendants=False)

    tree["products"]["root"].refresh_from_db()
    tree["products"]["fridge"].refresh_from_db()

    assert tree["products"]["root"].hotline_enabled is False
    assert tree["products"]["fridge"].hotline_enabled is True  # нащадків не чіпали


def test_path_prefix_does_not_leak_into_sibling_root(db, brand):
    """Пастка path__startswith: «10» — префікс «100», але це РІЗНІ корені, не батько й дитина."""
    c10 = Category.objects.create(external_id="10", name="A", slug="a")
    c100 = Category.objects.create(external_id="100", name="B", slug="b")
    p10 = make_product(category=c10, brand=brand, sku="P10", hotline_enabled=False)
    p100 = make_product(category=c100, brand=brand, sku="P100", hotline_enabled=False)

    tasks.set_hotline_for_category(c10.pk, True)

    p10.refresh_from_db()
    p100.refresh_from_db()
    assert p10.hotline_enabled is True
    assert p100.hotline_enabled is False


# ---------------------------------------------------------------------------
# Celery-задачі генерації
# ---------------------------------------------------------------------------
def test_generate_task_respects_kill_switch(site, product, feed_dir):
    SiteSettings.objects.filter(pk=site.pk).update(hotline_enabled=False)
    SiteSettings.invalidate()

    assert tasks.generate_hotline_feed()["status"] == "disabled"
    assert not FeedArtifact.objects.exists()


def test_generate_task_returns_counters(site, product, feed_dir):
    result = tasks.generate_hotline_feed()

    assert result["status"] == "ok"
    assert result["items"] == 1
    assert services.feed_path().exists()


def test_heal_regenerates_when_file_missing(site, product, feed_dir):
    tasks.generate_hotline_feed()
    services.feed_path().unlink()

    result = tasks.heal_hotline_feed()

    assert result["status"] == "healed"
    assert result["reason"] == "file_missing"
    assert services.feed_path().exists()


def test_heal_is_noop_when_fresh(site, product, feed_dir):
    tasks.generate_hotline_feed()

    assert tasks.heal_hotline_feed()["status"] == "fresh"
    assert FeedArtifact.objects.count() == 1


# ---------------------------------------------------------------------------
# Рубрикатор Hotline
# ---------------------------------------------------------------------------
TREE_SAMPLE = """Побутова техніка
;Велика побутова техніка
;;Холодильники
;;Пральні машини
;Телевізори
;;Телевізори
Авто і Мото
;Шини і диски
"""


def test_parse_tree_uses_leading_semicolons_as_depth():
    nodes = rubricator.parse_tree(TREE_SAMPLE)

    assert [(n.depth, n.name) for n in nodes][:3] == [
        (0, "Побутова техніка"),
        (1, "Велика побутова техніка"),
        (2, "Холодильники"),
    ]
    leaves = {n.name for n in nodes if n.is_leaf}
    assert "Холодильники" in leaves
    assert "Побутова техніка" not in leaves  # має дітей → не листова


def test_import_tree_builds_parent_chain(db):
    rubricator.import_tree(rubricator.parse_tree(TREE_SAMPLE))

    fridges = HotlineCategory.objects.get(name="Холодильники")
    assert fridges.path == "Побутова техніка/Велика побутова техніка/Холодильники"
    assert fridges.depth == 2
    assert fridges.is_leaf
    assert fridges.parent.name == "Велика побутова техніка"
    assert fridges.parent.parent.name == "Побутова техніка"

    # Стек батьків скидається на новому корені — інакше «Шини і диски» стали б дитиною ТВ.
    tyres = HotlineCategory.objects.get(name="Шини і диски")
    assert tyres.parent.name == "Авто і Мото"


def test_import_tree_deactivates_vanished_nodes_instead_of_deleting(db):
    stale = HotlineCategory.objects.create(path="Стара категорія", name="Стара категорія")

    rubricator.import_tree(rubricator.parse_tree(TREE_SAMPLE))

    stale.refresh_from_db()  # DELETE вибив би товари наших категорій з фіда через FK
    assert stale.is_active is False


def test_import_tree_is_idempotent(db):
    nodes = rubricator.parse_tree(TREE_SAMPLE)
    first = rubricator.import_tree(nodes)
    second = rubricator.import_tree(nodes)

    assert first["created"] == 8
    assert second["created"] == 0
    assert second["updated"] == 8
    assert HotlineCategory.objects.count() == 8


# ---------------------------------------------------------------------------
# Management-команда
# ---------------------------------------------------------------------------
def test_generate_feed_command(site, product, feed_dir):
    out = StringIO()
    call_command("generate_feed", stdout=out)

    assert "Товарів у фіді:  1" in out.getvalue()
    assert services.feed_path().exists()
    assert FeedArtifact.objects.filter(is_current=True).count() == 1


def test_generate_feed_command_dry_run(site, product, feed_dir):
    out = StringIO()
    call_command("generate_feed", "--dry-run", stdout=out)

    assert "DRY-RUN" in out.getvalue()
    assert not services.feed_path().exists()
    assert not FeedArtifact.objects.exists()


def test_feed_reflects_bulk_enable_end_to_end(site, category, brand, feed_dir):
    """Наскрізь: вимкнена категорія → фід порожній → масове вмикання → товар у фіді."""
    child = Category.objects.create(
        external_id="5609731",
        name="Двокамерні",
        slug="dvokamerni",
        parent=category,
        hotline_category=category.hotline_category,
    )
    make_product(category=child, brand=brand, sku="DEEP", hotline_enabled=False)

    assert services.generate_hotline_feed().items_count == 0

    tasks.set_hotline_for_category(category.pk, True)

    assert services.generate_hotline_feed().items_count == 1
    assert Product.objects.filter(hotline_enabled=True).count() == 1
