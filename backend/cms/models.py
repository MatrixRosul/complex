"""
Контент — моделі.

Зона відповідальності (ARCHITECTURE.md §1):
    Banner, NewsPost, StaticPage, MenuItem, PickupPoint

Джерела: DATA_MODEL.md §7, §2.3 (EditorImage), INPUTS.md §4.

i18n: усі текстові поля цих моделей — перекладні (`[tr]`), реєстрація — у `cms/translation.py`.
RU — В MVP, не «на майбутнє»: порожній `_ru` віддає `_uk` через MODELTRANSLATION_FALLBACK_LANGUAGES,
тому сторінка ніколи не порожня. Заповнює `_ru` не людина руками, а черга машинного перекладу
з ручним схваленням (`translation.TranslationEntry.approve()`).


⚠️ ЦІЛЬОВА БАЗА — PostgreSQL 14 (не 15/16). Міграції МУСЯТЬ застосуватись на PG14.

  1. UniqueConstraint(..., nulls_distinct=False) → PG15. Тут не використовується.

  2. GeneratedField → на PG14 лише STORED з IMMUTABLE-виразу. Тут обчислених полів немає:
     «банер зараз показується?» рахує queryset (`Banner.objects.live()`), а не колонка —
     вираз із `now()` не IMMUTABLE і в STORED-колонку не компілюється в принципі.

  3. MERGE (SQL) → PG15. Живемо на INSERT ... ON CONFLICT.

✅ ВИРІШЕНО (міграційний агент) — унікальність slug'ів по МОВНИХ колонках.
   DATA_MODEL §7 вимагає `UniqueConstraint(fields=["slug_uk"], name="uniq_news_slug_uk")`,
   а §0 забороняє згадувати перекладні колонки в `Meta` і радить окрему міграцію після
   `cms/0001_initial`. Порада §0 ХИБНА, і слідувати їй не можна:

     `Meta` — ЄДИНЕ джерело стану моделі для автодетектора. Обмеження, яке живе лише в
     міграції, він бачить як «у стані БД є, у моделі немає» і на наступному ж
     `makemigrations` ГЕНЕРУЄ МІГРАЦІЮ НА ЙОГО ВИДАЛЕННЯ, а `makemigrations --check` у CI
     падає назавжди. Той самий висновок незалежно отримав агент catalog
     (див. `uniq_cat_slug_in_parent` у Category.Meta) — відтворено на живій PG14.

   Обіцяної §0 фрагільності немає: у `Meta` лежить лише РЯДОК "slug_uk", який резолвиться
   в системних перевірках — тобто вже після `modeltranslation.ready()`. Тому constraint
   стоїть у `NewsPost.Meta` (нижче), і `makemigrations --check` чистий.

   `StaticPage` додаткового обмеження не потребує: ключ маршрутизації — `key`
   (не перекладний, unique у Meta).
"""

from __future__ import annotations

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import SEOMixin, TimeStampedModel


