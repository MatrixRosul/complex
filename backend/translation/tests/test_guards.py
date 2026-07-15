"""
Гарантії, які НЕ покладаються на промпт (TRANSLATION.md §3, §4).

Промпт можна проігнорувати. Валідатор — ні.
"""

from __future__ import annotations

import pytest

from translation.models import TranslationEntry, TranslationKind, TranslationStatus
from translation.segmentation import (
    apply_html,
    is_translatable,
    parse_html,
    split_name,
    validate_html_translation,
    validate_preserved,
)
from translation.services import collect_targets, enqueue_translation, translate_pending

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Бренд і код моделі
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "brand", "head", "tail"),
    [
        (
            "Варильна поверхня газова Bosch PNK6B2P40R",
            "Bosch",
            "Варильна поверхня газова",
            "Bosch PNK6B2P40R",
        ),
        ("Духова шафа Gorenje BO6737E02X", "Gorenje", "Духова шафа", "Gorenje BO6737E02X"),
        ("Холодильник", "Bosch", "Холодильник", ""),
        # Код моделі без бренда в БД — усе одно відрізається (містить літери І цифри).
        ("Пральна машина WM12N269UA", "", "Пральна машина", "WM12N269UA"),
    ],
)
def test_split_name_cuts_brand_and_model_code(name, brand, head, tail):
    assert split_name(name, brand) == (head, tail)


def test_pure_numeric_article_is_left_to_levels_2_and_3():
    """«Запчастина для духовки TEKA 83340602» — випадок, який РІВЕНЬ 1 свідомо не ловить.

    «83340602» — чисті цифри, а не код моделі (код = літери І цифри одночасно). Розширити
    регексп на «будь-який довгий числовий хвіст» — значить почати відрізати справжні числа
    з описових назв («Духова шафа 60 см»). Тому pure-numeric артикул лишається в тексті,
    а захищають його РІВЕНЬ 2 (правило в промпті) і РІВЕНЬ 3 (валідатор) — саме так, як
    описано в TRANSLATION.md §3.
    """
    name = "Запчастина для духовки TEKA 83340602"
    head, tail = split_name(name, brand="")
    assert (head, tail) == (name, "")  # рівень 1 нічого не відрізав — і це нормально

    # РІВЕНЬ 3 усе одно не дасть це зіпсувати:
    assert validate_preserved(name, "Запчасть для духовки TEKA 83340602")
    assert not validate_preserved(name, "Запчасть для духовки ТЕКА 83340602")  # бренд поїхав
    assert not validate_preserved(name, "Запчасть для духовки TEKA 83340603")  # артикул поїхав


def test_brand_and_model_code_never_enter_the_request(make_product, fake_client):
    """РІВЕНЬ 1: те, чого модель не бачить, вона не може зіпсувати."""
    make_product("Варильна поверхня газова Bosch PNK6B2P40R", sku="P-1")
    collect_targets({TranslationKind.PRODUCT_NAME})
    translate_pending(kind=TranslationKind.PRODUCT_NAME, client=fake_client)

    sent = fake_client.sent_texts
    assert sent == ["Варильна поверхня газова"]
    # У жодному запиті немає ані бренда, ані артикула.
    assert not any("Bosch" in t for t in sent)
    assert not any("PNK6B2P40R" in t for t in sent)

    entry = TranslationEntry.objects.get(kind=TranslationKind.PRODUCT_NAME)
    # Збірка назад — з нашого боку, а не з відповіді моделі.
    assert entry.target_text == "Варочная поверхность газовая Bosch PNK6B2P40R"
    assert entry.status == TranslationStatus.MACHINE


def test_mangled_brand_is_caught_by_validator(make_product):
    """РІВЕНЬ 3: якщо модель усе-таки зіпсувала латиницю — це FAILED, а не чергa схвалення."""
    from translation.tests.conftest import FakeClient

    # Модель «переклала» назву й дописала латиницю, якої не було в джерелі.
    evil = FakeClient(mangle=lambda uk: "Варочная поверхность газовая Bosh")

    make_product("Варильна поверхня газова Bosch PNK6B2P40R", sku="P-2")
    collect_targets({TranslationKind.PRODUCT_NAME})
    translate_pending(kind=TranslationKind.PRODUCT_NAME, client=evil)

    entry = TranslationEntry.objects.get(kind=TranslationKind.PRODUCT_NAME)
    assert entry.status == TranslationStatus.FAILED
    assert "latin_tokens" in entry.validation_errors
    # Невалідований переклад не потрапив навіть у чергу на схвалення.
    assert entry.published_text == ""


