"""
Тести ядра парсингу (SYNC.md §11, INPUTS.md §3).

Дані НЕ вигадані: назви, ціни, характеристики й пастки взяті з реальних скрінів прайсу
й таблиці характеристик (`docs/reference/screens/`). Де значення прийшло зі скріна —
поруч стоїть посилання на нього.

БД не потрібна: увесь модуль — чисті функції.
"""

from decimal import Decimal

import pytest

from sync.parsing.normalize import (
    PLACEHOLDER_VALUES,
    clean_or_empty,
    find_spec_match,
    is_placeholder,
    model_candidates,
    normalize_model,
)
from sync.parsing.numbers import (
    NumberParseError,
    is_ambiguous_thousands,
    parse_decimal,
    parse_int,
    parse_qty,
)
from sync.parsing.rows import (
    MAX_SANE_PRICE,
    Reason,
    RowKind,
    classify_row,
    is_section_header,
    split_photos,
)
from sync.parsing.specs import (
    PackageDims,
    Spec,
    extract_package_dims_and_weight,
    format_spec,
    format_spec_line,
    format_spec_value,
    parse_package_dims,
    parse_spec_triples,
    parse_warranty,
)

NBSP = " "  # нерозривний пробіл — Sheets сипле ним у назви
NNBSP = " "  # вузький нерозривний


# =============================================================================
# 1. normalize.py — порт скрипта Артура
# =============================================================================


class TestNormalizeModel:
    def test_real_price_names(self):
        """Реальні назви з прайсу (скрін photo_5359407930693851301)."""
        assert normalize_model("Варильна поверхня газова Bosch PNK6B2P40R") == "boschpnk6b2p40r"
        assert (
            normalize_model("Вбудований комплект Gorenje GI6401BSC + BOS6737E06BG")
            == "gorenjegi6401bscbos6737e06bg"
        )

    def test_nbsp_and_narrow_space_collapse(self):
        """NBSP і вузький нерозривний → звичайний пробіл → зникають зовсім."""
        assert normalize_model(f"Bosch{NBSP}PNK6B2P40R") == "boschpnk6b2p40r"
        assert normalize_model(f"Bosch{NNBSP}PNK6B2P40R") == "boschpnk6b2p40r"
        assert normalize_model("Bosch ​ PNK6B2P40R") == "boschpnk6b2p40r"

    def test_cyrillic_i_becomes_latin(self):
        """Кирилична І/і — візуальний двійник латинської. Обидві → латинські."""
        # "SІEMENS" з КИРИЛИЧНОЮ І всередині — око не бачить різниці, ключ ламається.
        assert normalize_model("SІEMENS HZ66D910") == "siemenshz66d910"
        assert normalize_model("Sіemens HZ66D910") == "siemenshz66d910"
        # …і збігається з чисто латинським написанням:
        assert normalize_model("Siemens HZ66D910") == normalize_model("SІEMENS HZ66D910")

    def test_all_dash_kinds_become_hyphen(self):
        """‒ – — − усі → "-". Дефіс лишається в білому списку, тому видно результат."""
        for dash in "‒–—−":
            assert normalize_model(f"LG GA{dash}B509CQTL") == "lgga-b509cqtl"

    def test_apostrophes_quotes_trademarks_dropped(self):
        """Апострофи/лапки нормалізуються, а потім їх (як і ®™) зрізає білий список."""
        assert normalize_model("Bosch® PNK6B2P40R™") == "boschpnk6b2p40r"
        assert normalize_model('Bosch "PNK6B2P40R"') == "boschpnk6b2p40r"
        assert normalize_model("Bosch ’PNK6B2P40R‘") == "boschpnk6b2p40r"

    def test_multiplication_sign_becomes_x(self):
        """× → x: критично для габаритів «60×65×185»."""
        assert normalize_model("60×65×185") == "60x65x185"

    def test_nfkc_normalization(self):
        """NFKC: повноширинні й лігатурні форми зводяться до звичайних."""
        assert normalize_model("ＢＯＳＣＨ") == "bosch"  # повноширинні латинські

    def test_allowed_charset_only(self):
        """Лишається тільки [a-z0-9._\\-/ ] — і пробіли теж зникають."""
        assert normalize_model("Bosch PNK-6.B2/P40R") == "boschpnk-6.b2/p40r"
        assert normalize_model("Bosch + PNK6B2P40R!") == "boschpnk6b2p40r"

    def test_empty_and_none(self):
        assert normalize_model("") == ""
        assert normalize_model(None) == ""

    def test_cyrillic_i_leaks_from_prose(self):
        """
        ⚠️ Задокументована особливість, УСПАДКОВАНА від скрипта Артура.

        Мапа `і → i` не відрізняє літеру в моделі від літери у звичайному слові, тому «і»
        з описової частини назви просочується в ключ латинською. Це не баг цього порту —
        так само поводиться оригінал; фіксуємо тестом, щоб зміна поведінки була ПОМІЧЕНОЮ,
        а не тихою. Саме тому нормалізована назва в нас — РЕЗЕРВНИЙ ключ, а основний — артикул.
        """
        assert normalize_model("Вакуумні пакети Siemens HZ66D910") == "isiemenshz66d910"
        #                            ^-- "і" з «Вакуумні»


