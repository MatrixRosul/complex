"""Прев'ю банера в адмінці: «як це буде на сайті НАСПРАВДІ».

⚠️ НАВІЩО ЦЕ ІСНУЄ. Замовник заводив банер наосліп: назви розміщень описували намір,
а не результат, кадр обирався пресетами, і не було видно ні скільки банерів влізе,
ні як картинка обріжеться. Тому тут не декоративна картинка, а три речі:

  1. РЕАЛЬНИЙ РЯД. Малюється справжній слот із УСІМА живими банерами цього розміщення,
     а поточний підсвічений. Одразу видно: скільки їх, котрий по порядку цей, що
     влізає одночасно, а що піде на наступне перегортання.
  2. ЧЕСНЕ КАДРУВАННЯ. `object-fit: cover` + той самий `object-position`, що на сайті.
     Раніше прев'ю показувало картинку цілком (`contain`) — тобто те, чого не буває.
  3. КАДР МИШЕЮ. Клік по картинці ставить точку фокуса; повзунок — наближення. Обидва
     оновлюють поля форми й саме прев'ю миттєво, без збереження.

Схема — HTML+inline-CSS без зовнішніх файлів: вона їде всередину readonly-поля Unfold,
а inline-стилі переживають будь-яку тему адмінки.
"""

from __future__ import annotations

from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString, mark_safe

# Синій бренду — той самий #15558F, що й акцент сайту (docs/research/DESIGN_SYSTEM.md §2.1).
BRAND = "#15558f"
MUTED_BORDER = "rgba(128,128,128,.45)"
MUTED_FILL = "rgba(128,128,128,.12)"
WARN = "#b45309"
OK = "#15803d"

# Скільки банерів цього розміщення сайт показує ОДНОЧАСНО (решта — перегортанням).
SLOT_CAPACITY: dict[str, int] = {
    "home_promo": 3,  # ряд праворуч від каталогу (frontend: promoGroups по 3)
    "home_side": 1,  # вузька колонка у відкритому каталозі
}

SLOT_LABELS: dict[str, str] = {
    "home_promo": "Біля каталогу",
    "home_side": "У відкритому каталозі",
}

SLOT_COLORS: dict[str, str] = {
    "Біля каталогу": "success",
    "У відкритому каталозі": "success",
    "Невідоме розміщення": "danger",
}

_HINTS: dict[str, str] = {
    "home_promo": (
        "Великий банер праворуч від каталогу — те, що видно одразу при вході. "
        "У ряд стає до ТРЬОХ штук: один розтягнеться на всю ширину, два стануть "
        "половинами, три — третинами. Четвертий і далі — перегортанням."
    ),
    "home_side": (
        "Вузька вертикальна смуга праворуч від підгруп — видно, поки каталог ВІДКРИТИЙ "
        "(навели на категорію або натиснули «Каталог»). Показується ОДИН — той, у кого "
        "менший «Порядок». Пропорція вертикальна, приблизно 1:1.5 (наприклад 600×900)."
    ),
}


def _img(url: str, focus_x: int, focus_y: int, zoom: int, *, radius: str = "4px") -> SafeString:
    """Картинка рівно з тим кадруванням, що застосує сайт."""
    scale = "" if zoom <= 100 else f"scale({zoom / 100});"
    return format_html(
        '<img src="{}" alt="" style="width:100%;height:100%;object-fit:cover;'
        'object-position:{}% {}%;{}border-radius:{}" />',
        url,
        focus_x,
        focus_y,
        mark_safe(scale),  # noqa: S308 — число, зібране тут же
        radius,
    )


