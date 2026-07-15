"""
Переклад — моделі.

Зона відповідальності (ARCHITECTURE.md §1):
    TranslationEntry (черга машинного перекладу UA→RU), GlossaryTerm,
    батчер Claude, черга схвалення в адмінці

Джерело правди: docs/research/TRANSLATION.md §1, §2, §5.4, §6.1, §8.2; DATA_MODEL.md §0.

╔══════════════════════════════════════════════════════════════════════════════════════╗
║ КЛЮЧОВА ІДЕЯ: ПЕРЕКЛАДАЄМО СЛОВНИК, А НЕ ТОВАРИ (TRANSLATION.md §2)                   ║
║                                                                                      ║
║ Наївний підхід «візьми товар і переклади його блок характеристик» шле рядок           ║
║ «Колір виробу: Чорний» у модель ОКРЕМО для кожного з 10 000 товарів. Наслідки:        ║
║   1. НЕКОНСИСТЕНТНІСТЬ. «Чорний» стане і «Черный», і «Чёрный»; «Сенсорне» — і         ║
║      «Сенсорное», і «Сенсорный». Це не гіпотеза, а базова властивість LLM.            ║
║      Фасетний фільтр після цього РОЗПАДАЄТЬСЯ: два різні рядки = два різні значення   ║
║      фасета = дубльовані чекбокси. Прямий баг у продакшні.                            ║
║   2. ВИТРАТИ ×140 (12,0 M токенів проти 0,086 M).                                     ║
║   3. Повторний реімпорт прайсу = повторна оплата всього.                              ║
║                                                                                      ║
║ Attribute / AttributeOption / Unit у нас ВЖЕ нормалізовані таблиці. Тому перекладаємо ║
║ ~4 300 унікальних рядків СЛОВНИКА один раз — і вони застосовуються до всіх товарів.   ║
║ «Чорний» фізично не може перекластись двома способами: він лежить в одному рядку БД.  ║
╚══════════════════════════════════════════════════════════════════════════════════════╝

РУЧНЕ СХВАЛЕННЯ — не опція, а конструкція:
    модель пише в `target_text` → редактор править `target_text` → і ТІЛЬКИ дія «Схвалити»
    переносить його в `published_text` і копіює в `*_ru`-колонку моделі (modeltranslation).
    Коли джерело змінилось (status=STALE) — сайт далі віддає старий, СХВАЛЕНИЙ `published_text`,
    а новий машинний варіант чекає в черзі. «На сайті раптом з'явився неперевірений текст»
    тут неможливий за побудовою.

Хто де джерело правди:
    TranslationEntry — джерело правди для ЧЕРГИ, аудиту й витрат;
    `*_ru`-колонка моделі — ВІТРИНА (її читає API/Ninja, нуль джойнів на рендері).

⚠️ ЦІЛЬОВА БАЗА — PostgreSQL 14 (не 15/16). Міграції МУСЯТЬ застосуватись на PG14.
  1. UniqueConstraint(..., nulls_distinct=False) → PG15. Тут не потрібен: ключ
     (content_type, object_id, field, target_lang) — усі колонки NOT NULL.
  2. GeneratedField → не використовуємо (source_hash рахує Python, див. compute_source_hash).
  3. MERGE (SQL) → PG15. Використовуємо INSERT ... ON CONFLICT.
  Функціональний unique-індекс по Lower("source_term") (GlossaryTerm) на PG14 працює:
  lower() — IMMUTABLE.
"""

import hashlib
import re
import unicodedata

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.db.models.functions import Lower
from django.utils import timezone

# ---------------------------------------------------------------------------
# Довідники
# ---------------------------------------------------------------------------


class TranslationKind(models.TextChoices):
    """
    Вид перекладу. Це НЕ косметика: kind визначає модель Claude, розмір батчу,
    набір валідаторів і політику схвалення (TRANSLATION.md §1, §5.3, §6.4).

    СЛОВНИК (перекладається ОДИН раз, ~4 900 одиниць, 100% ручне схвалення):
        attribute_name (~600) · attribute_value (~6 000) · unit (~25) · category_name (~300)
    ОБСЯГ (10 000+ одиниць):
        product_name · product_description · seo_* · page_html · news_html
    """

    ATTRIBUTE_NAME = "attribute_name", "Назва характеристики"
    ATTRIBUTE_VALUE = "attribute_value", "Значення характеристики"
    UNIT = "unit", "Одиниця виміру"
    CATEGORY_NAME = "category_name", "Назва категорії"
    PRODUCT_NAME = "product_name", "Назва товару"
    PRODUCT_SHORT_DESCRIPTION = "product_short_desc", "Короткий опис товару"
    PRODUCT_DESCRIPTION = "product_description", "Опис товару (HTML)"
    SEO_TITLE = "seo_title", "SEO: title"
    SEO_DESCRIPTION = "seo_description", "SEO: description"
    PAGE_HTML = "page_html", "Статична сторінка"
    NEWS_HTML = "news_html", "Новина"
    OTHER = "other", "Інше"


