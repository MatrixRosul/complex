"""Сервіси: файл за постійним URL, лічильники в FeedArtifact, is_current, heal."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from lxml import etree

from feeds import services
from feeds.generator import SKIP_NO_IMAGE, SKIP_NO_PRICE
from feeds.models import FeedArtifact
from feeds.tests.conftest import make_product
from sync.models import SyncRun

pytestmark = pytest.mark.django_db


def test_generate_writes_file_to_permanent_path(site, product, feed_dir):
    artifact = services.generate_hotline_feed()

    path = services.feed_path()
    assert path == feed_dir / "hotline.xml"  # ПОСТІЙНИЙ шлях: Hotline забирає фід сам
    assert path.exists()
    assert artifact.size_bytes == path.stat().st_size

    root = etree.fromstring(path.read_bytes())  # файл на диску — валідний XML
    assert root.findtext("items/item/id") == str(product.pk)
    # FileField вказує на той самий фіксований шлях — жодних hotline_XyZ123.xml
    assert artifact.file.name == "feeds/hotline.xml"


def test_artifact_records_counters_and_reasons(site, category, brand, feed_dir):
    make_product(category=category, brand=brand, sku="OK", mpn="MPN-1")
    make_product(category=category, brand=brand, sku="NOMPN", mpn="")
    make_product(category=category, brand=brand, sku="NOPRICE", price=Decimal("0"))
    make_product(category=category, brand=brand, sku="NOIMG", image="")

    artifact = services.generate_hotline_feed()

    assert artifact.items_count == 2
    assert artifact.skipped_count == 2
    assert artifact.skipped_reasons == {SKIP_NO_PRICE: 1, SKIP_NO_IMAGE: 1}
    # ⚡ no_mpn — окремий ЛІЧИЛЬНИК, не причина скіпу
    assert artifact.no_mpn_count == 1
    assert "no_mpn" not in artifact.skipped_reasons
    assert artifact.duration_ms is not None


def test_only_one_current_artifact_per_kind(site, product, feed_dir):
    """uniq_current_feed — частковий unique (PG14). Перемикання мусить бути двокроковим."""
    first = services.generate_hotline_feed()
    second = services.generate_hotline_feed()

    first.refresh_from_db()
    second.refresh_from_db()

    assert not first.is_current
    assert second.is_current
    assert (
        FeedArtifact.objects.filter(kind=FeedArtifact.Kind.HOTLINE_XML, is_current=True).count()
        == 1
    )
    assert services.current_artifact() == second


def test_generation_is_logged_as_sync_run(site, product, feed_dir):
    artifact = services.generate_hotline_feed(trigger=SyncRun.Trigger.MANUAL)

    run = artifact.run
    assert run.kind == SyncRun.Kind.HOTLINE_FEED
    assert run.status == SyncRun.Status.SUCCESS
    assert run.trigger == SyncRun.Trigger.MANUAL
    assert run.stats["items"] == 1


def test_dry_run_writes_nothing(site, product, feed_dir):
    artifact = services.generate_hotline_feed(dry_run=True)

    assert artifact.pk is None  # артефакт НЕ збережено
    assert artifact.items_count == 1  # …але лічильники пораховані по-справжньому
    assert not services.feed_path().exists()
    assert not FeedArtifact.objects.exists()
    assert not SyncRun.objects.exists()


def test_atomic_write_replaces_file_in_place(tmp_path):
    path = tmp_path / "hotline.xml"
    services.atomic_write(path, b"<price/>")
    services.atomic_write(path, b"<price>new</price>")

    assert path.read_bytes() == b"<price>new</price>"
    # Жодних недописаних .tmp-хвостів поруч — бот не має шансу спіймати половину файла.
    assert [p.name for p in tmp_path.iterdir()] == ["hotline.xml"]


def test_feed_is_stale_when_file_missing(site, product, feed_dir):
    services.generate_hotline_feed()
    assert services.feed_is_stale() == (False, "")

    services.feed_path().unlink()  # класика: `compose up -d` зніс шар контейнера

    stale, reason = services.feed_is_stale()
    assert stale
    assert reason == "file_missing"


def test_feed_is_stale_when_older_than_24h(site, product, feed_dir):
    artifact = services.generate_hotline_feed()
    FeedArtifact.objects.filter(pk=artifact.pk).update(
        generated_at=timezone.now() - timedelta(hours=25)
    )

    stale, reason = services.feed_is_stale()

    assert stale
    assert reason.startswith("stale_")


def test_feed_is_stale_without_artifact(db, site, feed_dir):
    assert services.feed_is_stale() == (True, "no_current_artifact")