def _slot_cell(*, label: str, current: bool, content: SafeString | str, flex: str = "1") -> str:
    """Одна комірка слота: або банер, або підпис-заглушка."""
    return format_html(
        '<div style="flex:{};min-width:0;height:130px;display:flex;align-items:center;'
        "justify-content:center;overflow:hidden;border-radius:6px;font-size:11px;"
        'text-align:center;border:{};background:{}">{}</div>',
        flex,
        f"2px solid {BRAND}" if current else f"1px dashed {MUTED_BORDER}",
        "rgba(21,85,143,.10)" if current else MUTED_FILL,
        content if content else format_html('<span style="opacity:.7">{}</span>', label),
    )


def _catalog_column() -> SafeString:
    rows = format_html_join(
        "",
        '<div style="height:7px;margin:4px 6px;border-radius:3px;background:{}"></div>',
        ((MUTED_BORDER,) for _ in range(5)),
    )
    return format_html(
        '<div style="width:84px;flex:none;height:130px;border:1px dashed {};border-radius:6px;'
        'background:{};padding:5px 0">'
        '<div style="font-size:9px;text-align:center;opacity:.7;margin-bottom:3px">Каталог</div>'
        "{}</div>",
        MUTED_BORDER,
        MUTED_FILL,
        rows,
    )


def real_slot_preview(banner, siblings) -> SafeString:
    """Справжній ряд слота з усіма банерами цього розміщення.

    `siblings` — живі банери того самого розміщення в порядку показу. Поточний
    (навіть якщо він ще не збережений або вимкнений) підсвічений синім.
    """
    placement = getattr(banner, "placement", "") or "home_promo"
    capacity = SLOT_CAPACITY.get(placement, 1)
    current_pk = getattr(banner, "pk", None)

    # Поточний банер може бути ще не збережений або вимкнений — показуємо його в ряду
    # все одно, інакше прев'ю не відповідало б на питання «а де буде МІЙ банер».
    shown = list(siblings)
    if current_pk is None or all(s.pk != current_pk for s in shown):
        shown.insert(0, banner)

    first_page = shown[:capacity]
    queued = len(shown) - len(first_page)

    def cell(item) -> str:
        url = ""
        image = getattr(item, "image", None)
        if image:
            try:
                url = image.url
            except ValueError:
                url = ""
        is_current = getattr(item, "pk", None) == current_pk or item is banner
        content = (
            _img(
                url,
                getattr(item, "focus_x", 50) or 50,
                getattr(item, "focus_y", 50) or 50,
                getattr(item, "zoom", 100) or 100,
            )
            if url
            else ""
        )
        return _slot_cell(label="без картинки", current=is_current, content=content)

    cells = format_html_join("", "{}", ((cell(item),) for item in first_page))

    # Порожні місця в ряду — щоб було видно, що слот ще не заповнений.
    empties = format_html_join(
        "",
        "{}",
        (
            (_slot_cell(label="вільно", current=False, content=""),)
            for _ in range(capacity - len(first_page))
        ),
    )

    right_zone = format_html(
        '<div style="display:flex;gap:6px;flex:1;min-width:0">{}{}</div>', cells, empties
    )
    # Для вузької реклами слот стоїть ПРАВОРУЧ ВІД ПІДГРУП, а не одразу за списком.
    middle = (
        format_html(
            "{}", _slot_cell(label="підгрупи категорії", current=False, content="", flex="2")
        )
        if placement == "home_side"
        else mark_safe("")
    )

    queue_note = (
        format_html(
            '<div style="margin-top:8px;font-size:12px;color:{}">'
            "У ряд одночасно вміщається {}. Ще {} — покажуться перегортанням "
            "(стрілки на сайті).</div>",
            WARN,
            capacity,
            queued,
        )
        if queued > 0
        else format_html(
            '<div style="margin-top:8px;font-size:12px;color:{}">'
            "Одночасно на сайті: {} з {} можливих у цьому місці.</div>",
            OK,
            len(first_page),
            capacity,
        )
    )

    return format_html(
        '<div style="max-width:760px">'
        '<div style="font-size:11px;font-weight:600;margin-bottom:5px;opacity:.75">{}</div>'
        '<div style="border:1px solid {};border-radius:8px;padding:8px;'
        'background:rgba(128,128,128,.04)">'
        '<div style="height:9px;border-radius:3px;background:{};margin-bottom:8px"></div>'
        '<div style="display:flex;gap:6px;align-items:stretch">{}{}{}</div>'
        "</div>{}</div>",
        "Як цей слот виглядає на сайті зараз (ваш банер — у синій рамці)",
        MUTED_BORDER,
        BRAND,
        _catalog_column(),
        middle,
        right_zone,
        queue_note,
    )


