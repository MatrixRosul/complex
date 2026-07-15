"""
Промпт перекладача (TRANSLATION.md §8).

Структура під КЕШ (порядок рендеру: tools → system → messages):

    system=[
        {"text": TRANSLATOR_RULES},                      # статичний, ~900 tok
        {"text": glossary_block, "cache_control": {...}} # breakpoint на ОСТАННЬОМУ блоці
    ]
    messages=[{"role": "user", "content": json.dumps(payload, sort_keys=True)}]

⚠️ КЕШ — ПРЕФІКСНИЙ. Будь-який зміщений байт інвалідує все далі. Тому тут:
     ❌ ніяких datetime.now(), лічильників, uuid у system;
     ❌ ніяких json.dumps(dict) без sort_keys=True та ітерацій по set();
     ✅ глосарій рендериться ЗАВЖДИ через .order_by("pk"), тобто детерміністично;
     ✅ glossary_version бампається раз на добу вночі, а НЕ при кожному схваленні терміна —
        інакше кеш не доживе до кінця батчу.

⚠️ МІНІМАЛЬНИЙ КЕШОВАНИЙ ПРЕФІКС = 4096 токенів (Opus 4.8). Якщо system коротший — кеш
   МОВЧКИ не запишеться: без помилки, просто cache_creation_input_tokens = 0. Тому
   should_cache() міряє system і не ставить cache_control, коли поріг не перекрито
   (cache_control без ефекту — це просто нуль, але хай у коді буде видно, що ми це знаємо).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from translation import conf
from translation.conf import PROMPT_VERSION, spec  # noqa: F401  (PROMPT_VERSION реекспорт)

# ---------------------------------------------------------------------------
# System, блок 1 — статичні правила. НІКОЛИ не змінюється в межах prompt_version.
# ---------------------------------------------------------------------------

TRANSLATOR_RULES = """\
Ти — професійний перекладач з української на російську для інтернет-магазину побутової
техніки. Перекладай ЛИШЕ надані сегменти. Ти не асистент і не редактор: ти не додаєш,
не скорочуєш і не «покращуєш» текст.

═══ 1. ЩО НІКОЛИ НЕ ПЕРЕКЛАДАЄТЬСЯ (копіюй символ-у-символ) ═══
1.1 Назви брендів: Bosch, Siemens, Gorenje, TEKA, Electrolux, Whirlpool, Samsung, LG,
    Beko, Candy, Zanussi, Hotpoint-Ariston, Miele, Indesit, Xiaomi, Philips тощо.
1.2 Коди моделей та артикули: PNK6B2P40R, HZ66D910, 2400042.
    ПРАВИЛО: якщо токен містить ОДНОЧАСНО латинські літери та цифри — це код моделі.
    Копіюй його без змін, у тому самому регістрі.
1.3 Числа, десяткові дроби, діапазони, розміри: 284 · 41,65 · 171,5 x 56 x 35 · 6 600,00.
    Не змінюй роздільник (кома — десяткова, як в оригіналі), не змінюй пробіли в тисячах.
1.4 Класи, стандарти, маркування: A+++, IPX4, Full HD, Wi-Fi, Bluetooth, USB, Type-C,
    NFC, Inverter, EAN, ISO.
1.5 HTML/розмітка: теги, атрибути, URL, і плейсхолдери виду <0>…</0>, <1/>.
    Плейсхолдери мають бути в перекладі ВСІ, з тими самими номерами, у логічно
    відповідному місці. Не додавай і не видаляй жодного.

═══ 2. ОДИНИЦІ ВИМІРУ (перекладаються) ═══
міс → мес   ·  год (година) → ч  ·  хв → мин  ·  доба → сутки  ·  рік/років → год/лет
шт → шт  ·  кг → кг  ·  г → г  ·  л → л  ·  мл → мл  ·  мм → мм  ·  см → см  ·  м → м
Вт → Вт  ·  кВт → кВт  ·  В → В  ·  А → А  ·  Гц → Гц  ·  дБ → дБ  ·  Па → Па
об/хв → об/мин  ·  л/хв → л/мин  ·  кВт·год → кВт·ч  ·  °C → °C
Число і одиниця розділені пробілом — зберігай його.