class TestModelCandidates:
    def test_original_first(self):
        assert model_candidates("Bosch PNK6B2P40R") == ["boschpnk6b2p40r"]

    def test_truncation_by_paren(self):
        assert model_candidates("Bosch PNK6B2P40R (нержавіюча сталь)") == [
            "boschpnk6b2p40ri",  # «і» з «нержавіюча» — див. test_cyrillic_i_leaks_from_prose
            "boschpnk6b2p40r",
        ]

    def test_truncation_by_comma(self):
        assert model_candidates("Холодильник LG GA-B509CQTL, 203 см") == [
            "lgga-b509cqtl203",
            "lgga-b509cqtl",
        ]

    def test_truncation_by_bracket_and_pipe_and_dash(self):
        assert "boschpnk6b2p40r" in model_candidates("Bosch PNK6B2P40R [нова]")
        assert "boschpnk6b2p40r" in model_candidates("Bosch PNK6B2P40R | акція")
        assert "boschpnk6b2p40r" in model_candidates("Bosch PNK6B2P40R - уцінка")

    def test_no_duplicates_and_no_empty(self):
        cands = model_candidates("Bosch PNK6B2P40R")
        assert len(cands) == len(set(cands))
        assert "" not in cands
        assert model_candidates("") == []
        assert model_candidates("(лише дужка)") == []  # усе кирилиця → порожньо


class TestFindSpecMatch:
    def test_exact_match(self):
        index = {"boschpnk6b2p40r": "SPEC-A", "gorenjegi6401bsc": "SPEC-B"}
        assert find_spec_match("Варильна поверхня газова Bosch PNK6B2P40R", index) == "SPEC-A"

    def test_exact_match_via_candidate_truncation(self):
        """Точного збігу по повній назві немає, але є по обрізку до «(»."""
        index = {"boschpnk6b2p40r": "SPEC-A"}
        assert find_spec_match("Bosch PNK6B2P40R (нержавіюча сталь)", index) == "SPEC-A"

    def test_substring_fallback_unique(self):
        """Фолбек: у таблиці характеристик назва довша — унікальне входження підрядка."""
        index = {"boschpnk6b2p40rвбудована": "SPEC-A", "gorenjegi6401bsc": "SPEC-B"}
        assert find_spec_match("Bosch PNK6B2P40R", index) == "SPEC-A"

    def test_substring_fallback_reverse_direction(self):
        """Назва в прайсі довша за назву в таблиці характеристик — теж має знайти."""
        index = {"pnk6b2p40r": "SPEC-A"}
        assert find_spec_match("Bosch PNK6B2P40R", index) == "SPEC-A"

    def test_ambiguous_substring_returns_none(self):
        """
        ГОЛОВНИЙ тест фолбеку: збігів більше одного → None. НАВМИСНО.

        «PNK6B2P40R» і «PNK6B2P40R2» — дві РІЗНІ плити. Вгадати не можна, а приліпити
        товару чужі габарити означає занижену доставку на кожній посилці.
        Порожні габарити ловить фільтр адмінки. Чужі — не ловить ніхто.
        """
        index = {"boschpnk6b2p40r": "SPEC-A", "boschpnk6b2p40r2": "SPEC-B"}
        assert find_spec_match("PNK6B2P40R", index) is None

    def test_exact_match_wins_over_ambiguity(self):
        """Якщо точний збіг є — неоднозначність підрядків уже не має значення."""
        index = {"boschpnk6b2p40r": "SPEC-A", "boschpnk6b2p40r2": "SPEC-B"}
        assert find_spec_match("Bosch PNK6B2P40R", index) == "SPEC-A"

    def test_no_match(self):
        assert find_spec_match("Whirlpool XYZ123", {"boschpnk6b2p40r": "SPEC-A"}) is None

    def test_empty_inputs(self):
        assert find_spec_match("", {"a": 1}) is None
        assert find_spec_match("Bosch", {}) is None

    def test_short_candidate_does_not_match_everything(self):
        """Короткий кандидат («lg») у фолбек не йде — інакше зачепив би пів каталогу."""
        assert find_spec_match("LG", {"lgga-b509cqtl": "SPEC-A"}) is None