# Клік по картинці ставить кадр; повзунок — наближення. Обидва одразу пишуть у поля
# форми й перемальовують прев'ю, тож зберігати заради перегляду не треба.
_CROP_JS = """
<script>
(function () {
  function bind() {
    var stage = document.getElementById('banner-crop-stage');
    if (!stage) return;
    var img = stage.querySelector('img');
    var fx = document.querySelector('input[name="focus_x"]');
    var fy = document.querySelector('input[name="focus_y"]');
    var zoom = document.querySelector('input[name="zoom"]');
    var out = document.getElementById('banner-crop-readout');
    if (!img || !fx || !fy) return;

    function apply() {
      var x = Math.min(100, Math.max(0, parseInt(fx.value || '50', 10)));
      var y = Math.min(100, Math.max(0, parseInt(fy.value || '50', 10)));
      var z = zoom ? Math.min(300, Math.max(100, parseInt(zoom.value || '100', 10))) : 100;
      img.style.objectPosition = x + '% ' + y + '%';
      img.style.transform = z > 100 ? 'scale(' + (z / 100) + ')' : '';
      if (out) out.textContent = 'кадр ' + x + '% / ' + y + '%' + (z > 100 ? ', наближення ' + z + '%' : '');
      // Те саме кадрування — у справжньому ряду слота вище.
      document.querySelectorAll('[data-slot-current] img').forEach(function (el) {
        el.style.objectPosition = x + '% ' + y + '%';
        el.style.transform = z > 100 ? 'scale(' + (z / 100) + ')' : '';
      });
    }

    stage.addEventListener('click', function (e) {
      var r = img.getBoundingClientRect();
      fx.value = Math.round(((e.clientX - r.left) / r.width) * 100);
      fy.value = Math.round(((e.clientY - r.top) / r.height) * 100);
      apply();
    });
    [fx, fy, zoom].forEach(function (el) {
      if (el) el.addEventListener('input', apply);
    });
    apply();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
})();
</script>
"""


def crop_editor(banner) -> SafeString:
    """Велике фото, по якому кадр ставиться КЛІКОМ (а не дев'ятьма пресетами)."""
    image = getattr(banner, "image", None)
    url = ""
    if image:
        try:
            url = image.url
        except ValueError:
            url = ""

    if not url:
        return mark_safe(
            '<span style="opacity:.6">Спершу завантажте зображення на вкладці <b>uk</b>.</span>'
        )

    stage = format_html(
        '<div id="banner-crop-stage" title="Клікніть по фото — саме ця точка лишиться в кадрі" '
        'style="width:320px;height:200px;overflow:hidden;border-radius:8px;cursor:crosshair;'
        'border:2px solid {}">{}</div>',
        BRAND,
        _img(
            url,
            getattr(banner, "focus_x", 50) or 50,
            getattr(banner, "focus_y", 50) or 50,
            getattr(banner, "zoom", 100) or 100,
            radius="0",
        ),
    )

    return format_html(
        "{}"
        '<div id="banner-crop-readout" style="margin-top:6px;font-size:12px;opacity:.75"></div>'
        '<div style="margin-top:4px;font-size:12px;opacity:.7">'
        "Клікніть по фото — ця точка лишиться в кадрі. Поля нижче можна правити й руками."
        "</div>{}",
        stage,
        mark_safe(_CROP_JS),  # noqa: S308 — статичний літерал
    )