def test_validate_preserved():
    assert validate_preserved("Гарантія 12 міс Bosch", "Гарантия 12 мес Bosch")
    assert not validate_preserved("Bosch PNK6B2P40R", "Бош PNK6B2P40R")  # бренд транслітеровано
    assert not validate_preserved("A+++ клас", "A++ класс")  # клас енергоспоживання поїхав
    assert not validate_preserved("284 мм", "285 мм")  # число підмінено


def test_is_translatable():
    assert is_translatable("Нержавіюча сталь")
    assert not is_translatable("Gorenje")
    assert not is_translatable("284")
    assert not is_translatable("A+++")
    assert not is_translatable("")


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

ROBOT_HTML = """\
<h2>Робот-пилосос</h2>
<p>Потужне всмоктування <b>2700 Па</b> прибирає навіть шерсть.</p>
<figure>
  <img src="https://r2.complex.ua/img/a1b2.jpg" alt="Фото режиму вологого прибирання" class="w-full">
  <figcaption>Час роботи до 180 хв</figcaption>
</figure>
<p>Купуйте <a href="https://complex.ua/p/1" rel="nofollow">тут</a></p>\
"""


def test_html_segments_contain_no_markup():
    """У модель їде ТІЛЬКИ текст. Ані тегів, ані URL."""
    doc = parse_html(ROBOT_HTML)
    texts = [s.uk for s in doc.segments]

    assert "Потужне всмоктування <0>2700 Па</0> прибирає навіть шерсть." in texts
    assert "Фото режиму вологого прибирання" in texts  # img@alt — окремий сегмент
    assert "Час роботи до 180 хв" in texts
    assert "Купуйте <0>тут</0>" in texts

    blob = " ".join(texts)
    assert "https://r2.complex.ua" not in blob  # модель фізично не бачить R2-URL
    assert "<img" not in blob
    assert "href" not in blob
    assert "class" not in blob


def test_html_roundtrip_preserves_markup_bit_for_bit(make_product, fake_client):
    """Теги, атрибути, <img src>, порядок вузлів — біт-у-біт ті самі."""
    make_product("Робот-пилосос Xiaomi S20", sku="R-1", description=ROBOT_HTML)
    collect_targets({TranslationKind.PRODUCT_DESCRIPTION})
    translate_pending(kind=TranslationKind.PRODUCT_DESCRIPTION, client=fake_client)

    entry = TranslationEntry.objects.get(kind=TranslationKind.PRODUCT_DESCRIPTION)
    assert entry.status == TranslationStatus.MACHINE, entry.validation_errors
    out = entry.target_text

    # Розмітка ціла
    assert 'src="https://r2.complex.ua/img/a1b2.jpg"' in out
    assert 'class="w-full"' in out
    assert 'href="https://complex.ua/p/1"' in out
    assert 'rel="nofollow"' in out
    assert "<figure>" in out and "<figcaption>" in out and "<b>" in out

    # Текст перекладено
    assert "Мощное всасывание <b>2700 Па</b> убирает даже шерсть." in out
    assert 'alt="Фото режима влажной уборки"' in out
    assert "Время работы до 180 мин" in out
    assert "Покупайте <a" in out

    # І жодної помилки валідації
    assert entry.validation_errors == []


def test_html_validator_catches_dropped_image():
    """Якщо переклад загубив плейсхолдер <img> — це FAILED, а не «майже добре»."""
    doc = parse_html("<p>Текст <b>жирний</b> далі</p>")
    seg = doc.segments[0]
    assert seg.uk == "Текст <0>жирний</0> далі"

    # Модель «забула» інлайновий тег.
    bad = {seg.id: "Текст жирный далее"}
    out = apply_html(parse_html("<p>Текст <b>жирний</b> далі</p>"), bad)

    errs = validate_html_translation("<p>Текст <b>жирний</b> далі</p>", out, doc.segments, bad)
    assert f"inline_tags:{seg.id}" in errs
    assert "tag_multiset" in errs  # <b> зник із DOM