class TestPlaceholders:
    def test_known_placeholders(self):
        for value in ["Уточнюється", "уточнюється", "немає даних", "-", "—", "", "  ", None]:
            assert is_placeholder(value), value

    def test_placeholder_with_trailing_punctuation(self):
        assert is_placeholder("Уточнюється.")
        assert is_placeholder("Уточнюється:")

    def test_real_values_are_not_placeholders(self):
        for value in ["Bosch", "Німеччина", "0", "c50549829"]:
            assert not is_placeholder(value), value

    def test_clean_or_empty(self):
        assert clean_or_empty("Уточнюється") == ""
        assert clean_or_empty(f"Bosch{NBSP}GmbH") == "Bosch GmbH"

    def test_required_set_is_covered(self):
        """Мінімальний набір із ТЗ — підмножина нашого (ми ширші)."""
        assert {"уточнюється", "немає даних", "-", "—", ""} <= PLACEHOLDER_VALUES


# =============================================================================
# 2. numbers.py — українська локаль
# =============================================================================


class TestParseDecimal:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("6 600,00", Decimal("6600.00")),  # звичайний пробіл-тисячі
            (f"6{NBSP}600,00", Decimal("6600.00")),  # NBSP — те, що реально в Sheets
            (f"6{NNBSP}600,00", Decimal("6600.00")),  # вузький нерозривний
            ("6'600,00", Decimal("6600.00")),  # апостроф-тисячі
            ("6’600,00", Decimal("6600.00")),  # типографський апостроф
            ("41,65", Decimal("41.65")),  # курс USD з E4
            ("145,00", Decimal("145.00")),  # ціна з прайсу
            ("145.00", Decimal("145.00")),  # крапка
            ("61.5", Decimal("61.5")),  # вага з таблиці характеристик
            ("58", Decimal("58")),
            (f"27{NBSP}445,50 ₴", Decimal("27445.50")),  # SYNC.md §11 — з валютою
            ("1 234,56 грн", Decimal("1234.56")),
            ("$145.00", Decimal("145.00")),
        ],
    )
    def test_ukrainian_locale(self, raw, expected):
        assert parse_decimal(raw) == expected

    @pytest.mark.parametrize("raw", ["", None, "—", "-", "Уточнюється", "   ", "немає даних"])
    def test_empty_and_placeholders_are_none(self, raw):
        assert parse_decimal(raw) is None

    def test_numeric_input_passthrough(self):
        """Sheets при UNFORMATTED_VALUE віддає число, а не рядок — не ламаємось."""
        assert parse_decimal(6600) == Decimal("6600")
        assert parse_decimal(41.65) == Decimal("41.65")
        assert parse_decimal(Decimal("145.00")) == Decimal("145.00")

    def test_bool_is_rejected(self):
        """bool — підклас int. Без явної перевірки True тихо став би Decimal("1")."""
        with pytest.raises(NumberParseError):
            parse_decimal(True)

    @pytest.mark.parametrize("raw", ["домовитись", "від 5000", "abc", "1.2.3"])
    def test_garbage_raises(self, raw):
        with pytest.raises(NumberParseError):
            parse_decimal(raw)

    def test_garbage_is_a_valueerror(self):
        """SYNC.md §4.1 обіцяє викликачу, що це ловиться як ValueError."""
        assert issubclass(NumberParseError, ValueError)

    def test_mixed_separators_rightmost_wins(self):
        assert parse_decimal("1.234,56") == Decimal("1234.56")  # європейський
        assert parse_decimal("1,234.56") == Decimal("1234.56")  # американський

    def test_documented_ambiguity_trap(self):
        """
        ⚠️ Свідомо лишена пастка (SYNC.md §4.1): "6,600" → 6.600, а НЕ 6600.

        Автовизначити неможливо — "41,65" виглядає ТОЧНО ТАК САМО. Локаль зафіксована
        (кома = десяткова), а неоднозначність ловить окремий прапорець + guard #2.
        """
        assert parse_decimal("6,600") == Decimal("6.600")
        assert is_ambiguous_thousands("6,600") is True
        # …а нормальні ціни під прапорець НЕ підпадають:
        assert is_ambiguous_thousands("145,00") is False
        assert is_ambiguous_thousands("41,65") is False
        assert is_ambiguous_thousands("6 600,00") is False