class Banner(TimeStampedModel):
    """Рекламні банери головної: великі біля каталогу і вузький у відкритому каталозі.

    ⚡ `image` і `image_mobile` — ПЕРЕКЛАДНІ: у банерах текст запечений у картинку, тому
    RU-версія слайдера — це інший файл, а не інший підпис. Мобільна версія — окремий файл,
    а не CSS-кроп: банер 1920×500, обрізаний до 375px, перетворюється на кашу.
    """

    class Placement(models.TextChoices):
        """⚠️ ТІЛЬКИ ТЕ, ЩО САЙТ СПРАВДІ ВИВОДИТЬ.

        Було чотири варіанти, два з яких брехали: «Слайдер головної» (слайдера не
        існувало — банер мовчки падав у промо-слот) і «Банер над категорією» (фронт це
        значення не читав узагалі, банер не показувався ніде). Замовник справедливо
        спитав, чому в списку є те, чого немає. Обидва прибрані міграцією 0005:
        слайдери переведені в промо, банери категорій деактивовані.

        Назви — за МІСЦЕМ на сторінці, а не за жанром: обидва слоти стоять праворуч від
        каталогу, і «промо-блок» проти «реклами» нічого не пояснювало.
        """

        HOME_PROMO = "home_promo", "Головна: банер біля каталогу (до 3 в ряд)"
        HOME_SIDE = "home_side", "Головна: вузька реклама у відкритому каталозі"

    placement = models.CharField(
        "Розміщення", max_length=16, choices=Placement.choices, db_index=True
    )

    title = models.CharField("Заголовок", max_length=200, blank=True)  # [tr]
    subtitle = models.CharField("Підзаголовок", max_length=300, blank=True)  # [tr]
    image = models.ImageField("Зображення", upload_to="banners/")  # [tr]
    image_mobile = models.ImageField("Зображення (моб.)", upload_to="banners/", blank=True)  # [tr]

    # ── Кадрування: ручне, у відсотках ───────────────────────────────────────
    # ⚠️ БУЛО 9 ПРЕСЕТІВ («по центру», «верх ліворуч» …) — замовник попросив рухати
    # кадр РУКАМИ. Відсотки дають будь-яку точку, а не дев'ять; у прев'ю адмінки їх
    # ставлять кліком по картинці, тож числа руками вводити не обов'язково.
    # Значення лягають прямо в CSS: object-position: {focus_x}% {focus_y}%.
    focus_x = models.PositiveSmallIntegerField(
        "Кадр по горизонталі, %",
        default=50,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="0 — лівий край, 100 — правий. Простіше: клікніть по картинці в схемі.",
    )
    focus_y = models.PositiveSmallIntegerField(
        "Кадр по вертикалі, %",
        default=50,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="0 — верх, 100 — низ.",
    )
    zoom = models.PositiveSmallIntegerField(
        "Масштаб, %",
        default=100,
        validators=[MinValueValidator(100), MaxValueValidator(300)],
        help_text="100 — вписати як є. Більше — наблизити (обріже сильніше).",
    )

    link_url = models.CharField("Посилання", max_length=500, blank=True)
    # Відносний шлях ("/uk/c/5609730/kholodylnyky") або абсолютний URL. Не URLField —
    # внутрішні посилання відносні, а URLField їх не пропустить.
    category = models.ForeignKey(
        "catalog.Category",
        verbose_name="Категорія",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        help_text="Не використовується: розміщення «над категорією» прибрано.",
    )
    # ⚠️ Поле лишене, але з форми ПРИБРАНЕ (див. cms/admin.py). Єдиний його споживач —
    # знесене розміщення CATEGORY_TOP. Видаляти колонку окремою міграцією зараз не
    # варто: у ній може лежати звʼязок, а користі від видалення нуль.

    sort_order = models.PositiveSmallIntegerField("Порядок", default=0)
    is_active = models.BooleanField("Активний", default=True, db_index=True)

    # --- період показу; NULL з будь-якого боку = без обмеження ---
    starts_at = models.DateTimeField("Показувати з", null=True, blank=True)
    ends_at = models.DateTimeField("Показувати до", null=True, blank=True)

    class Meta:
        verbose_name = "Банер"
        verbose_name_plural = "Банери"
        ordering = ("placement", "sort_order", "id")
        indexes = [
            models.Index(fields=["placement", "is_active", "sort_order"], name="banner_live_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(starts_at__isnull=True)
                | Q(ends_at__isnull=True)
                | Q(ends_at__gt=models.F("starts_at")),
                name="banner_period_sane",
            ),
        ]

    def __str__(self) -> str:
        return self.title or f"{self.get_placement_display()} #{self.pk}"

    @property
    def is_live(self) -> bool:
        """Чи показується банер ЗАРАЗ. Той самий предикат, що й у `Banner.objects.live()`."""
        now = timezone.now()
        return (
            self.is_active
            and (self.starts_at is None or self.starts_at <= now)
            and (self.ends_at is None or self.ends_at > now)
        )


