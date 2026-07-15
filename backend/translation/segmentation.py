"""
Сегментація і ВАЛІДАТОРИ — детерміністична частина перекладу.

Тут немає жодного виклику Claude. Це та частина, яка робить гарантії, а не сподівається
на промпт (TRANSLATION.md §3, §4).

╔══════════════════════════════════════════════════════════════════════════════════════╗
║ ТРИ РІВНІ ЗАХИСТУ БРЕНДА Й КОДУ МОДЕЛІ                                                ║
║   Рівень 1 (тут): split_name() — бренд і артикул ФІЗИЧНО не потрапляють у запит.       ║
║                   «Варильна поверхня газова Bosch PNK6B2P40R»                          ║
║                   → у модель їде тільки «Варильна поверхня газова».                    ║
║                   Те, чого модель не бачить, вона не може зіпсувати.                   ║
║   Рівень 2 (prompts.py): правило в промпті — на випадок, коли бренд у назві не         ║
║                   знайшовся («Запчастина для духовки TEKA 83340602»).                  ║
║   Рівень 3 (тут): validate_preserved() — жорстка постперевірка. Не збіглося → FAILED,  ║
║                   жоден невалідований переклад не потрапляє навіть у чергу схвалення.  ║
╚══════════════════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════════════════╗
║ HTML: РОЗМІТКА НЕ ПРОХОДИТЬ ЧЕРЕЗ МОДЕЛЬ УЗАГАЛІ                                      ║
║ Ми не віддаємо HTML у Claude «як є» — це лотерея: модель «покращить» верстку або       ║
║ перепише <img src="https://r2...">. Замість цього:                                    ║
║   HTML → lxml DOM → для кожного БЛОКОВОГО елемента беремо inner-текст, інлайнові теги  ║
║   кодуємо як <0>…</0>, <1/> → у модель їде ЛИШЕ текст → назад підставляємо в ТОЙ САМИЙ ║
║   DOM (заміняємо текстові вузли) → serialize.                                          ║
║ Наслідок by construction: src/href/class/style, порядок вузлів і вкладеність —         ║
║ біт-у-біт ті самі. Модель фізично не бачить URL → не може їх «поправити».              ║
╚══════════════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from itertools import count

from lxml import etree
from lxml import html as lxml_html

# ---------------------------------------------------------------------------
# Токени, які НІКОЛИ не перекладаються
# ---------------------------------------------------------------------------

# Код моделі = токен, що містить ОДНОЧАСНО латинські літери та цифри: PNK6B2P40R, HZ66D910.
MODEL_CODE_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9\-/.]+$")

# Усе латинсько-цифрове, що має зберегтись у перекладі один-в-один: бренди, коди, числа,
# класи (A+++, IPX4), стандарти (Wi-Fi, USB, Type-C).
LATIN_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-/.+]*")

# Плейсхолдери інлайнових тегів: <0>…</0>, <3/>
PLACEHOLDER_RE = re.compile(r"</?(\d+)/?>")

CYRILLIC_RE = re.compile(r"[а-щьюяєіїґА-ЩЬЮЯЄІЇҐ]", re.IGNORECASE)


def has_cyrillic(text: str) -> bool:
    return bool(CYRILLIC_RE.search(text or ""))


def is_translatable(text: str) -> bool:
    """Чи є що перекладати взагалі.

    ~35% значень характеристик — чисті числа («284»), бренди («Gorenje»), коди («A+++»).
    Гнати їх у модель — платити за те, щоб вона повернула їх без змін. Не женемо.
    """
    text = (text or "").strip()
    return bool(text) and has_cyrillic(text)


def split_name(name: str, brand: str | None = None) -> tuple[str, str]:
    """«Варильна поверхня газова Bosch PNK6B2P40R» → («Варильна поверхня газова»,
    «Bosch PNK6B2P40R»).

    Відрізаємо З КІНЦЯ бренд і токени-коди моделі. У Claude їде лише описова частина.
    Збірка назад: f"{ru_head} {tail}".strip().
    """
    tokens = (name or "").split()
    brand_cf = (brand or "").casefold()
    tail: list[str] = []
    while tokens:
        last = tokens[-1]
        is_brand = bool(brand_cf) and last.casefold() == brand_cf
        if is_brand or MODEL_CODE_RE.fullmatch(last):
            tail.insert(0, tokens.pop())
            continue
        break
    return " ".join(tokens), " ".join(tail)


def join_name(head_ru: str, tail: str) -> str:
    return f"{head_ru.strip()} {tail.strip()}".strip()


def validate_preserved(src: str, dst: str) -> bool:
    """Мультимножина латинсько-цифрових токенів мусить збігтися один-в-один.

    Це і є той рівень, на якому «Bosch» не може стати «Бош», а «PNK6B2P40R» — «PNK6B2P4OR».
    """
    return Counter(LATIN_TOKEN_RE.findall(src or "")) == Counter(LATIN_TOKEN_RE.findall(dst or ""))


def placeholders(text: str) -> Counter[str]:
    return Counter(PLACEHOLDER_RE.findall(text or ""))


# Одиниці виміру: число+одиниця мусить зберегтись, з урахуванням перекладу самої одиниці.
UNIT_MAP = {
    "міс": "мес",
    "год": "ч",
    "хв": "мин",
    "доба": "сутки",
    "об/хв": "об/мин",
    "л/хв": "л/мин",
    "кВт·год": "кВт·ч",
}
_UNIT_ALT = "|".join(
    sorted(
        (
            re.escape(u)
            for u in [
                *UNIT_MAP,
                *UNIT_MAP.values(),
                "мм",
                "см",
                "м",
                "кг",
                "г",
                "л",
                "мл",
                "Вт",
                "кВт",
                "В",
                "А",
                "Гц",
                "дБ",
                "Па",
                "°C",
                "шт",
            ]
        ),
        key=len,
        reverse=True,
    )
)
NUM_UNIT_RE = re.compile(rf"(\d+(?:[.,]\d+)?)\s*({_UNIT_ALT})\b")


def _num_units(text: str) -> Counter[tuple[str, str]]:
    out: Counter[tuple[str, str]] = Counter()
    for num, unit in NUM_UNIT_RE.findall(text or ""):
        out[(num, UNIT_MAP.get(unit, unit))] += 1  # нормалізуємо одиницю до RU-форми
    return out


def validate_numbers(src: str, dst: str) -> bool:
    """«12 міс» → «12 мес» ок; «12 міс» → «12 мес» з іншим числом — не ок."""
    return _num_units(src) == _num_units(dst)


# ---------------------------------------------------------------------------
# HTML → сегменти → HTML
# ---------------------------------------------------------------------------

BLOCK_TAGS = frozenset(
    {
        "p",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "td",
        "th",
        "figcaption",
        "blockquote",
        "dt",
        "dd",
        "caption",
    }
)
# Перекладні атрибути (окремі сегменти — модель не бачить решти тега).
TRANSLATABLE_ATTRS = {"img": ("alt", "title"), "a": ("title",)}


@dataclass
class Segment:
    id: str
    uk: str


@dataclass
class HtmlDocument:
    """Розібраний опис: DOM + карта, куди підставляти переклад."""

    root: etree._Element
    segments: list[Segment] = field(default_factory=list)
    # id сегмента → (елемент, store плейсхолдерів) для inner-html
    _blocks: dict[str, tuple[etree._Element, dict[int, etree._Element]]] = field(
        default_factory=dict
    )
    # id сегмента → (елемент, назва атрибута)
    _attrs: dict[str, tuple[etree._Element, str]] = field(default_factory=dict)


def _encode_inner(el: etree._Element, counter, store: dict[int, etree._Element]) -> str:
    """Inner-HTML елемента → текст із плейсхолдерами <0>…</0> / <3/>."""
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        if isinstance(child, etree._Comment):
            continue
        idx = next(counter)
        store[idx] = child
        inner = _encode_inner(child, counter, store)
        if inner:
            parts.append(f"<{idx}>{inner}</{idx}>")
        else:
            parts.append(f"<{idx}/>")  # <br>, <img> — порожні інлайни
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _decode_inner(el: etree._Element, text: str, store: dict[int, etree._Element]) -> None:
    """Текст із плейсхолдерами → діти елемента. ТЕГИ БЕРУТЬСЯ ЗІ STORE, не з тексту моделі.

    Саме тут HTML стає невразливим: модель могла написати що завгодно всередині <0>…</0>,
    але сам тег, його ім'я й АТРИБУТИ (src, href, class) ми беремо з оригінального DOM.
    """
    for child in list(el):
        el.remove(child)
    el.text = None

    def append_text(cur: etree._Element, txt: str) -> None:
        if not txt:
            return
        if len(cur) == 0:
            cur.text = (cur.text or "") + txt
        else:
            last = cur[-1]
            last.tail = (last.tail or "") + txt

    stack: list[etree._Element] = [el]
    pos = 0
    for m in PLACEHOLDER_RE.finditer(text):
        append_text(stack[-1], text[pos : m.start()])
        pos = m.end()
        raw = m.group(0)
        idx = int(m.group(1))
        if raw.startswith("</"):
            if len(stack) > 1:
                stack.pop()
            continue
        src = store.get(idx)
        if src is None:
            continue  # невідомий плейсхолдер — валідатор уже позначив це помилкою
        new = etree.SubElement(stack[-1], src.tag)
        for k, v in src.attrib.items():
            new.set(k, v)  # src/href/class/style — один-в-один з оригіналу
        if not raw.endswith("/>"):
            stack.append(new)
    append_text(stack[-1], text[pos:])


def parse_html(html: str) -> HtmlDocument:
    """HTML → DOM + сегменти. Сегменти — це ТІЛЬКИ текст, ніякої розмітки."""
    root = lxml_html.fragment_fromstring(html or "", create_parent="div")
    doc = HtmlDocument(root=root)
    n = count()

    for el in root.iter():
        if isinstance(el, etree._Comment):
            continue
        tag = el.tag if isinstance(el.tag, str) else ""

        # 1) перекладні атрибути (img@alt, img@title, a@title) — окремими сегментами
        for attr in TRANSLATABLE_ATTRS.get(tag, ()):
            val = (el.get(attr) or "").strip()
            if is_translatable(val):
                sid = f"a{next(n)}"
                doc.segments.append(Segment(id=sid, uk=val))
                doc._attrs[sid] = (el, attr)

        # 2) блокові елементи — лише ЛИСТКОВІ (без вкладених блоків), інакше подвоїмо текст
        if tag not in BLOCK_TAGS:
            continue
        if any(isinstance(d.tag, str) and d.tag in BLOCK_TAGS for d in el.iterdescendants()):
            continue

        store: dict[int, etree._Element] = {}
        inner = _encode_inner(el, count(), store)
        if not is_translatable(PLACEHOLDER_RE.sub("", inner)):
            continue  # самі числа/плейсхолдери — нема чого перекладати
        sid = f"s{next(n)}"
        doc.segments.append(Segment(id=sid, uk=inner))
        doc._blocks[sid] = (el, store)

    return doc


def apply_html(doc: HtmlDocument, translations: dict[str, str]) -> str:
    """Підставляємо переклад у ТОЙ САМИЙ DOM і серіалізуємо."""
    for sid, (el, store) in doc._blocks.items():
        if sid in translations:
            _decode_inner(el, translations[sid], store)
    for sid, (el, attr) in doc._attrs.items():
        if sid in translations:
            el.set(attr, translations[sid])

    return "".join(
        [doc.root.text or ""] + [lxml_html.tostring(c, encoding="unicode") for c in doc.root]
    ).strip()


def _tag_multiset(html: str) -> Counter[str]:
    root = lxml_html.fragment_fromstring(html or "", create_parent="div")
    return Counter(el.tag for el in root.iter() if isinstance(el.tag, str) and el is not root)


def _urls(html: str) -> Counter[str]:
    root = lxml_html.fragment_fromstring(html or "", create_parent="div")
    out: Counter[str] = Counter()
    for el in root.iter():
        for attr in ("src", "href", "srcset", "poster"):
            v = el.get(attr) if isinstance(el.tag, str) else None
            if v:
                out[v] += 1
    return out


def validate_html_translation(
    src_html: str, dst_html: str, segments_in: list[Segment], segments_out: dict[str, str]
) -> list[str]:
    """Усі валідатори з TRANSLATION.md §4. Непорожній список → status=FAILED + ретрай."""
    errs: list[str] = []

    if len(segments_in) != len(segments_out):
        errs.append("segment_count")

    for seg in segments_in:
        ru = segments_out.get(seg.id)
        if ru is None:
            errs.append(f"missing:{seg.id}")
            continue
        if placeholders(seg.uk) != placeholders(ru):
            errs.append(f"inline_tags:{seg.id}")  # набір <0>…</0> не збігся
        if not validate_preserved(seg.uk, ru):
            errs.append(f"latin_tokens:{seg.id}")  # бренди / коди / числа
        if not validate_numbers(seg.uk, ru):
            errs.append(f"numbers:{seg.id}")

    if _tag_multiset(src_html) != _tag_multiset(dst_html):
        errs.append("tag_multiset")
    if _urls(src_html) != _urls(dst_html):
        errs.append("urls")  # <img src="https://r2..."> зник або змінився

    return errs


def validate_plain_translation(src: str, dst: str) -> list[str]:
    """Валідатори для НЕ-HTML сегментів (назви, значення, SEO)."""
    errs: list[str] = []
    if not (dst or "").strip():
        errs.append("empty")
        return errs
    if not validate_preserved(src, dst):
        errs.append("latin_tokens")
    if not validate_numbers(src, dst):
        errs.append("numbers")
    return errs