def test_html_validator_catches_broken_url():
    src = '<p>Фото</p><p><img src="https://r2.complex.ua/a.jpg" alt="Фото"></p>'
    doc = parse_html(src)
    good = {s.id: s.uk for s in doc.segments}
    out = apply_html(parse_html(src), good)
    # URL не проходив через модель → він на місці
    assert validate_html_translation(src, out, doc.segments, good) == []
    assert "https://r2.complex.ua/a.jpg" in out

    # А якщо хтось руками зіпсує URL — валідатор це побачить.
    broken = out.replace("https://r2.complex.ua/a.jpg", "https://evil.example/a.jpg")
    errs = validate_html_translation(src, broken, doc.segments, good)
    assert "urls" in errs


def test_numbers_and_units_are_checked(attr_group):
    """«12 міс» → «12 мес» ок. «12 міс» → «10 мес» — ні."""
    from translation.segmentation import validate_numbers

    assert validate_numbers("Гарантія 12 міс", "Гарантия 12 мес")
    assert validate_numbers("Висота 284 мм", "Высота 284 мм")
    assert not validate_numbers("Гарантія 12 міс", "Гарантия 10 мес")
    assert not validate_numbers("Потужність 2700 Па", "Мощность 2000 Па")


# ---------------------------------------------------------------------------
# Ідемпотентність і кеш по source_hash
# ---------------------------------------------------------------------------


def test_same_text_twice_gives_one_entry(attribute):
    """Двічі enqueue того самого поля → ОДИН запис (кеш по source_hash)."""
    e1 = enqueue_translation(attribute, "name", TranslationKind.ATTRIBUTE_NAME)
    e2 = enqueue_translation(attribute, "name", TranslationKind.ATTRIBUTE_NAME)

    assert e1.pk == e2.pk
    assert TranslationEntry.objects.count() == 1
    assert e2.source_hash == e1.source_hash


def test_cosmetic_whitespace_change_does_not_retrigger_payment(attribute, fake_client, approve):
    """Подвійний пробіл — не привід платити за переклад ще раз (NFC + згортання пробілів)."""
    enqueue_translation(attribute, "name", TranslationKind.ATTRIBUTE_NAME)
    translate_pending(client=fake_client)
    entry = TranslationEntry.objects.get(kind=TranslationKind.ATTRIBUTE_NAME)
    approve(entry)

    attribute.name_uk = "Колір  виробу "  # подвійний пробіл + хвостовий
    attribute.save(update_fields=["name_uk"])

    enqueue_translation(attribute, "name", TranslationKind.ATTRIBUTE_NAME)
    entry.refresh_from_db()
    assert entry.status == TranslationStatus.APPROVED, "косметика не має скидати переклад у STALE"


def test_source_change_marks_stale_but_site_keeps_old_ru(attribute, fake_client, approve):
    """Джерело змінилось → STALE. Сайт далі показує СТАРИЙ СХВАЛЕНИЙ переклад."""
    enqueue_translation(attribute, "name", TranslationKind.ATTRIBUTE_NAME)
    translate_pending(client=fake_client)
    entry = TranslationEntry.objects.get()
    approve(entry)
    attribute.refresh_from_db()
    assert attribute.name_ru == "Цвет изделия"

    attribute.name_uk = "Тип управління"
    attribute.save(update_fields=["name_uk"])
    enqueue_translation(attribute, "name", TranslationKind.ATTRIBUTE_NAME)

    entry.refresh_from_db()
    attribute.refresh_from_db()
    assert entry.status == TranslationStatus.STALE
    assert entry.published_text == "Цвет изделия"  # НЕ чіпали
    assert attribute.name_ru == "Цвет изделия"  # вітрина не порожня і не «наполовину»


def test_translation_memory_makes_duplicates_free(make_product, fake_client, approve):
    """Той самий текст в іншого об'єкта → копіюємо схвалене, $0 і 0 запитів."""
    p1 = make_product("Холодильник двокамерний Bosch KGN1X", sku="M-1")
    e1 = enqueue_translation(p1, "name", TranslationKind.PRODUCT_NAME)
    translate_pending(client=fake_client)
    e1.refresh_from_db()
    approve(e1)
    n_before = len(fake_client.requests)

    # Другий товар з ТІЄЮ САМОЮ назвою (буває: дублі в прайсі).
    p2 = make_product("Холодильник двокамерний Bosch KGN1X", sku="M-2")
    e2 = enqueue_translation(p2, "name", TranslationKind.PRODUCT_NAME)

    assert e2.status == TranslationStatus.APPROVED
    assert e2.published_text == e1.published_text
    assert e2.cost_usd == 0

    translate_pending(client=fake_client)
    assert len(fake_client.requests) == n_before, "пам'ять перекладів має коштувати 0 запитів"