class TestParseIntAndQty:
    def test_parse_int(self):
        assert parse_int("2,00") == 2  # так Sheets пише кількість
        assert parse_int("2.0") == 2
        assert parse_int(2.0) == 2
        assert parse_int("10") == 10
        assert parse_int("") is None

    def test_parse_int_rejects_fractional(self):
        """int(Decimal("0.5")) == 0 перетворило б товар на «немає в наявності». Краще гучно."""
        with pytest.raises(NumberParseError):
            parse_int("1,5")

    def test_qty_empty_is_none_not_zero(self):
        """
        КРИТИЧНО (SYNC.md крок 9): порожня кількість → None, а НЕ 0.

        None → «кількість невідома» → наявність визначає сам факт присутності рядка
        в прайсі → default_availability (товар Є).
        0    → «кількості немає» → OUT_OF_STOCK (товару НЕМА).

        Клієнтський лист UAH взагалі не має колонки «К-сть». Якби порожнє поле давало 0,
        весь прайс миттєво став би «Немає в наявності».
        """
        assert parse_qty("") is None
        assert parse_qty(None) is None
        assert parse_qty("—") is None
        assert parse_qty("Уточнюється") is None

        assert parse_qty("0") == 0  # …а ось ЦЕ — справді «немає»
        assert parse_qty("0,00") == 0

    def test_qty_normal(self):
        assert parse_qty("2,00") == 2
        assert parse_qty(2) == 2
        assert parse_qty("-1") == -1  # від'ємне НЕ ховаємо: qty<=0 → OUT_OF_STOCK

    def test_qty_with_unit(self):
        assert parse_qty("2 шт") == 2
        assert parse_qty("2 шт.") == 2


# =============================================================================
# 3. rows.py — пастки прайсу (INPUTS.md §3.2)
# =============================================================================


def _row(**kwargs):
    """Рядок прайсу, уже розкладений по логічних колонках (через column_map)."""
    base = {"name": "", "sku": "", "price": "", "qty": "", "category": "", "photo": ""}
    return base | kwargs


class TestSectionHeader:
    def test_real_section_row(self):
        """Пастка 1: «Аксесуари до техніки» — назва є, ціни й артикула немає."""
        row = _row(name="Аксесуари до техніки")
        assert is_section_header(row) is True

        verdict = classify_row(row)
        assert verdict.kind is RowKind.SECTION
        assert verdict.reason is Reason.SECTION_HEADER
        assert verdict.warnings == ()  # МОВЧКИ. Не помилка, не WARN.

    def test_normal_product_is_not_a_section(self):
        row = _row(name="Вакуумні пакети Siemens HZ66D910", sku="2400042", price="145,00")
        assert is_section_header(row) is False

    def test_row_with_qty_is_not_a_section(self):
        """
        ⚠️ Ключова відмінність. `Запчастина для духовки TEKA 83340602` має назву і К-сть,
        але не має ні артикула, ні ціни (INPUTS.md §3.2, пастка 3).

        Якби is_section_header дивилась тільки на sku+price, цей рядок збігся б із секцією
        і зник МОВЧКИ — замість WARN. А це реальний товар, який не потрапить на сайт.
        """
        row = _row(name="Запчастина для духовки TEKA 83340602", qty="1,00")
        assert is_section_header(row) is False
        assert classify_row(row).reason is Reason.ROW_NO_SKU

    def test_section_name_list_is_not_hardcoded(self):
        """Нову секцію Артур допише будь-яку — ловимо за ФОРМОЮ, не за списком імен."""
        assert is_section_header(_row(name="Витяжки та комплектуючі")) is True


