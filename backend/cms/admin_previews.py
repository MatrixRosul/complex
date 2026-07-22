"""
Схема-прев'ю банера в адмінці: «де саме на сайті опиниться ця картинка».

⚠️ НАВІЩО ЦЕ ІСНУЄ. Замовник обирав `placement` зі списку назв і не міг зрозуміти, що за
що відповідає («плутанина виходить трошки»): назви пунктів описують НАМІР, а не результат.
Гірше — два з чотирьох розміщень поводяться не так, як звучать:

  • «Слайдер головної» — слайдера на сайті НЕМАЄ. Значення лишилось як запасне: банер
    з ним потрапить у промо-слот, тільки якщо не заведено жодного «Промо-блоку».
  • «Банер над категорією» — фронт це значення НЕ ЧИТАЄ ВЗАГАЛІ (перевірено grep-ом по
    frontend/src). Банер збережеться, буде «активним» — і не покажеться ніде й ніколи.

Тому прев'ю не косметичне: воно єдине місце, де ця розбіжність видно ДО того, як людина
завантажить картинку й чекатиме її на сайті. Коли фронт навчиться цих розміщень —
правити тут `SUPPORTED`, і попередження зникне саме.

Схема свідомо намальована HTML+inline-CSS, без картинок і JS: вона їде всередину
readonly-поля Unfold, а inline-стилі переживають будь-яку тему адмінки.
"""

from __future__ import annotations

from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString, mark_safe

# Синій бренду — той самий #15558F, що й акцент сайту (docs/research/DESIGN_SYSTEM.md §2.1).
BRAND = "#15558f"
MUTED_BORDER = "rgba(128,128,128,.45)"
MUTED_FILL = "rgba(128,128,128,.12)"
WARN = "#b45309"

# Розміщення, які фронт СПРАВДІ виводить. Решта — збережеться, але не покажеться.
SUPPORTED = {"home_promo", "home_side", "home_slider"}

_HINTS: dict[str, str] = {
    "home_promo": (
        "Широкий банер праворуч від каталогу, поки каталог ЗАКРИТИЙ — те, що бачить "
        "людина одразу при вході. Пропорція приблизно 3:2 (наприклад 1200×800)."
    ),
    "home_side": (
        "Вузька вертикальна смуга праворуч від підгруп — видно, поки каталог ВІДКРИТИЙ "
        "(навели на категорію або натиснули «Каталог»). Пропорція вертикальна, "
        "приблизно 1:1.5 (наприклад 600×900)."
    ),
    "home_slider": (
        "⚠️ Слайдера на сайті немає. Цей банер потрапить у той самий широкий слот, що й "
        "«Промо-блок головної», але ТІЛЬКИ якщо жодного «Промо-блоку» не заведено. "
        "Хочете передбачуваний результат — оберіть «Промо-блок головної»."
    ),
    "category_top": (
        "⚠️ Це розміщення сайт поки НЕ ВИВОДИТЬ. Банер збережеться і буде «активним», "
        "але на сторінці категорії не з'явиться. Поки що використовуйте «Промо-блок "
        "головної» або «Рекламу праворуч від каталогу»."
    ),
}


def _box(
    label: str,
    *,
    active: bool,
    image_url: str = "",
    flex: str = "1",
) -> str:
    """Один прямокутник макета. Активний — синій, з картинкою банера всередині."""
    if active and image_url:
        inner = format_html(
            '<img src="{}" alt="" style="max-width:100%;max-height:100%;'
            'object-fit:contain;border-radius:3px" />',
            image_url,
        )
    else:
        inner = format_html('<span style="opacity:.85">{}</span>', label)

    return format_html(
        '<div style="flex:{};display:flex;align-items:center;'
        "justify-content:center;text-align:center;padding:6px;border-radius:6px;"
        'font-size:11px;line-height:1.25;border:{};background:{};color:{}">{}</div>',
        flex,
        (f"2px solid {BRAND}" if active else f"1px dashed {MUTED_BORDER}"),
        "rgba(21,85,143,.10)" if active else MUTED_FILL,
        BRAND if active else "inherit",
        inner,
    )


def _catalog_column() -> SafeString:
    """Ліва колонка зі списком категорій — вона є в обох станах."""
    rows = format_html_join(
        "",
        '<div style="height:7px;margin:4px 6px;border-radius:3px;background:{}"></div>',
        ((MUTED_BORDER,) for _ in range(5)),
    )
    return format_html(
        '<div style="width:74px;flex:none;border:1px dashed {};border-radius:6px;'
        'background:{};padding:5px 0">'
        '<div style="font-size:9px;text-align:center;opacity:.7;margin-bottom:3px">Каталог</div>'
        "{}</div>",
        MUTED_BORDER,
        MUTED_FILL,
        rows,
    )