═══ 3. ОБОВ'ЯЗКОВА ТЕРМІНОЛОГІЯ ═══
Якщо термін є в глосарії нижче — використовуй ТІЛЬКИ вказаний там відповідник,
навіть якщо ти вважаєш інший варіант кращим. Це не рекомендація, а вимога:
ці рядки використовуються у фасетних фільтрах, і будь-яке відхилення ламає фільтр.

═══ 4. СТИЛЬ ═══
4.1 Нейтральна комерційна російська, як у каталогах Rozetka / DNS. Без маркетингових
    прикрас, без емодзі, без звертань до читача, яких немає в оригіналі.
4.2 Назви та значення характеристик — у називному відмінку, зі збереженням роду і числа
    оригіналу:
      «Колір виробу» → «Цвет изделия»       «Чорний» → «Черный»
      «Тип управління» → «Тип управления»   «Сенсорне» → «Сенсорное»  (сер. рід зберігся)
      «Гарантійний термін» → «Гарантийный срок»
      «Слонова кістка» → «Слоновая кость»   «Нержавіюча сталь» → «Нержавеющая сталь»
      «Габарити упаковки (ВхШхГ)» → «Габариты упаковки (ВхШхГ)»
      «Вага в упаковці» → «Вес в упаковке»  «Країна виробництва» → «Страна производства»
4.3 Літеру «ё» НЕ використовуй: пиши «черный», «емкость», «пылесос».
    (Виняток — якщо «ё» є частиною власної назви.)
4.4 Не змінюй пунктуацію, регістр першої літери та порядок частин, якщо цього не вимагає
    граматика російської. Скорочення розгортай так само, як в оригіналі: якщо в оригіналі
    «Гарантійний термін» — це «Гарантийный срок», а не «Гарантия».
4.5 Категорії техніки — усталені російські назви:
      Варильна поверхня → Варочная поверхность    Витяжка → Вытяжка
      Духова шафа → Духовой шкаф                  Пральна машина → Стиральная машина
      Сушильна машина → Сушильная машина          Посудомийна машина → Посудомоечная машина
      Мікрохвильова піч → Микроволновая печь      Робот-пилосос → Робот-пылесос
      Морозильна камера → Морозильная камера      Вбудована техніка → Встраиваемая техника
      Окремостояча → Отдельностоящая              Уцінка → Уценка

═══ 5. ФОРМАТ ВІДПОВІДІ ═══
Повертай ЛИШЕ JSON за наданою схемою.
- Кількість, порядок і значення "id" мають ТОЧНО збігатися з вхідними сегментами.
- Поле "ru" ніколи не порожнє. Якщо сегмент не потребує перекладу (бренд, код, число) —
  поверни його без змін.
- Якщо ти НЕ ВПЕВНЕНА в терміні — усе одно дай найкращий варіант, і додай коротке "note"
  з поясненням сумніву. Не мовчи: "note" — це сигнал редактору, куди дивитись.