class TestClassifyRow:
    def test_empty_row_skipped_silently(self):
        """Рядок 0 матриці: хвіст сітки Sheets. Мовчки, без WARN."""
        verdict = classify_row(_row())
        assert verdict.kind is RowKind.SKIP
        assert verdict.reason is Reason.EMPTY_ROW
        assert verdict.warnings == ()
        assert verdict.is_error is False

    def test_product_without_sku_becomes_draft(self):
        """Рядок 2 матриці: товар без артикула → DATA + WARN (сурогатний ключ, товар схований).

        ⚠️ Було SKIP. На реальному прайсі це означало тихо втратити 237 рядків з 836:
        «Уточнюється» в колонці «Артикул» — це не сміття, а товар, який замовник ще не
        дозаповнив. Тепер такий рядок стає товаром із сурогатним ключем, але `is_active=False`.
        """
        verdict = classify_row(_row(name="Запчастина для духовки TEKA 83340602", price="500,00"))
        assert verdict.kind is RowKind.DATA  # ← товар БУДЕ
        assert verdict.reason is Reason.ROW_NO_SKU
        assert verdict.is_error is False  # ← але це не помилка: прогін лишається SUCCESS
        assert verdict.price == Decimal("500.00")
        assert len(verdict.warnings) == 1
        assert "ROW_NO_SKU" in verdict.warnings[0]

    def test_placeholder_sku_is_the_same_as_no_sku(self):
        """«Уточнюється» в колонці «Артикул» = АРТИКУЛА НЕМАЄ (237 рядків реального прайсу).

        Якби плейсхолдер вважався значенням, усі 237 рядків отримали б ОДИН артикул
        «УТОЧНЮЄТЬСЯ» — тобто злиплись би в один товар.
        """
        verdict = classify_row(_row(name="Саундбар LG S70TR", price="280,00", sku="Уточнюється"))
        assert verdict.kind is RowKind.DATA
        assert verdict.reason is Reason.ROW_NO_SKU

    def test_row_without_sku_and_without_price_is_skipped(self):
        """Рядок 2а: ні артикула, ні ціни → товару не буде (ціна NOT NULL і > 0)."""
        verdict = classify_row(_row(name="Запчастина для духовки TEKA 83340602", qty="2"))
        assert verdict.kind is RowKind.SKIP
        assert verdict.reason is Reason.ROW_NO_SKU
        assert verdict.is_error is False

    def test_sku_without_name_is_error(self):
        """Рядок 3 матриці: артикул без назви = зсув колонок → ERROR."""
        verdict = classify_row(_row(sku="2400042", price="145,00"))
        assert verdict.kind is RowKind.SKIP
        assert verdict.reason is Reason.ROW_INVALID
        assert verdict.is_error is True

    @pytest.mark.parametrize("price", ["", "0", "0,00", "-1", "домовитись", "Уточнюється"])
    def test_broken_price_is_error(self, price):
        """Рядок 4 матриці. «Уточнюється» в ЦІНІ — це битий рядок, а не порожнє поле."""
        verdict = classify_row(_row(name="Bosch PNK6B2P40R", sku="2400042", price=price))
        assert verdict.reason is Reason.ROW_INVALID, price
        assert verdict.is_error is True

    def test_price_over_limit_rejected(self):
        """Рядок 5 матриці: з'їхала кома (27445 → 2744500…). Ловимо ДО БД."""
        ok = classify_row(_row(name="Плита", sku="1", price=str(MAX_SANE_PRICE)))
        assert ok.kind is RowKind.DATA

        bad = classify_row(_row(name="Плита", sku="1", price=str(MAX_SANE_PRICE + 1)))
        assert bad.reason is Reason.ROW_INVALID
        assert "кома" in bad.detail

    def test_valid_row(self):
        """Рядок 6 матриці — реальний рядок зі скріна photo_5359407930693851301."""
        verdict = classify_row(
            _row(
                name="Вакуумні пакети Siemens HZ66D910",
                sku="2400042",
                price="145,00",
                qty="2,00",
                category="c50549829",
            )
        )
        assert verdict.kind is RowKind.DATA
        assert verdict.reason is Reason.OK
        assert verdict.is_data is True
        assert verdict.price == Decimal("145.00")
        assert verdict.qty == 2
        assert verdict.warnings == ()

    def test_valid_row_without_qty_column(self):
        """Клієнтський лист UAH не має колонки «К-сть» → qty=None, а НЕ 0. Товар лишається."""
        verdict = classify_row(_row(name="Bosch PNK6B2P40R", sku="2400042", price="6 600,00"))
        assert verdict.kind is RowKind.DATA
        assert verdict.qty is None
        assert verdict.price == Decimal("6600.00")

    def test_garbage_qty_warns_but_keeps_row(self):
        """Сміття в К-сті — не привід викидати товар з каталогу. WARN + qty=None."""
        verdict = classify_row(
            _row(name="Bosch PNK6B2P40R", sku="2400042", price="6 600,00", qty="багато")
        )
        assert verdict.kind is RowKind.DATA
        assert verdict.qty is None
        assert any("QTY_INVALID" in w for w in verdict.warnings)

    def test_ambiguous_thousands_warns(self):
        verdict = classify_row(_row(name="Плита", sku="1", price="6,600"))
        assert verdict.kind is RowKind.DATA
        assert any("AMBIGUOUS_NUMBER" in w for w in verdict.warnings)

    def test_nbsp_name_is_cleaned(self):
        verdict = classify_row(
            _row(name=f"Вакуумні{NBSP}пакети  Siemens", sku="2400042", price="145,00")
        )
        assert verdict.kind is RowKind.DATA