# Порядок черги схвалення (TRANSLATION.md §6.3): спершу СЛОВНИК.
# Схвалення 600 назв характеристик одразу «фіксує» термінологію в 10 000 товарів
# і в глосарії для описів — це найвищий ROI на годину роботи модератора.
KIND_REVIEW_PRIORITY: dict[str, int] = {
    TranslationKind.ATTRIBUTE_NAME: 10,
    TranslationKind.UNIT: 20,
    TranslationKind.ATTRIBUTE_VALUE: 30,
    TranslationKind.CATEGORY_NAME: 40,
    TranslationKind.PRODUCT_NAME: 50,
    TranslationKind.SEO_TITLE: 60,
    TranslationKind.SEO_DESCRIPTION: 61,
    TranslationKind.PRODUCT_SHORT_DESCRIPTION: 70,
    TranslationKind.PRODUCT_DESCRIPTION: 80,
    TranslationKind.PAGE_HTML: 90,
    TranslationKind.NEWS_HTML: 91,
    TranslationKind.OTHER: 99,
}


class TranslationStatus(models.TextChoices):
    PENDING = "pending", "Очікує перекладу"
    MACHINE = "machine", "Машинний, очікує схвалення"
    APPROVED = "approved", "Схвалено"
    REJECTED = "rejected", "Відхилено (на перепереклад)"
    STALE = "stale", "Джерело змінилось"
    FAILED = "failed", "Валідація не пройшла"
    DO_NOT_TRANSLATE = "skip", "Не перекладати"


def compute_source_hash(text: str) -> str:
    """
    Хеш джерела (TRANSLATION.md §5.4).

    NFC + згортання пробілів ДО хешування: косметична правка (подвійний пробіл, NBSP,
    інша форма композиції) НЕ повинна тригерити повторний платний переклад.
    95% полів при щоденному синку мають незмінний хеш — і коштують рівно $0.
    """
    norm = unicodedata.normalize("NFC", text or "")
    norm = re.sub(r"\s+", " ", norm).strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# TranslationEntry
# ---------------------------------------------------------------------------