class NewsPost(SEOMixin, TimeStampedModel):
    """Новини / блог."""

    title = models.CharField("Заголовок", max_length=250)  # [tr]
    slug = models.SlugField("Slug", max_length=280)  # [tr]
    # ⚠️ unique — по slug_uk, і саме в Meta (див. шапку файла: constraint лише в міграції
    #    автодетектор наступним кроком видаляє). UK — мова-джерело, роут спільний для обох мов.
    excerpt = models.TextField("Анонс", blank=True)  # [tr]
    body = models.TextField("Текст")  # [tr]
    # Rich HTML з редактора TipTap (unfold). Зображення в тексті вантажаться в EditorImage.
    # 🔴 Проганяти через bleach-allowlist ПЕРЕД збереженням (той самий санітайзер, що й для
    #    Product.description — core/services/sanitize.py): img[src] дозволено ТІЛЬКИ з нашого
    #    R2-домену, інакше mixed content і чужий трекінг на нашій сторінці.
    cover = models.ImageField("Обкладинка", upload_to="news/", blank=True)

    is_published = models.BooleanField("Опубліковано", default=False, db_index=True)
    published_at = models.DateTimeField("Дата публікації", null=True, blank=True, db_index=True)

    class Meta:
        verbose_name = "Новина"
        verbose_name_plural = "Новини"
        ordering = ("-published_at", "-id")
        indexes = [
            models.Index(fields=["is_published", "-published_at"], name="news_pub_idx"),
        ]
        constraints = [
            # DATA_MODEL §7. Роут новини — /news/<slug>, спільний для uk та ru, тому ключ —
            # slug_uk (мова-джерело). slug_ru лишається вільним: він не бере участі в URL.
            models.UniqueConstraint(fields=["slug_uk"], name="uniq_news_slug_uk"),
        ]

    def __str__(self) -> str:
        return self.title


class StaticPage(SEOMixin, TimeStampedModel):
    """Статичні сторінки: доставка/оплата, гарантія, повернення, про нас, контакти.

    ⚡ Маршрутизація — по `key` (НЕ перекладний, unique), а не по slug: інакше при перемиканні
    мови URL сторінки змінювався б і ламались би зовнішні посилання й індексація.
    Перекладаються тільки `title` / `body`.

    🔴 «Умови повернення коштів і доставки» + контакти ФОП — це не просто контент:
    їх ПЕРЕВІРЯЮТЬ при верифікації магазину в LiqPay (LIQPAY.md §2). Без них платежі
    не підключать.
    """

    class Key(models.TextChoices):
        PAYMENT_DELIVERY = "payment-delivery", "Оплата і доставка"
        WARRANTY = "warranty", "Гарантія"
        RETURN = "return", "Повернення та обмін"
        ABOUT = "about", "Про нас"
        CONTACTS = "contacts", "Контакти"
        BUYERS = "buyers", "Покупцям"
        CREDIT = "credit", "Оплата частинами"
        OFFER = "offer", "Публічна оферта"

    key = models.SlugField("Ключ", max_length=60, unique=True, choices=Key.choices)
    # choices — підказка, не догма: SlugField приймає і власний ключ (сторінок буде більше).

    title = models.CharField("Заголовок", max_length=250)  # [tr]
    body = models.TextField("Текст")  # [tr]  — той самий bleach-allowlist, що й у NewsPost

    is_published = models.BooleanField("Опубліковано", default=True)
    show_in_footer = models.BooleanField("Показувати в підвалі", default=True)
    show_in_menu = models.BooleanField("Показувати в меню", default=False)
    sort_order = models.PositiveSmallIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Статична сторінка"
        verbose_name_plural = "Статичні сторінки"
        ordering = ("sort_order", "id")

    def __str__(self) -> str:
        return self.title or self.key