class TestSplitPhotos:
    def test_split_by_comma_semicolon_newline(self):
        raw = "https://a.com/1.jpg, https://b.com/2.jpg;https://c.com/3.jpg\nhttps://d.com/4.jpg"
        assert split_photos(raw) == (
            "https://a.com/1.jpg",
            "https://b.com/2.jpg",
            "https://c.com/3.jpg",
            "https://d.com/4.jpg",
        )

    def test_placeholder_url_dropped(self):
        """Інакше в чергу завантаження летить «URL» Уточнюється і довбиться в DNS."""
        assert split_photos("Уточнюється") == ()
        assert split_photos("") == ()
        assert split_photos("—") == ()

    def test_non_https_dropped(self):
        assert split_photos("http://insecure.com/1.jpg, ftp://x, https://ok.com/2.jpg") == (
            "https://ok.com/2.jpg",
        )


# =============================================================================
# 4. specs.py — характеристики, габарити, гарантія
# =============================================================================


class TestParseSpecTriples:
    def test_real_triples_from_column_j(self):
        """Скрін Screenshot_20260706_224502_Sheets: трійки з колонки J (10-та, 1-based)."""
        row = [
            *["Gorenje GI6401BSC", "Сербія", "", "", "", "", "2400042", "Gorenje", "c50549829"],
            *["Колір виробу", "", "Слонова кістка"],
            *["Бренд", "", "Gorenje"],
            *["Тип варильної панелі", "", "Газова"],
            *["Висота", "мм", "284"],
        ]
        assert parse_spec_triples(row, start_col=10) == [
            Spec("Колір виробу", "", "Слонова кістка"),
            Spec("Бренд", "", "Gorenje"),
            Spec("Тип варильної панелі", "", "Газова"),
            Spec("Висота", "мм", "284"),
        ]

    def test_fixed_columns_are_not_read_as_specs(self):
        """Артикул лежить у G (7-й) — він НЕ має потрапити в характеристики."""
        row = ["Назва", "", "", "", "", "", "2400042", "Gorenje", "c50549829", "Бренд", "", "LG"]
        assert parse_spec_triples(row) == [Spec("Бренд", "", "LG")]

    def test_stops_on_first_empty_name(self):
        """Дефолт зі SYNC.md §6.2."""
        row = [""] * 9 + ["Бренд", "", "LG"] + ["", "", ""] + ["Висота", "мм", "284"]
        assert parse_spec_triples(row) == [Spec("Бренд", "", "LG")]

    def test_can_read_past_the_hole(self):
        """…але дірку можна й перестрибнути, якщо таблиця колись стане дірявою."""
        row = [""] * 9 + ["Бренд", "", "LG"] + ["", "", ""] + ["Висота", "мм", "284"]
        assert parse_spec_triples(row, stop_on_empty_name=False) == [
            Spec("Бренд", "", "LG"),
            Spec("Висота", "мм", "284"),
        ]

    def test_placeholder_value_skipped_but_reading_continues(self):
        row = [""] * 9 + ["Колір", "", "Уточнюється"] + ["Висота", "мм", "284"]
        assert parse_spec_triples(row) == [Spec("Висота", "мм", "284")]

    def test_ragged_row_does_not_crash(self):
        """Sheets обрізає хвіст порожніх клітинок — трійка може бути неповною."""
        assert parse_spec_triples([""] * 9 + ["Бренд", ""]) == []
        assert parse_spec_triples([""] * 9 + ["Бренд"]) == []

    def test_nbsp_in_spec_is_cleaned(self):
        row = [""] * 9 + [f"Колір{NBSP}виробу", "", f"Слонова{NBSP}кістка"]
        assert parse_spec_triples(row) == [Spec("Колір виробу", "", "Слонова кістка")]

    def test_empty_row(self):
        assert parse_spec_triples([]) == []
        assert parse_spec_triples([""] * 20) == []

    def test_start_col_must_be_1_based(self):
        with pytest.raises(ValueError, match="1-based"):
            parse_spec_triples(["a"], start_col=0)


class TestFormatSpec:
    def test_unit_glues_to_value_not_to_name(self):
        """
        КРИТИЧНО (INPUTS.md §2). Замовник просить формат galiton:
        таблиця «назва зліва, значення справа», що читається як «Висота: 284 мм».

        ПРАВИЛЬНО:   ("Висота", "284 мм")
        НЕПРАВИЛЬНО: ("Висота (мм)", "284")  ← так робив скрипт Артура

        Одиниця в назві ще й ламає фільтри: «Висота (мм)» і «Висота (см)» стали б
        ДВОМА різними атрибутами.
        """
        assert format_spec("Висота", "мм", "284") == ("Висота", "284 мм")
        assert format_spec_value("мм", "284") == "284 мм"
        assert format_spec_line("Висота", "мм", "284") == "Висота: 284 мм"

    def test_no_unit(self):
        assert format_spec("Колір виробу", "", "Слонова кістка") == (
            "Колір виробу",
            "Слонова кістка",
        )
        assert format_spec("Бренд", "", "Gorenje") == ("Бренд", "Gorenje")

    def test_placeholder_unit_is_ignored(self):
        assert format_spec("Бренд", "—", "Gorenje") == ("Бренд", "Gorenje")

    def test_unit_not_doubled_if_already_in_value(self):
        assert format_spec("Висота", "мм", "284 мм") == ("Висота", "284 мм")

    def test_nbsp_cleaned(self):
        assert format_spec(f"Висота{NBSP}", "мм", f"284{NBSP}") == ("Висота", "284 мм")