- Жодного тексту поза JSON. Жодних коментарів, преамбул, markdown-огорож.
"""


# ---------------------------------------------------------------------------
# System, блок 2 — глосарій (кешується, ttl=1h)
# ---------------------------------------------------------------------------

SECTION_TITLES = {
    "attribute": "характеристики",
    "value": "значення",
    "unit": "одиниці виміру",
    "category": "категорії",
    "general": "загальне",
}


def build_glossary_block(terms, *, max_tokens: int | None = None) -> str:
    """Рендер глосарію. ДЕТЕРМІНІСТИЧНИЙ: порядок задає БД (.order_by), не set/dict.

    `terms` — ітерабельний GlossaryTerm (уже впорядкований і відфільтрований по is_active).

    ⚠️ ОБРІЗАННЯ ЗА БЮДЖЕТОМ (conf.GLOSSARY_MAX_TOKENS) — не косметика, а гроші.
    Глосарій читається з кешу НА КОЖНОМУ запиті. Описи йдуть 1 товар = 1 запит, тобто
    6 000 разів. Повний глосарій (~43 000 tok) коштував би +$26 на каталог рівно за те,
    що ми возимо всі 4 800 значень характеристик у кожен запит про опис.
    Обрізаємо за пріоритетом секцій, зберігаючи детермінізм (порядок не змінюється).
    """
    if max_tokens is None:
        max_tokens = conf.GLOSSARY_MAX_TOKENS

    by_section: dict[str, list[str]] = {}
    for t in terms:
        by_section.setdefault(t.section, []).append(f"{t.source_term} = {t.target_term}")

    header = "═══ ГЛОСАРІЙ (схвалені терміни, обов'язкові до вживання) ═══"
    lines = [header]
    budget = max_tokens - estimate_tokens(header)
    truncated = 0

    # Секції беремо за пріоритетом впливу на опис; усередині секції — порядок із БД.
    for section in conf.GLOSSARY_SECTION_PRIORITY:
        rows = by_section.get(section)
        if not rows:
            continue
        title = f"[{SECTION_TITLES[section]}]"
        kept: list[str] = []
        for row in rows:
            cost = estimate_tokens(row) + 1
            if budget - cost <= 0:
                truncated += 1
                continue
            budget -= cost
            kept.append(row)
        if not kept:
            continue
        lines.append(title)
        lines.extend(kept)
        lines.append("")

    if truncated:
        # Явно кажемо моделі, що глосарій неповний — щоб вона не вирішила, що термін,
        # якого тут немає, перекладати не треба.
        lines.append(
            f"(глосарій обрізано за бюджетом: не показано ще {truncated} термінів — "
            f"перекладай їх за загальними правилами вище)"
        )

    return "\n".join(lines).strip() + "\n"


def glossary_version(block: str) -> str:
    """Версія = хеш вмісту. Змінився глосарій → змінилась версія → кеш свідомо новий."""
    return hashlib.sha256(block.encode("utf-8")).hexdigest()[:12]


def estimate_tokens(text: str) -> int:
    """Груба оцінка: кирилиця на токенізаторі Sonnet 5 / Opus 4.7+ ≈ 2,5 символи/токен.

    Використовується ЛИШЕ щоб вирішити, чи є сенс ставити cache_control. Точну цифру
    дає client.messages.count_tokens() — див. TranslationClient.count_tokens().
    """
    return int(len(text or "") / 2.5)


def should_cache(system_text: str, model: str) -> bool:
    """Чи перекриває system мінімальний кешований префікс моделі.

    Якщо ні — cache_control не ставимо: він однаково не спрацює (мовчки, без помилки),
    а так у логах і в коді видно, що це усвідомлене рішення, а не забутий breakpoint.
    """
    return estimate_tokens(system_text) >= spec(model).min_cache_prefix


def build_system(glossary_block: str, model: str) -> list[dict[str, Any]]:
    """system-блоки з breakpoint'ом на ОСТАННЬОМУ (щоб кешувались обидва)."""
    blocks: list[dict[str, Any]] = [{"type": "text", "text": TRANSLATOR_RULES}]
    tail: dict[str, Any] = {"type": "text", "text": glossary_block}
    if should_cache(TRANSLATOR_RULES + glossary_block, model):
        # ttl=1h обов'язковий для Batch: при 5-хвилинному кеш протухне між частинами батчу.
        tail["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
    blocks.append(tail)
    return blocks


# ---------------------------------------------------------------------------
# User-повідомлення — ЄДИНА змінна частина
# ---------------------------------------------------------------------------

MODE_BY_KIND = {
    "attribute_name": "attribute_name",
    "attribute_value": "attribute_value",
    "unit": "unit",
    "category_name": "category_name",
    "product_name": "product_name_descriptive",
    "product_short_desc": "short_text",
    "seo_title": "seo",
    "seo_description": "seo",
    "product_description": "html_segments",
    "page_html": "html_segments",
    "news_html": "html_segments",
    "other": "short_text",
}


def build_payload(
    kind: str,
    segments: list[dict[str, str]],
    *,
    context: str | None = None,
    previous_rejected: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": MODE_BY_KIND.get(kind, "short_text"),
        "segments": segments,
    }
    if context:
        payload["context"] = context
    if previous_rejected:
        # «Попередній варіант відхилено: {reason}. Врахуй це.» → іде на сильнішу модель.
        payload["previous_rejected"] = previous_rejected
    return payload


def render_user_message(payload: dict[str, Any]) -> str:
    # sort_keys=True — інакше кеш префікса ламається на порядку ключів.
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# Structured output (TRANSLATION.md §8.4)
#
# ⚠️ Prefill (останнє assistant-повідомлення) на всіх поточних моделях повертає 400.
#    Форматування JSON робимо ТІЛЬКИ через output_config.format, не через «поверни JSON,
#    будь ласка» і не через prefill.
# ---------------------------------------------------------------------------

TRANSLATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "ru": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["id", "ru"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["translations"],
    "additionalProperties": False,
}