def live_preview(banner) -> SafeString:
    """Мініатюра «як цей банер виглядає на сайті» — для списку банерів."""
    image = getattr(banner, "image", None)
    if not image:
        return mark_safe('<span style="opacity:.5">— немає картинки</span>')

    narrow = banner.placement == "home_side"
    width, height = (60, 90) if narrow else (120, 80)

    return format_html(
        '<div style="width:{}px;height:{}px;border-radius:6px;overflow:hidden;border:1px solid {}">'
        "{}</div>",
        width,
        height,
        MUTED_BORDER,
        _img(image.url, banner.focus_x, banner.focus_y, banner.zoom, radius="0"),
    )


def phone_thumb(banner) -> SafeString:
    """Мініатюра «як на телефоні» для СПИСКУ банерів."""
    image = getattr(banner, "image_mobile", None) or getattr(banner, "image", None)
    if not image:
        return mark_safe('<span style="opacity:.4">—</span>')

    has_own = bool(getattr(banner, "image_mobile", None))
    return format_html(
        '<div style="display:flex;flex-direction:column;align-items:center;gap:3px">'
        '<div style="width:46px;height:80px;border:2px solid {};border-radius:8px;overflow:hidden">'
        "{}</div>"
        '<span style="font-size:9px;color:{}">{}</span></div>',
        MUTED_BORDER,
        _img(image.url, banner.focus_x, banner.focus_y, banner.zoom, radius="0"),
        OK if has_own else WARN,
        "своя" if has_own else "з десктопу",
    )


def mobile_preview(banner) -> SafeString:
    """Як банер ляже на ТЕЛЕФОНІ + чи є для нього окрема мобільна картинка."""
    image = getattr(banner, "image", None)
    mobile = getattr(banner, "image_mobile", None)

    if not image and not mobile:
        return mark_safe('<span style="opacity:.5">Спершу завантажте зображення.</span>')

    shown = mobile or image
    frame = format_html(
        '<div style="width:150px;height:250px;border:6px solid {};border-radius:14px;'
        'overflow:hidden;background:{}">'
        '<div style="height:16px;background:{}"></div>'
        '<div style="height:120px;overflow:hidden">{}</div>'
        '<div style="padding:6px">'
        '<div style="height:6px;margin:4px 0;border-radius:3px;background:{}"></div>'
        '<div style="height:6px;margin:4px 0;width:70%;border-radius:3px;background:{}"></div>'
        "</div></div>",
        MUTED_BORDER,
        MUTED_FILL,
        BRAND,
        _img(shown.url, banner.focus_x, banner.focus_y, banner.zoom, radius="0"),
        MUTED_BORDER,
        MUTED_BORDER,
    )

    if mobile:
        note = format_html(
            '<div style="margin-top:8px;font-size:12px;color:{}">✓ Окрема мобільна '
            "картинка завантажена — на телефоні показується саме вона.</div>",
            OK,
        )
    else:
        note = format_html(
            '<div style="margin-top:8px;padding:8px 10px;border-radius:6px;'
            'border-left:3px solid {};background:rgba(180,83,9,.10);font-size:12px">'
            "Окремої мобільної картинки немає — на телефоні обріжеться десктопна "
            "(видно вище). Якщо сюжет губиться, завантажте «Зображення (моб.)» "
            "вертикальним або посуньте кадр.</div>",
            WARN,
        )

    return format_html("<div>{}{}</div>", frame, note)


def placement_hint(placement: str) -> SafeString:
    hint = _HINTS.get(placement, "")
    if not hint:
        return mark_safe("")
    return format_html('<div style="margin-top:10px;font-size:12px;opacity:.8">{}</div>', hint)


def placement_badge(placement: str) -> str:
    return SLOT_LABELS.get(placement, "Невідоме розміщення")