def _mockup(caption: str, body: SafeString) -> SafeString:
    """Один макет сторінки з підписом."""
    return format_html(
        '<div style="flex:1;min-width:250px">'
        '<div style="font-size:11px;font-weight:600;margin-bottom:5px;opacity:.75">{}</div>'
        '<div style="border:1px solid {};border-radius:8px;padding:7px;background:rgba(128,128,128,.04)">'
        '<div style="height:9px;border-radius:3px;background:{};margin-bottom:7px"></div>'
        # Фіксована висота ряду: інакше картинка розтягує «свій» макет, і два стани
        # перестають бути порівнюваними — саме те, заради чого схема й малюється.
        '<div style="display:flex;gap:6px;align-items:stretch;height:120px">{}</div>'
        "</div></div>",
        caption,
        MUTED_BORDER,
        BRAND,
        body,
    )


def _variant(placement: str, image_url: str) -> SafeString:
    """Схема для ОДНОГО розміщення: два стани головної + підказка під ними."""
    is_promo_slot = placement in {"home_promo", "home_slider"}

    closed = _mockup(
        "Каталог закритий (як бачать при вході)",
        format_html(
            "{}{}",
            _catalog_column(),
            _box("широкий банер", active=is_promo_slot, image_url=image_url),
        ),
    )
    opened = _mockup(
        "Каталог відкритий (навели / натиснули «Каталог»)",
        format_html(
            "{}{}{}",
            _catalog_column(),
            _box("підгрупи категорії", active=False, flex="2"),
            _box(
                "вузька",
                active=placement == "home_side",
                image_url=image_url,
                flex="1",
            ),
        ),
    )

    hint = _HINTS.get(placement, "")
    unsupported = placement and placement not in SUPPORTED

    note = (
        format_html(
            '<div style="margin-top:10px;padding:8px 10px;border-radius:6px;'
            'border-left:3px solid {};background:rgba(180,83,9,.10);font-size:12px">{}</div>',
            WARN,
            hint,
        )
        if unsupported
        else format_html('<div style="margin-top:10px;font-size:12px;opacity:.8">{}</div>', hint)
    )

    # Статичний рядок без підстановок — mark_safe, бо format_html() без args застарів.
    empty_note = mark_safe(  # noqa: S308 — літерал, зовнішніх даних тут немає
        ""
        if image_url
        else '<div style="margin-top:6px;font-size:11px;opacity:.6">'
        "Завантажте зображення на вкладці <b>uk</b> — і воно з'явиться просто тут, "
        "на своєму місці в макеті.</div>"
    )

    return format_html(
        '<div style="display:flex;gap:14px;flex-wrap:wrap;max-width:760px">{}{}</div>{}{}',
        closed,
        opened,
        note,
        empty_note,
    )


# Перемикання схеми ПРЯМО ПРИ ВИБОРІ в списку, без збереження.
# ⚠️ Без цього прев'ю оновлювалось би лише після «Зберегти» — тобто рівно тоді, коли вже
# пізно: людина обирає розміщення НАОСЛІП, а саме це й треба було прибрати. Тому сервер
# віддає схеми для ВСІХ розміщень одразу, а скрипт показує ту, що відповідає вибору.
# Слухаємо і 'change', і 'click' по опціях: Unfold підміняє нативний <select> своїм
# віджетом, і подія 'change' на прихованому select приходить не в усіх темах.
_SWITCH_JS = """
<script>
(function () {
  function bind() {
    var root = document.getElementById('banner-layout-preview');
    if (!root) return;
    var select = document.querySelector('select[name="placement"]');
    if (!select) return;
    function sync() {
      var value = select.value || '';
      root.querySelectorAll('[data-bp]').forEach(function (el) {
        el.style.display = el.getAttribute('data-bp') === value ? '' : 'none';
      });
    }
    select.addEventListener('change', sync);
    document.addEventListener('click', function () { setTimeout(sync, 0); });
    sync();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
})();
</script>
"""


def layout_preview(placement: str, image_url: str = "") -> SafeString:
    """Схеми для всіх розміщень; видима — та, що обрана зараз (і міняється на льоту)."""
    from cms.models import Banner

    values = list(Banner.Placement.values)
    current = placement if placement in values else (values[0] if values else "")

    blocks = format_html_join(
        "",
        '<div data-bp="{}" style="display:{}">{}</div>',
        (
            (value, "" if value == current else "none", _variant(value, image_url))
            for value in values
        ),
    )
    return format_html(
        '<div id="banner-layout-preview">{}</div>{}',
        blocks,
        mark_safe(_SWITCH_JS),  # noqa: S308 — статичний літерал, без зовнішніх даних
    )


# Підпис для колонки списку. Свідомо НЕ «виводиться / не виводиться»: така пара брехала б
# на «Слайдері головної» (він показується лише за відсутності «Промо-блоку») і нічого не
# казала б про те, що в кожному слоті виграє РІВНО ОДИН банер — перший за «Порядком».
SLOT_LABELS: dict[str, str] = {
    "home_promo": "Широкий слот",
    "home_side": "Вузький слот",
    "home_slider": "Запасний",
    "category_top": "Не виводиться",
}

# value → колір бейджа Unfold.
SLOT_COLORS: dict[str, str] = {
    "Широкий слот": "success",
    "Вузький слот": "success",
    "Запасний": "warning",
    "Не виводиться": "danger",
}


def placement_badge(placement: str) -> str:
    """Куди саме потрапляє банер цього розміщення (див. SLOT_LABELS)."""
    return SLOT_LABELS.get(placement, "Не виводиться")