class TestParsePackageDims:
    def test_real_value_from_screen(self):
        """Скрін photo_5372857028075790129: «Габарити упаковки (ВхШхГ) (см): 171,5 x 56 x 35»."""
        assert parse_package_dims("171,5 x 56 x 35") == PackageDims(
            Decimal("171.5"), Decimal("56"), Decimal("35")
        )

    def test_order_is_height_width_depth(self):
        dims = parse_package_dims("171,5 x 56 x 35")
        assert dims.height_cm == Decimal("171.5")
        assert dims.width_cm == Decimal("56")
        assert dims.depth_cm == Decimal("35")

    @pytest.mark.parametrize(
        "raw",
        [
            "171,5 x 56 x 35",
            "171,5x56x35",  # без пробілів
            "171,5 х 56 х 35",  # КИРИЛИЧНА х — візуальний двійник
            "171,5 × 56 × 35",  # знак множення
            "171,5 * 56 * 35",  # зірочка
            "  171,5  x  56  x  35  ",  # зайві пробіли
            "171.5 x 56 x 35",  # десяткова крапка
            "171,5 x 56 x 35 см",  # хвіст одиниць
        ],
    )
    def test_separator_and_whitespace_variants(self, raw):
        assert parse_package_dims(raw) == PackageDims(
            Decimal("171.5"), Decimal("56"), Decimal("35")
        )

    def test_integer_dims(self):
        assert parse_package_dims("60×65×185") == PackageDims(
            Decimal("60"), Decimal("65"), Decimal("185")
        )

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            None,
            "Уточнюється",
            "—",
            "171,5 x 56",  # тільки два числа
            "171,5 x 56 x 35 x 12",  # чотири
            "abc x def x ghi",  # не числа
            "0 x 56 x 35",  # нульовий вимір — не габарит
        ],
    )
    def test_missing_or_broken_returns_none(self, raw):
        assert parse_package_dims(raw) is None


class TestExtractPackageDimsAndWeight:
    #: Точний набір з примітки на скріні photo_5372857028075790129.
    REAL_SPECS = [
        Spec("Висота", "мм", "1635"),
        Spec("Ширина", "мм", "490"),
        Spec("Глибина", "мм", "278"),
        Spec("Габарити упаковки (ВхШхГ)", "см", "171,5 x 56 x 35"),
        Spec("Вага", "кг", "58"),
        Spec("Вага в упаковці", "кг", "61.5"),
        Spec("Країна виробництва", "", "Сербія"),
        Spec("Гарантійний термін", "міс", "12"),
    ]

    def test_takes_package_not_item(self):
        """
        КРИТИЧНО (INPUTS.md §3.4): для НП беремо УПАКОВКУ, а не товар.

        Вага товару 58 кг, вага в упаковці 61.5 кг. Габарити товару 163.5×49×27.8 см,
        габарити упаковки 171.5×56×35 см. Взяти не те — недоплатити за КОЖНУ посилку.
        """
        info = extract_package_dims_and_weight(self.REAL_SPECS)

        assert info.height_cm == Decimal("171.5")  # упаковка, а не 163.5 (=1635 мм)
        assert info.width_cm == Decimal("56")
        assert info.depth_cm == Decimal("35")
        assert info.weight_kg == Decimal("61.5")  # упаковка, а не 58
        assert info.weight_is_fallback is False
        assert info.is_complete is True

    def test_item_dims_alone_are_not_used_as_package_dims(self):
        """Є «Висота (мм)», але немає «Габарити упаковки» → габаритів НЕМАЄ. Не вигадуємо."""
        info = extract_package_dims_and_weight(
            [Spec("Висота", "мм", "1635"), Spec("Ширина", "мм", "490")]
        )
        assert info.has_dims is False
        assert info.height_cm is None

    def test_weight_fallback_is_flagged(self):
        """«Вага в упаковці» немає → беремо «Вага», але ЧЕСНО позначаємо це заниженням."""
        info = extract_package_dims_and_weight(
            [Spec("Габарити упаковки (ВхШхГ)", "см", "171,5 x 56 x 35"), Spec("Вага", "кг", "58")]
        )
        assert info.weight_kg == Decimal("58")
        assert info.weight_is_fallback is True
        assert info.is_complete is True

    def test_alternative_spec_names(self):
        """«Розміри в упаковці (см)» — так називається колонка C у таблиці характеристик."""
        info = extract_package_dims_and_weight(
            [Spec("Розміри в упаковці", "см", "60×65×185"), Spec("Вага в упаковці", "кг", "61,5")]
        )
        assert info.height_cm == Decimal("60")
        assert info.weight_kg == Decimal("61.5")

    def test_unit_suffix_in_weight_value(self):
        info = extract_package_dims_and_weight([Spec("Вага в упаковці", "", "61.5 кг")])
        assert info.weight_kg == Decimal("61.5")

    def test_nothing_found(self):
        info = extract_package_dims_and_weight([Spec("Бренд", "", "Gorenje")])
        assert info.has_dims is False
        assert info.weight_kg is None
        assert info.is_complete is False
        assert info.weight_is_fallback is False

    def test_empty(self):
        assert extract_package_dims_and_weight([]).is_complete is False

    def test_broken_dims_do_not_crash(self):
        info = extract_package_dims_and_weight(
            [Spec("Габарити упаковки (ВхШхГ)", "см", "Уточнюється"), Spec("Вага", "кг", "abc")]
        )
        assert info.has_dims is False
        assert info.weight_kg is None