class TranslationEntry(models.Model):
    """
    Одиниця черги перекладу: одне поле одного об'єкта на одну цільову мову.

    Ця ж таблиця є TRANSLATION MEMORY: запит по (kind, source_hash, target_lang) БЕЗ
    прив'язки до об'єкта знаходить уже схвалений переклад того самого тексту в іншого
    об'єкта → копіюємо, $0 витрат. Тому дублікати назв/значень між товарами
    перекладаються один раз (TRANSLATION.md §5.4).
    """

    # --- ЩО перекладаємо ---
    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, verbose_name="Тип об'єкта"
    )
    object_id = models.PositiveBigIntegerField("ID об'єкта")
    # ⚡ ВІДСТУП ВІД TRANSLATION.md §6.1 (там PositiveIntegerField).
    #   PositiveIntegerField — це int4, максимум 2 147 483 647. А DATA_MODEL §2.3 прямо
    #   каже: legacy-товари імпортуються з ЯВНИМ pk = старий 10-значний Prom-ID
    #   (до 9 999 999 999) — він НЕ ВЛІЗЕ в int4. Тому PositiveBigIntegerField.
    #   Дефолт застосунків і так BigAutoField (apps.py), тобто int4 тут — це просто помилка.
    target = GenericForeignKey("content_type", "object_id")
    field = models.CharField("Поле", max_length=64)  # "name" | "description" | "seo_title" ...

    kind = models.CharField("Вид", max_length=32, choices=TranslationKind.choices, db_index=True)
    source_lang = models.CharField("Мова джерела", max_length=5, default="uk")
    target_lang = models.CharField("Цільова мова", max_length=5, default="ru")

    # --- ДЖЕРЕЛО ---
    source_text = models.TextField("Текст джерела")
    source_hash = models.CharField("Хеш джерела", max_length=64, db_index=True)
    # ⚡ ВІДСТУП: hash НЕ unique (див. коментар до Meta.constraints).

    # --- ПЕРЕКЛАД ---
    target_text = models.TextField("Переклад (чернетка)", blank=True)
    # що видала модель / що правив редактор
    published_text = models.TextField("Опубліковано", blank=True)
    # ЩО БАЧИТЬ САЙТ. Пише ТІЛЬКИ approve(). Це і є «переклад не йде одразу в продакшн».
    published_at = models.DateTimeField("Опубліковано о", null=True, blank=True)

    status = models.CharField(
        "Статус",
        max_length=16,
        choices=TranslationStatus.choices,
        default=TranslationStatus.PENDING,
        db_index=True,
    )
    model_note = models.TextField("Примітка моделі", blank=True)
    # "note" від Claude: де саме вона сумнівалась. Найкращий сигнал модератору,
    # куди дивитись у першу чергу (в адмінці — значок ⚠ і окремий фільтр).
    validation_errors = models.JSONField("Помилки валідації", default=list, blank=True)
    # ["latin_tokens:s3", "tag_multiset"] — переклад з непорожнім списком НЕ потрапляє
    # навіть у чергу на схвалення: він іде у FAILED + ретрай на сильнішій моделі.

    # --- АУДИТ І ВИТРАТИ ---
    engine_model = models.CharField("Модель", max_length=40, blank=True)  # "claude-sonnet-5"
    # ⚡ У завданні поле називалось model_used; тут — engine_model (як у TRANSLATION.md §6.1).
    #   `model_*` — небезпечний префікс у Django-моделі (перетинається з Model API).
    prompt_version = models.CharField("Версія промпта", max_length=16, blank=True)
    glossary_version = models.CharField("Версія глосарія", max_length=16, blank=True)
    batch_id = models.CharField("Batch ID", max_length=64, blank=True, db_index=True)
    input_tokens = models.IntegerField("Токенів input", default=0)
    output_tokens = models.IntegerField("Токенів output", default=0)
    cost_usd = models.DecimalField("Вартість, $", max_digits=8, decimal_places=6, default=0)
    # 6 знаків після коми: один рядок словника коштує ~$0,00015. Округлення до копійок
    # перетворило б увесь місячний звіт на нулі.

    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="Редагував",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="Схвалив",
    )
    approved_at = models.DateTimeField("Схвалено о", null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        verbose_name = "Запис перекладу"
        verbose_name_plural = "Черга перекладу"
        ordering = ("-created_at", "-id")
        constraints = [
            # ОДИН запис на (об'єкт, поле, мова).
            # ⚡ ВІДСТУП ВІД ЗАВДАННЯ: там було «source_hash unique — кеш». Так не можна:
            #   два РІЗНІ об'єкти з однаковим текстом (напр. дві категорії «Аксесуари» або
            #   сотні товарів з тим самим коротким описом) дали б IntegrityError на другому,
            #   і половина каталогу просто не потрапила б у чергу. Кеш (Translation Memory)
            #   реалізується не через unique, а через ПОШУК по (kind, source_hash, target_lang)
            #   зі status=APPROVED — індекс tr_memory_idx нижче (TRANSLATION.md §5.4).
            models.UniqueConstraint(
                fields=["content_type", "object_id", "field", "target_lang"],
                name="uniq_translation_target",
            ),
        ]
        indexes = [
            models.Index(fields=["kind", "status"], name="tr_kind_status_idx"),
            # Translation Memory: «чи перекладали вже цей самий текст?»
            models.Index(fields=["kind", "source_hash", "target_lang"], name="tr_memory_idx"),
            # черга схвалення в адмінці + дашборд-віджет (pending / stale / failed)
            models.Index(fields=["status", "kind"], name="tr_queue_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.field}#{self.object_id} [{self.status}]"

    def save(self, *args, **kwargs):
        # Хеш — похідна від source_text і не має шансу розійтись із ним.
        if not self.source_hash or "source_text" in (kwargs.get("update_fields") or []):
            self.source_hash = compute_source_hash(self.source_text)
        super().save(*args, **kwargs)

    # -- дії -----------------------------------------------------------------

    def approve(self, user=None) -> None:
        """
        Схвалити й ОПУБЛІКУВАТИ: target_text → published_text → *_ru-колонка моделі.

        Єдиний шлях, яким машинний переклад потрапляє на сайт. Write-back у
        modeltranslation-колонку робить сервіс (translation/services/writeback.py),
        і робить його ПІСЛЯ коміту — щоб відкат транзакції не лишив вітрину з текстом,
        якого немає в черзі.
        """
        self.published_text = self.target_text
        self.published_at = timezone.now()
        self.approved_by = user
        self.approved_at = timezone.now()
        self.status = TranslationStatus.APPROVED
        self.save(
            update_fields=[
                "published_text",
                "published_at",
                "approved_by",
                "approved_at",
                "status",
                "updated_at",
            ]
        )

        # Лінивий імпорт: models.py не має залежати від сервісного шару на етапі імпорту.
        from translation.services.writeback import write_back_to_model

        transaction.on_commit(lambda: write_back_to_model(self.pk))

    def mark_stale(self, new_source: str) -> None:
        """
        Джерело змінилось. published_text НЕ ЧІПАЄМО — сайт далі показує старий,
        схвалений RU, поки новий машинний варіант чекає в черзі.
        """
        self.source_text = new_source
        self.source_hash = compute_source_hash(new_source)
        self.status = TranslationStatus.STALE
        self.save(update_fields=["source_text", "source_hash", "status", "updated_at"])


# ---------------------------------------------------------------------------
# GlossaryTerm
# ---------------------------------------------------------------------------


class GlossaryTerm(models.Model):
    """
    Термінологічний словник для system-блоку промпта (TRANSLATION.md §8.2).

    Навіщо окрема таблиця, якщо глосарій генерується зі схвалених TranslationEntry:
      * потрібні ТЕРМІНИ, яких немає в жодному полі БД («Вбудована техніка → Встраиваемая
        техника», категорійні кальки, стоп-переклади для брендів);
      * редактор має правити термінологію, не чіпаючи чергу;
      * глосарій — це те, що робить описи товарів узгодженими з характеристиками:
        схвалений термін потрапляє в промпт і стає обов'язковим до вживання.

    ⚠️ Блок кешується (`cache_control: ephemeral, ttl=1h`), а кеш Claude — ПРЕФІКСНИЙ:
    будь-який зміщений байт інвалідує все далі. Тому рендер глосарія ЗАВЖДИ
    детермінований: `.order_by("pk")`, `sort_keys=True`, жодних set()/datetime.now().
    Версія блоку (`glossary_version`) бампається раз на добу вночі, а НЕ при кожному
    схваленні терміна — інакше кеш не доживає до кінця батчу.
    """

    class Section(models.TextChoices):
        ATTRIBUTE = "attribute", "Характеристики"
        VALUE = "value", "Значення"
        UNIT = "unit", "Одиниці виміру"
        CATEGORY = "category", "Категорії"
        GENERAL = "general", "Загальне"

    section = models.CharField(
        "Розділ", max_length=12, choices=Section.choices, default=Section.GENERAL, db_index=True
    )
    source_lang = models.CharField("Мова джерела", max_length=5, default="uk")
    target_lang = models.CharField("Цільова мова", max_length=5, default="ru")

    source_term = models.CharField("Термін (uk)", max_length=200)
    target_term = models.CharField("Переклад (ru)", max_length=200)
    note = models.CharField("Примітка", max_length=300, blank=True)
    # "не плутати з «Тип нагріву»"; для DO-NOT-TRANSLATE: "бренд, лишати як є"

    is_active = models.BooleanField("Активний", default=True, db_index=True)
    sort_order = models.PositiveSmallIntegerField("Порядок", default=100)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        verbose_name = "Термін глосарія"
        verbose_name_plural = "Глосарій"
        ordering = ("section", "sort_order", "id")
        constraints = [
            # Функціональний unique по Lower(source_term): «Чорний» і «чорний» — це ОДИН
            # термін. Два різні переклади того самого слова в промпті = недетермінований
            # переклад = дубльовані чекбокси у фасеті.
            # PG14 це вміє: lower() — IMMUTABLE, функціональний unique-індекс легальний.
            models.UniqueConstraint(
                Lower("source_term"),
                "source_lang",
                "target_lang",
                name="uniq_glossary_term",
            ),
        ]
        indexes = [models.Index(fields=["is_active", "section"], name="gloss_active_idx")]

    def __str__(self) -> str:
        return f"{self.source_term} = {self.target_term}"