class MenuItem(TimeStampedModel):
    """Пункт меню: верхнє / підвал / бургер.

    Розділи бургер-меню («Інформація», «Покупцям») — це пункти БЕЗ url з дочірніми
    елементами (`parent`), а не окрема модель: два рівні self-FK покривають усі референси.
    """

    class Zone(models.TextChoices):
        HEADER = "header", "Верхнє меню"
        FOOTER = "footer", "Підвал"
        MOBILE = "mobile", "Бургер-меню"

    zone = models.CharField("Зона", max_length=8, choices=Zone.choices, db_index=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children"
    )

    title = models.CharField("Назва", max_length=120)  # [tr]
    url = models.CharField("Посилання", max_length=300, blank=True)
    # blank: пункт-заголовок розділу ("Інформація") посилання не має.
    static_page = models.ForeignKey(
        StaticPage, null=True, blank=True, on_delete=models.CASCADE, related_name="menu_items"
    )
    # Заповнено → URL будується зі сторінки (`/{locale}/page/{key}`), а поле `url` ігнорується.
    # CASCADE: сторінку видалили → пункт меню веде в 404. Краще прибрати пункт.

    icon = models.CharField("Іконка (lucide)", max_length=40, blank=True)
    sort_order = models.PositiveSmallIntegerField("Порядок", default=0)
    is_active = models.BooleanField("Активний", default=True)

    class Meta:
        verbose_name = "Пункт меню"
        verbose_name_plural = "Меню"
        ordering = ("zone", "sort_order", "id")
        indexes = [
            models.Index(fields=["zone", "is_active", "sort_order"], name="menu_zone_idx"),
        ]

    def __str__(self) -> str:
        return f"[{self.get_zone_display()}] {self.title}"


class PickupPoint(TimeStampedModel):
    """Точка самовивозу (Ужгород). На неї посилається `orders.Order.pickup_point` (SET_NULL).

    Координати й графік ідуть ще й у фід Hotline: самовивіз = `<delivery type="pickup">`
    + `<store>` з координатами і робочими годинами (INTEGRATIONS §2.5).
    """

    name = models.CharField("Назва", max_length=160)  # [tr]
    address = models.CharField("Адреса", max_length=250)  # [tr]
    city = models.CharField("Місто", max_length=100, default="Ужгород")
    phone = models.CharField("Телефон", max_length=20, blank=True)
    working_hours = models.CharField("Графік роботи", max_length=200, blank=True)  # [tr]
    # "ПН–ПТ 09:00–20:00, СБ–НД 10:00–17:00" (INPUTS §4).

    latitude = models.DecimalField("Широта", max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(
        "Довгота", max_digits=9, decimal_places=6, null=True, blank=True
    )
    sort_order = models.PositiveSmallIntegerField("Порядок", default=0)
    is_active = models.BooleanField("Активна", default=True, db_index=True)

    class Meta:
        verbose_name = "Точка самовивозу"
        verbose_name_plural = "Точки самовивозу"
        ordering = ("sort_order", "id")

    def __str__(self) -> str:
        return f"{self.name} — {self.address}"


class EditorImage(TimeStampedModel):
    """Зображення, завантажене в rich-редактор (TipTap в unfold).

    ⚡ Потрібне, бо опис товару (`catalog.Product.description`) і тексти CMS — це rich HTML
    з ВБУДОВАНИМИ зображеннями (DATA_MODEL §2.3, INPUTS §2: робот-пилосос з фото між абзацами).
    🔴 Санітайзер дозволяє `img[src]` ТІЛЬКИ з нашого R2-домену — отже, картинка з редактора
    зобов'язана мати запис у нашому сховищі. Без цієї моделі редактор або вставляв би чужі
    URL (mixed content + трекінг), або base64 в TextField (роздування таблиці товарів).
    """

    file = models.ImageField("Файл", upload_to="editor/%Y/%m/")
    alt = models.CharField("Alt", max_length=255, blank=True)
    # НЕ перекладний: alt осідає всередині HTML-опису, а опис уже має версію на кожну мову.

    width = models.PositiveIntegerField(null=True, blank=True, editable=False)
    height = models.PositiveIntegerField(null=True, blank=True, editable=False)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        verbose_name = "Зображення редактора"
        verbose_name_plural = "Зображення редактора"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.file.name
