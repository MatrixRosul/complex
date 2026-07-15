"""
Синк довідників НП: ідемпотентність, деактивація, guard, smoke-тест (INTEGRATIONS §1.5).

Кожен тест тут прикриває сценарій, у якому баг зупиняє ПРОДАЖІ, а не «псує дані».
"""

from __future__ import annotations

import pytest

from delivery.client import NovaPoshtaClient, NovaPoshtaUnavailable
from delivery.models import NPArea, NPCity, NPSettlement, NPWarehouse
from delivery.tasks import sync_np_refs
from delivery.tests.conftest import (
    UZH_BRANCH_30KG_REF,
    UZHHOROD_SETTLEMENT_REF,
    make_failing_client,
)
from sync.models import SyncRun

pytestmark = pytest.mark.django_db

UZHHOROD_CITY_REF = "e221d627-391c-11dd-90d9-001a92567626"


def test_first_sync_populates_all_reference_tables(np_client):
    result = sync_np_refs(client=np_client)

    assert result["status"] == SyncRun.Status.SUCCESS
    assert NPArea.objects.count() == 3
    assert NPCity.objects.count() == 4
    assert NPSettlement.objects.count() == 5
    assert NPWarehouse.objects.count() == 10


def test_sync_is_idempotent(np_client):
    """Другий прогін тими самими даними НЕ дублює і НЕ деактивує нічого."""
    sync_np_refs(client=np_client)
    before = {
        "areas": NPArea.objects.count(),
        "cities": NPCity.objects.count(),
        "settlements": NPSettlement.objects.count(),
        "warehouses": NPWarehouse.objects.count(),
        "active_wh": NPWarehouse.objects.filter(is_active=True).count(),
    }

    result = sync_np_refs(client=np_client)

    assert result["status"] == SyncRun.Status.SUCCESS
    assert result["stats"]["warehouses_deactivated"] == 0
    assert result["stats"]["settlements_deactivated"] == 0
    assert before == {
        "areas": NPArea.objects.count(),
        "cities": NPCity.objects.count(),
        "settlements": NPSettlement.objects.count(),
        "warehouses": NPWarehouse.objects.count(),
        "active_wh": NPWarehouse.objects.filter(is_active=True).count(),
    }


def test_settlement_ref_and_city_ref_are_different_uuids(np_client):
    """🔴 SettlementRef ≠ CityRef — два різні UUID на той самий Ужгород.

    Канонічний для CityRecipient / getWarehouses — delivery_city_ref.
    """
    sync_np_refs(client=np_client)
    uzh = NPSettlement.objects.get(ref=UZHHOROD_SETTLEMENT_REF)

    assert uzh.delivery_city_ref == UZHHOROD_CITY_REF
    assert uzh.delivery_city_ref != uzh.ref


def test_warehouses_count_is_ours_not_nps(np_client):
    """Лічильник рахуємо САМІ по NPWarehouse: поле `Warehouses` з відповіді НП брехливе
    (317 для Ужгорода проти реальних 178) і як лічильник заборонене."""
    sync_np_refs(client=np_client)
    uzh = NPSettlement.objects.get(ref=UZHHOROD_SETTLEMENT_REF)

    # 5 відділень в Ужгороді у фікстурі, з них одне Closed → рахуємо тільки Working.
    assert uzh.warehouses_count == 4


def test_closed_warehouse_is_stored_but_not_offered(np_client):
    """WarehouseStatus != Working — зберігаємо (на нього посилаються старі замовлення),
    але покупцеві не пропонуємо."""
    sync_np_refs(client=np_client)
    closed = NPWarehouse.objects.get(ref="7b422fc3-e1c2-11e3-8c4a-0050568002cf")

    assert closed.status == "Closed"
    assert closed not in NPWarehouse.objects.active()


def test_vanished_warehouse_is_deactivated_never_deleted(np_client, monkeypatch):
    """🔴 Відділення закрилось і зникло з відповіді НП.

    Було: рядок лишався is_active=True НАЗАВЖДИ → покупець обирає неіснуюче відділення →
    замовлення створюється, А ТТН НЕ ВИБИВАЄТЬСЯ.
    DELETE теж не можна: на ref посилаються вже створені замовлення.
    """
    sync_np_refs(client=np_client)
    assert NPWarehouse.objects.get(ref=UZH_BRANCH_30KG_REF).is_active is True

    original = NovaPoshtaClient.fetch_all

    def without_that_branch(self, model, method, **props):
        rows = original(self, model, method, **props)
        if method == "getWarehouses":
            return [r for r in rows if r["Ref"] != UZH_BRANCH_30KG_REF]
        return rows

    monkeypatch.setattr(NovaPoshtaClient, "fetch_all", without_that_branch)
    result = sync_np_refs(client=np_client)

    gone = NPWarehouse.objects.get(ref=UZH_BRANCH_30KG_REF)  # НЕ DoesNotExist!
    assert gone.is_active is False
    assert result["stats"]["warehouses_deactivated"] == 1