class TestParseWarranty:
    def test_real_value_from_screen(self):
        """«Гарантійний термін (міс): 12» → 12 (тег <warranty> у фіді Hotline)."""
        assert parse_warranty([Spec("Гарантійний термін", "міс", "12")]) == 12

    def test_months_in_value(self):
        assert parse_warranty([Spec("Гарантійний термін", "", "24 міс")]) == 24

    def test_years_converted_to_months(self):
        assert parse_warranty([Spec("Гарантія", "", "2 роки")]) == 24
        assert parse_warranty([Spec("Гарантія", "рік", "1")]) == 12

    def test_not_found(self):
        assert parse_warranty([Spec("Бренд", "", "Gorenje")]) is None
        assert parse_warranty([]) is None

    def test_non_numeric_warranty_is_none(self):
        """«Гарантія від виробника» — це не число місяців. Тег просто не віддамо."""
        assert parse_warranty([Spec("Гарантія", "", "від виробника")]) is None

    def test_zero_is_none(self):
        assert parse_warranty([Spec("Гарантійний термін", "міс", "0")]) is None

    def test_picked_from_full_real_spec_list(self):
        assert parse_warranty(TestExtractPackageDimsAndWeight.REAL_SPECS) == 12


# =============================================================================
# 5. Наскрізний сценарій
# =============================================================================


def test_end_to_end_price_row_joined_with_spec_sheet():
    """
    Повний шлях: рядок прайсу → класифікація → зіставлення з таблицею характеристик
    за нормалізованою назвою → габарити для калькулятора Нової Пошти.
    """
    price_row = _row(
        name=f"Пральна машина{NBSP}Gorenje GI6401BSC",
        sku="2400042",
        price="27 445,50",
        qty="2,00",
    )

    verdict = classify_row(price_row)
    assert verdict.kind is RowKind.DATA
    assert verdict.price == Decimal("27445.50")
    assert verdict.qty == 2

    # Таблиця характеристик: ключ — нормалізована назва (РЕЗЕРВНИЙ ключ; основний — артикул).
    spec_row = [""] * 9 + [
        "Габарити упаковки (ВхШхГ)", "см", "171,5 x 56 x 35",
        "Вага в упаковці", "кг", "61.5",
        "Гарантійний термін", "міс", "12",
    ]  # fmt: skip
    index = {normalize_model("Gorenje GI6401BSC"): spec_row}

    matched = find_spec_match(price_row["name"], index)
    assert matched is spec_row

    specs = parse_spec_triples(matched)
    info = extract_package_dims_and_weight(specs)

    assert info.is_complete is True  # ← без цього НП не порахує об'ємну вагу
    assert (info.height_cm, info.width_cm, info.depth_cm) == (
        Decimal("171.5"),
        Decimal("56"),
        Decimal("35"),
    )
    assert info.weight_kg == Decimal("61.5")
    assert parse_warranty(specs) == 12

    # І формат рендеру — той, що просить замовник.
    assert format_spec(*(specs[0].name, specs[0].unit, specs[0].value)) == (
        "Габарити упаковки (ВхШхГ)",
        "171,5 x 56 x 35 см",
    )