def test_guard_aborts_on_sudden_drop(np_client, monkeypatch):
    """🔴 Частковий збій НП: замість 10 відділень прийшло 2.

    Без guard'а довідник збіднів би → у дропдауні немає відділень → замовлення оформити
    НЕМОЖЛИВО. Повна зупинка продажів через збій ЧУЖОГО API.
    """
    sync_np_refs(client=np_client)
    before = NPWarehouse.objects.filter(is_active=True).count()

    original = NovaPoshtaClient.fetch_all

    def truncated(self, model, method, **props):
        rows = original(self, model, method, **props)
        return rows[:2] if method == "getWarehouses" else rows

    monkeypatch.setattr(NovaPoshtaClient, "fetch_all", truncated)
    result = sync_np_refs(client=np_client)

    assert result["status"] == SyncRun.Status.ABORTED
    assert "warehouses" in result["guard"]["failures"][0]
    # ⚡ Довідник НЕ ЧІПАЛИ:
    assert NPWarehouse.objects.filter(is_active=True).count() == before


def test_guard_lets_the_very_first_sync_through(np_client):
    """Порожня БД (перший деплой) — guard не має її блокувати."""
    assert NPWarehouse.objects.count() == 0
    assert sync_np_refs(client=np_client)["status"] == SyncRun.Status.SUCCESS


def test_smoke_test_rolls_everything_back(np_client, monkeypatch):
    """Довідник без Ужгорода або Києва — битий довідник. Усе відкочується."""
    original = NovaPoshtaClient.fetch_all

    def no_uzhhorod(self, model, method, **props):
        rows = original(self, model, method, **props)
        if method == "getWarehouses":
            return [r for r in rows if r["CityRef"] != UZHHOROD_CITY_REF]
        return rows

    monkeypatch.setattr(NovaPoshtaClient, "fetch_all", no_uzhhorod)

    with pytest.raises(AssertionError, match="Ужгород"):
        sync_np_refs(client=np_client)

    assert NPWarehouse.objects.count() == 0  # транзакція відкочена цілком
    assert NPArea.objects.count() == 0


def test_dry_run_changes_nothing(np_client):
    result = sync_np_refs(dry_run=True, client=np_client)

    assert result["status"] == SyncRun.Status.SUCCESS
    assert result["stats"]["warehouses_upserted"] == 10
    # …але в БД — порожньо.
    assert NPWarehouse.objects.count() == 0
    assert NPSettlement.objects.count() == 0


def test_failed_fetch_leaves_the_directory_intact(np_client, breaker):
    """НП недоступна → FAILED, довідники недоторкані (а не наполовину оновлені)."""
    sync_np_refs(client=np_client)
    before = NPWarehouse.objects.filter(is_active=True).count()

    with pytest.raises(NovaPoshtaUnavailable):
        sync_np_refs(client=make_failing_client(breaker))

    assert NPWarehouse.objects.filter(is_active=True).count() == before
    assert SyncRun.objects.filter(status=SyncRun.Status.FAILED).exists()


def test_settlement_without_warehouses_still_gets_a_city_ref(np_client):
    """Село без жодного відділення: CityRef з дампа warehouses не візьмеш.

    Фолбек — зіставлення (Area, назва) з NPCity. Не спрацював — лишаємо порожнім, і
    getDocumentPrice толерантно прийме SettlementRef.
    """
    sync_np_refs(client=np_client)
    onokivtsi = NPSettlement.objects.get(name="Оноківці")

    assert onokivtsi.warehouses_count == 0
    assert onokivtsi.delivery_city_ref == ""  # такого міста в getCities немає


def test_present_is_built_for_autocomplete(np_client):
    sync_np_refs(client=np_client)

    assert NPSettlement.objects.get(name="Ужгород").present == "м. Ужгород, Закарпатська обл."
    assert (
        NPSettlement.objects.get(name="Оноківці").present
        == "с. Оноківці, Ужгородський р-н, Закарпатська обл."
    )
